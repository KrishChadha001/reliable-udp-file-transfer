"""Contracts derived from the synopsis, with independent wire/test vectors."""

import struct
import socket
import unittest
import zlib
from rudp.channel import LossyChannel
from rudp.protocol import Packet, Kind, ProtocolError, ReceiveWindow
from rudp.congestion import Rtt, Aimd
from rudp.models import Event, Settings, Stats


class CodecTests(unittest.TestCase):
    def test_transfer_models_have_simple_defaults(self):
        settings = Settings()
        stats = Stats()
        event = Event("SEND", 3, "data")
        self.assertEqual(settings.window, 32)
        self.assertEqual(stats.rto_ms, 500.0)
        self.assertEqual((event.kind, event.seq, event.info), ("SEND", 3, "data"))

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
        self.assertEqual(r.rto, 2)

    def test_aimd_and_recovery_boundary(self):
        a = Aimd(4, 64)
        a.ack()
        self.assertAlmostEqual(a.cwnd, 5)
        self.assertTrue(a.loss(2, 8))
        self.assertEqual(a.ssthresh, 2.5)
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

    def test_small_windows_are_valid(self):
        for window in (1, 2, 3):
            aimd = Aimd(1, window)
            aimd.ack()
            self.assertLessEqual(aimd.cwnd, window)
            self.assertTrue(aimd.loss(0, 1))
            self.assertLessEqual(aimd.cwnd, window)


class ChannelTests(unittest.TestCase):
    def test_corruption_reaches_receiver_and_fails_crc(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        channel = LossyChannel(sender, corrupt=100, seed=1)
        try:
            channel.sendto(Packet(Kind.DATA, 0, b"payload").encode(), receiver.getsockname())
            wire, _ = receiver.recvfrom(2048)
            with self.assertRaises(ProtocolError):
                Packet.decode(wire)
        finally:
            channel.close()
            receiver.close()

    def test_close_cancels_delayed_sends(self):
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(0.2)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        channel = LossyChannel(sender, delay_ms=100, seed=1)
        channel.sendto(b"not delivered", receiver.getsockname())
        channel.close()
        with self.assertRaises(socket.timeout):
            receiver.recvfrom(2048)
        receiver.close()


if __name__ == "__main__":
    unittest.main()
