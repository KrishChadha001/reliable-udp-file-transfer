"""Real socket tests; proxy faults are deterministic and are NOT NetEm results."""

import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

RUN = [sys.executable, "-m", "rudp"]


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FaultProxy:
    def __init__(self, target, fin_ack_drops=1):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]
        self.target = ("127.0.0.1", target)
        self.client = None
        self.backend = self.target
        self.seen = set()
        self.stop = False
        self.held = None
        self.events = []
        self.fin_ack_drops = fin_ack_drops
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop:
            try:
                data, src = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                continue
            if self.client is None:
                self.client = src
            up = src == self.client
            typ = data[0] if data else 0
            seq = struct.unpack("!I", data[1:5])[0] if len(data) >= 11 else 0
            if not up:
                self.backend = src
            dst = (self.target if typ == 1 else self.backend) if up else self.client
            key = (up, typ, seq)
            first = key not in self.seen
            self.seen.add(key)
            final_ack = up and typ == 4 and seq == 0xFFFFFFFF and self.fin_ack_drops > 0
            if final_ack:
                self.fin_ack_drops -= 1
            drop = (
                final_ack
                or first
                and (
                    (typ == 2)
                    or (typ == 5)
                    or (typ == 4 and seq == 0xFFFFFFFE)
                    or (typ == 3 and seq % 11 == 3)
                    or (typ == 4 and seq % 13 == 5)
                )
            )
            if drop:
                self.events.append(("drop", key))
                continue
            if not up and typ == 3 and seq == 0 and first:
                damaged = bytearray(data)
                damaged[-1] ^= 1
                self.sock.sendto(damaged, dst)
                self.events.append(("corrupt", key))
                continue
            if not up and typ == 3 and seq == 1 and first:
                self.held = (data, dst, time.monotonic())
                self.events.append(("hold", key))
                continue
            self.sock.sendto(data, dst)
            if self.held and (typ == 3 or time.monotonic() - self.held[2] > 0.05):
                self.sock.sendto(self.held[0], self.held[1])
                self.held = None
            if not up and typ == 3 and seq == 2:
                self.sock.sendto(data, dst)
                self.events.append(("duplicate", key))

    def close(self):
        self.stop = True
        self.thread.join(1)
        self.sock.close()


