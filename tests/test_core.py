"""Contracts derived from the synopsis, with independent wire/test vectors."""

import struct
import unittest
import zlib
from rudp.protocol import Packet, Kind, ProtocolError, ReceiveWindow
from rudp.congestion import Rtt, Aimd
from rudp.common import validate_final, error_control, control, parse_control


class CodecTests(unittest.TestCase):
    def test_error_replies_stay_bounded_for_untrusted_ids_and_unicode(self):
        for request_id in ("x" * 1000, {"nested": "x" * 900}, "a" * 32):
            reply = parse_control(
                control(error_control(request_id, "\U0001f4c1" * 500))
            )
            self.assertLessEqual(len(control(reply)), 1024)
            self.assertEqual(
                reply["request"], "a" * 32 if request_id == "a" * 32 else None
            )
            self.assertTrue(reply["error"])
        for message in ("\x01" * 250, '\\"\n' * 250):
            self.assertLessEqual(len(control(error_control(None, message))), 1024)

    def test_header_and_roundtrip(self):
        p = Packet(Kind.DATA, 0x01020304, b"\x00\xff*")
        wire = p.encode()
        self.assertEqual(wire[:7], b"\x03\x01\x02\x03\x04\x00\x03")
        self.assertEqual(len(wire), 14)
        self.assertEqual(Packet.decode(wire), p)
        checksum = struct.unpack("!I", wire[7:11])[0]
        self.assertEqual(
            checksum, zlib.crc32(wire[:7] + b"\0" * 4 + wire[11:]) & 0xFFFFFFFF
        )

    def test_corruption_and_lengths(self):
        wire = Packet(Kind.DATA, 2, b"abc").encode()
        for bad in (
            wire[:-1],
            wire + b"x",
            wire[:11] + b"abd",
            b"",
            b"\x09" + wire[1:],
        ):
            with self.assertRaises(ProtocolError):
                Packet.decode(bad)
        with self.assertRaises(ProtocolError):
            Packet(Kind.DATA, 0, b"x" * 1025).encode()
        with self.assertRaises(ProtocolError):
            Packet(Kind.DATA, -1, b"").encode()


class WindowTests(unittest.TestCase):
    def test_reorder_duplicate_and_bound(self):
        r = ReceiveWindow(4, 4, 4096)
        self.assertEqual(r.accept(1, b"b" * 1024), (True, []))
        self.assertEqual(r.accept(1, b"b" * 1024), (True, []))
        self.assertEqual(r.accept(8, b"x" * 1024), (False, []))
        ok, chunks = r.accept(0, b"a" * 1024)
        self.assertTrue(ok)
        self.assertEqual(chunks, [b"a" * 1024, b"b" * 1024])
        self.assertEqual(r.base, 2)
        self.assertEqual(r.accept(0, b"a" * 1024), (True, []))
        self.assertEqual(len(r.buffer), 0)

    def test_invalid_final_packet_length(self):
        r = ReceiveWindow(3, 2, 1025)
        self.assertEqual(r.accept(1, b"xx"), (False, []))
        self.assertEqual(r.accept(1, b"z"), (True, []))


class CongestionTests(unittest.TestCase):
    def test_malformed_fin_statistics_are_rejected(self):
        valid = dict(
            id="a" * 32,
            bytes=4,
            packets=1,
            retransmissions=0,
            congestion_events=0,
            cwnd=4,
            srtt_ms=1,
            rto_ms=100,
        )
        validate_final(valid, "a" * 32, 4, 1)
        for key, value in [
            ("retransmissions", -1),
            ("cwnd", float("nan")),
            ("srtt_ms", "bad"),
            ("bytes", 5),
            ("id", "old"),
        ]:
            bad = {**valid, key: value}
            with self.assertRaises(ValueError):
                validate_final(bad, "a" * 32, 4, 1)

    def test_rtt_and_karn(self):
        r = Rtt()
        self.assertEqual(r.rto, 0.5)
        r.sample(0.1, False)
        self.assertAlmostEqual(r.srtt, 0.1)
        self.assertAlmostEqual(r.rto, 0.3)
        r.sample(3, True)
        self.assertAlmostEqual(r.srtt, 0.1)
        r.backoff()
        self.assertAlmostEqual(r.rto, 0.6)
        for _ in range(10):
            r.backoff()
        self.assertEqual(r.rto, 3)

    def test_aimd_and_recovery_boundary(self):
        a = Aimd(4, 64)
        a.ack()
        self.assertAlmostEqual(a.cwnd, 4.25)
        self.assertTrue(a.loss(2, 8))
        old = a.cwnd
        self.assertFalse(a.loss(5, 12))
        self.assertEqual(old, a.cwnd)
        self.assertTrue(a.loss(8, 14))
        self.assertGreaterEqual(a.limit, 1)
        for _ in range(10000):
            a.ack()
        self.assertLessEqual(a.cwnd, 64)

    def test_invalid_window(self):
        for value in (0, -1, float("nan"), float("inf"), 65):
            with self.assertRaises(ValueError):
                Aimd(value, 64)


if __name__ == "__main__":
    unittest.main()
