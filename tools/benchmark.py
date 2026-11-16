"""Measure the simplified UDP transfer over several loss levels and file sizes."""

import argparse
import csv
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rudp.models import Settings
from rudp.receiver import receive_file
from rudp.sender import send_file


def free_port() -> int:
    """Choose an unused local UDP port for one benchmark run."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_once(source: Path, output: Path, loss: float, seed: int):
    """Run one real sender/receiver pair and return measured statistics."""
    settings = Settings(
        window=32,
        loss=loss,
        seed=seed,
        retries=100,
        timeouts=30,
    )
    result = {}
    port = free_port()

    def receiver():
        """Run the receiver in a worker for this in-process measurement."""
        result["receiver"] = receive_file(port, str(output), settings)

    thread = threading.Thread(target=receiver)
    thread.start()
    time.sleep(0.02)
    started = time.monotonic()
    sender_stats = send_file(str(source), "127.0.0.1", port, settings)
    thread.join(35)
    receiver_stats = result.get("receiver")
    return sender_stats, receiver_stats, time.monotonic() - started


def main(argv=None):
    """Write one CSV row for every loss, size, and repeat combination."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--simple", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--losses", default="0,2,5,10")
    parser.add_argument("--sizes", default="1024,10000,100000")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("results/benchmark.csv"))
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fields = ["loss", "size", "repeat", "success", "elapsed", "retransmissions"]
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        source = root / "benchmark.bin"
        with args.output.open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fields)
            writer.writeheader()
            for size_text in args.sizes.split(","):
                size = int(size_text)
                source.write_bytes(bytes(index % 251 for index in range(size)))
                for loss_text in args.losses.split(","):
                    loss = float(loss_text)
                    for repeat in range(args.repeats):
                        sender_stats, receiver_stats, elapsed = run_once(
                            source, root / f"out-{size}-{loss}-{repeat}", loss, repeat
                        )
                        writer.writerow(
                            {
                                "loss": loss,
                                "size": size,
                                "repeat": repeat,
                                "success": bool(
                                    sender_stats.verified
                                    and receiver_stats
                                    and receiver_stats.verified
                                ),
                                "elapsed": elapsed,
                                "retransmissions": sender_stats.retransmissions,
                            }
                        )
                        csvfile.flush()
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
