"""Loopback tests for the simplified push-transfer API."""

import hashlib
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
    """Reserve a UDP port number for a short-lived real-socket test."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TransferApiTests(unittest.TestCase):
    def transfer(self, content: bytes, settings: Settings):
        """Run sender and receiver in threads and return their statistics."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "input.bin"
            output = root / "received"
            source.write_bytes(content)
            result = {}

            def receive():
                result["receiver"] = receive_file(
                    free_port_number, str(output), settings
                )

            free_port_number = free_port()
            thread = threading.Thread(target=receive)
            thread.start()
            time.sleep(0.03)
            result["sender"] = send_file(
                str(source), "127.0.0.1", free_port_number, settings
            )
            thread.join(15)
            self.assertFalse(thread.is_alive())
            files = list(output.glob("*"))
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].read_bytes(), content)
            self.assertEqual(
                hashlib.sha256(files[0].read_bytes()).hexdigest(),
                hashlib.sha256(content).hexdigest(),
            )
            return result

    def test_empty_file_and_non_aligned_file(self):
        """Verify zero-byte and partial final chunks through real UDP sockets."""
        for content in (b"", b"x" * 1500):
            with self.subTest(size=len(content)):
                result = self.transfer(
                    content, Settings(window=1, timeouts=5, retries=50)
                )
                self.assertTrue(result["sender"].verified)
                self.assertTrue(result["receiver"].verified)

    def test_loss_duplication_corruption_and_reordering(self):
        """Verify CRC recovery and Selective Repeat under deterministic faults."""
        result = self.transfer(
            bytes(range(251)) * 40,
            Settings(
                window=4,
                loss=5,
                dup=5,
                corrupt=2,
                delay_ms=2,
                jitter_ms=5,
                seed=4,
                timeouts=10,
                retries=100,
            ),
        )
        self.assertTrue(result["sender"].verified)
        self.assertTrue(result["receiver"].verified)


if __name__ == "__main__":
    unittest.main()
