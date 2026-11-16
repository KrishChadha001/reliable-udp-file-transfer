"""Run one local five-percent-loss transfer using the real API."""

from pathlib import Path
import argparse
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.models import Settings
from rudp.receiver import receive_file
from rudp.sender import send_file


def free_port() -> int:
    """Choose an unused local UDP port for the demo receiver."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main(argv=None):
    """Start receiver and sender locally and print their measured summaries."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="samples/1mb.bin", type=Path)
    parser.add_argument("--out", default="results/demo-received", type=Path)
    args = parser.parse_args(argv)
    if not args.file.is_file():
        raise SystemExit(f"file not found: {args.file}")
    port = free_port()
    settings = Settings(
        window=32,
        loss=5,
        delay_ms=2,
        jitter_ms=5,
        seed=1,
        retries=100,
        timeouts=30,
    )
    result = {}

    def receiver():
        """Run the receiver in a worker so the sender can use the same process."""
        result["receiver"] = receive_file(port, str(args.out), settings)

    thread = threading.Thread(target=receiver)
    thread.start()
    time.sleep(0.05)
    result["sender"] = send_file(str(args.file), "127.0.0.1", port, settings)
    thread.join(settings.timeouts + 5)
    sender_stats = result["sender"]
    receiver_stats = result.get("receiver")
    print(f"sender verified={sender_stats.verified} error={sender_stats.error or 'none'}")
    print(
        "receiver verified="
        f"{getattr(receiver_stats, 'verified', False)} "
        f"error={getattr(receiver_stats, 'error', 'receiver did not finish') or 'none'}"
    )
    return 0 if sender_stats.verified and getattr(receiver_stats, "verified", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
