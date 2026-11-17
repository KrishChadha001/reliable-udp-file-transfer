"""Real loopback transfer tests for loss recovery and receiver behavior."""

from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from rudp.models import Settings
from rudp.receiver import receive_file
from rudp.sender import send_file


def free_port():
    """Choose an unused local UDP port for one test."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TransferTests(unittest.TestCase):
    def run_transfer(self, content, settings):
        """Run one sender and receiver and return both statistics and output."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.bin"
            output = root / "received"
            source.write_bytes(content)
            port = free_port()
            result = {}

            def receive():
                """Run the receiver on a real loopback socket."""
                result["receiver"] = receive_file(port, str(output), settings)

            thread = threading.Thread(target=receive)
            thread.start()
            time.sleep(0.03)
            result["sender"] = send_file(
                str(source), "127.0.0.1", port, settings
            )
            thread.join(20)
            self.assertFalse(thread.is_alive())
            files = list(output.glob("*"))
            received = files[0].read_bytes() if files else None
            return result, received

    def test_binary_file_at_multiple_window_sizes(self):
        """Verify byte identity with windows one, four, and thirty-two."""
        for window in (1, 4, 32):
            with self.subTest(window=window):
                result, received = self.run_transfer(
                    bytes(range(251)) * 20,
                    Settings(window=window, retries=100, timeouts=10),
                )
                self.assertTrue(result["sender"].verified)
                self.assertTrue(result["receiver"].verified)
                self.assertEqual(received, bytes(range(251)) * 20)

    def test_receiver_never_overwrites_existing_file(self):
        """Verify the receiver adds a suffix when the basename already exists."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / "received"
            output.mkdir()
            (output / "input.bin").write_bytes(b"old")
            source = root / "input.bin"
            source.write_bytes(b"new")
            port = free_port()
            result = {}

            def receive():
                """Receive one file into a directory containing a prior file."""
                result["receiver"] = receive_file(
                    port, str(output), Settings(timeouts=5, retries=30)
                )

            thread = threading.Thread(target=receive)
            thread.start()
            time.sleep(0.03)
            result["sender"] = send_file(
                str(source),
                "127.0.0.1",
                port,
                Settings(timeouts=5, retries=30),
            )
            thread.join(10)
            self.assertTrue(result["sender"].verified)
            self.assertTrue(result["receiver"].verified)
            self.assertEqual((output / "input.bin").read_bytes(), b"old")
            self.assertEqual((output / "input (1).bin").read_bytes(), b"new")

    def test_dead_receiver_fails_with_bounded_timeout(self):
        """Verify a sender returns a clear error when no receiver is listening."""
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.bin"
            source.write_bytes(b"data")
            stats = send_file(
                str(source),
                "127.0.0.1",
                free_port(),
                Settings(timeouts=0.5, retries=2, rto=0.1),
            )
            self.assertFalse(stats.verified)
            self.assertIn("META", stats.error)


if __name__ == "__main__":
    unittest.main()