class Transfers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.share = self.root / "share"
        self.share.mkdir()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
            p.communicate(timeout=5)
        self.tmp.cleanup()

    def server(self, proto="udp", once=True, **kw):
        pnum = port()
        cmd = [
            *RUN,
            "serve",
            "--protocol",
            proto,
            "--root",
            str(self.share),
            "--port",
            str(pnum),
            "--plain",
        ]
        if once:
            cmd.append("--once")
        for k, v in kw.items():
            cmd.extend(["--" + k, str(v)])
        p = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        self.procs.append(p)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            line = p.stdout.readline()
            if "LISTENING" in line:
                return p, pnum
            if p.poll() is not None:
                self.fail("Server startup failed: " + p.stderr.read())
        self.fail("No startup banner")

    def get(self, pnum, name="data.bin", out=None, proto="udp", extra=()):
        return subprocess.run(
            [
                *RUN,
                "get",
                "--protocol",
                proto,
                "--host",
                "127.0.0.1",
                "--port",
                str(pnum),
                "--file",
                name,
                "--output",
                str(out or self.root / "received.bin"),
                "--plain",
                *extra,
            ],
            capture_output=True,
            text=True,
            timeout=40,
        )

    def roundtrip(self, proto, payload, faults=False, fin_ack_drops=1):
        (self.share / "data.bin").write_bytes(payload)
        sm = self.root / "sender.json"
        cm = self.root / "client.json"
        trace = self.root / "trace.csv"
        srv, pnum = self.server(proto, metrics=sm, trace=trace)
        proxy = FaultProxy(pnum, fin_ack_drops) if faults else None
        try:
            result = self.get(
                proxy.port if proxy else pnum, proto=proto, extra=["--metrics", str(cm)]
            )
        finally:
            if proxy:
                proxy.close()
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        stdout, stderr = srv.communicate(timeout=10)
        self.assertEqual(srv.returncode, 0, stderr)
        self.assertEqual((self.root / "received.bin").read_bytes(), payload)
        client = json.loads(cm.read_text())
        sender = json.loads(sm.read_text())
        self.assertTrue(client["success"])
        self.assertEqual(client["sha256"], hashlib.sha256(payload).hexdigest())
        self.assertEqual(client["bytes"], len(payload))
        self.assertGreater(client["elapsed_s"], 0)
        if proto == "tcp":
            self.assertIsNone(client["retransmissions"])
        if faults:
            self.assertGreater(sender["retransmissions"], 0)
            self.assertGreater(client["corrupt_packets"], 0)
            self.assertTrue(proxy.events)
            self.assertIn("timeout", trace.read_text())
        self.assertFalse(list(self.root.glob("*.part.*")))

    def test_udp_binary(self):
        self.roundtrip("udp", bytes(range(256)) * 512)

    def test_tcp_binary(self):
        self.roundtrip("tcp", bytes(range(256)) * 512)

    def test_udp_empty(self):
        self.roundtrip("udp", b"")

    def test_tcp_empty(self):
        self.roundtrip("tcp", b"")

    def test_udp_loss_corruption_reorder_duplicates(self):
        self.roundtrip("udp", bytes(range(256)) * 128, True)

    def test_four_final_acks_lost_still_completes(self):
        self.roundtrip("udp", bytes(range(256)) * 128, True, 4)

    def test_tcp_completion_confirmation_not_in_timed_interval(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        pnum = listener.getsockname()[1]

        def framed_read(conn):
            header = conn.recv(4)
            n = struct.unpack("!I", header)[0]
            buf = b""
            while len(buf) < n:
                buf += conn.recv(n - len(buf))
            return json.loads(buf)

        def framed_send(conn, obj):
            data = json.dumps(obj).encode()
            conn.sendall(struct.pack("!I", len(data)) + data)

        def delayed_sender():
            with listener:
                conn, _ = listener.accept()
                with conn:
                    req = framed_read(conn)
                    framed_send(
                        conn,
                        dict(
                            version=1,
                            id="a" * 32,
                            request=req["id"],
                            file="data.bin",
                            size=3,
                            sha256=hashlib.sha256(b"abc").hexdigest(),
                            window=64,
                            chunk=1024,
                            control_timeout=0.3,
                            control_retries=30,
                        ),
                    )
                    conn.sendall(b"abc")
                    framed_read(conn)
                    time.sleep(0.8)
                    framed_send(conn, dict(id="a" * 32, success=True))

        thread = threading.Thread(target=delayed_sender, daemon=True)
        thread.start()
        metrics = self.root / "timing.json"
        r = self.get(pnum, proto="tcp", extra=["--metrics", str(metrics)])
        thread.join(2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(json.loads(metrics.read_text())["elapsed_s"], 0.5)

    def test_missing_file(self):
        _, pnum = self.server()
        r = self.get(pnum)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("file", r.stderr.lower())
        self.assertFalse((self.root / "received.bin").exists())

    def test_path_traversal(self):
        _, pnum = self.server()
        r = self.get(pnum, name="../secret")
        self.assertNotEqual(r.returncode, 0)

    def test_no_silent_overwrite(self):
        (self.share / "data.bin").write_bytes(b"new")
        dest = self.root / "received.bin"
        dest.write_bytes(b"old")
        _, pnum = self.server()
        r = self.get(pnum)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(dest.read_bytes(), b"old")

    def test_explicit_overwrite(self):
        (self.share / "data.bin").write_bytes(b"new")
        dest = self.root / "received.bin"
        dest.write_bytes(b"old")
        _, pnum = self.server()
        r = self.get(pnum, extra=["--force"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(dest.read_bytes(), b"new")

    def test_unreachable_peer_is_bounded(self):
        r = self.get(port(), extra=["--retries", "2", "--timeout", "1"])
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "received.bin").exists())

    def test_invalid_cli(self):
        for args in (
            ["--window", "0"],
            ["--cwnd", "nan"],
            ["--port", "70000"],
            ["--protocol", "bogus"],
            ["--typo", "yes"],
        ):
            r = subprocess.run(
                [*RUN, "get", "--file", "x", *args], capture_output=True, text=True
            )
            self.assertNotEqual(r.returncode, 0, args)

    def test_invalid_metrics_path_does_not_overwrite_output(self):
        dest = self.root / "received.bin"
        dest.write_bytes(b"keep this")
        r = self.get(port(), extra=["--metrics", str(dest)])
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(dest.read_bytes(), b"keep this")

    def test_shared_symlink_is_not_served(self):
        target = self.root / "secret"
        target.write_bytes(b"private")
        try:
            os.symlink(target, self.share / "data.bin")
        except OSError:
            self.skipTest("Symlink creation unavailable on this host")
        _, pnum = self.server()
        r = self.get(pnum)
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "received.bin").exists())

    def test_tcp_missing_file(self):
        _, pnum = self.server("tcp")
        r = self.get(pnum, proto="tcp")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "received.bin").exists())

    def test_sender_survives_oversized_invalid_request_id(self):
        from rudp.protocol import Packet, Kind

        (self.share / "data.bin").write_bytes(b"valid after invalid")
        for proto in ("udp", "tcp"):
            with self.subTest(protocol=proto):
                srv, pnum = self.server(proto, once=False)
                raw = json.dumps({"id": "x" * 1000}).encode()
                if proto == "udp":
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as bad:
                        bad.settimeout(2)
                        bad.sendto(
                            Packet(Kind.REQUEST, 0, raw).encode(), ("127.0.0.1", pnum)
                        )
                        bad.recvfrom(65535)
                else:
                    with socket.create_connection(
                        ("127.0.0.1", pnum), timeout=2
                    ) as bad:
                        bad.sendall(struct.pack("!I", len(raw)) + raw)
                        self.assertTrue(bad.recv(4096))
                result = self.get(
                    pnum, proto=proto, out=self.root / f"after-bad-{proto}.bin"
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(srv.poll(), "Invalid request terminated the sender")

    def test_multiple_files_on_same_sender(self):
        (self.share / "data.bin").write_bytes(bytes(range(256)) * 4)
        for proto in ("udp", "tcp"):
            with self.subTest(protocol=proto):
                srv, pnum = self.server(proto, once=False)
                for i in range(3):
                    dest = self.root / f"repeat-{proto}-{i}.bin"
                    result = self.get(pnum, proto=proto, out=dest)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        dest.read_bytes(), (self.share / "data.bin").read_bytes()
                    )
                self.assertIsNone(srv.poll())

    def test_inspect(self):
        f = self.root / "abc"
        f.write_bytes(b"abc")
        r = subprocess.run([*RUN, "inspect", str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(hashlib.sha256(b"abc").hexdigest(), r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
