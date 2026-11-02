"""Generate deterministic binary fixtures without storing large files in source."""

import argparse
import hashlib
from pathlib import Path


def generate(path: Path, size: int):
    if size < 0:
        raise ValueError("Size must be nonnegative")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic 64 KiB block contains every possible byte, including zero.
    block = b"".join(
        hashlib.sha256(str(i).encode("ascii")).digest() for i in range(2048)
    )
    with path.open("wb") as out:
        left = size
        while left:
            piece = block[: min(left, len(block))]
            out.write(piece)
            left -= len(piece)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--directory", default="samples")
    p.add_argument(
        "--sizes", default="1,10,50", help="Comma-separated sizes in decimal MB"
    )
    args = p.parse_args(argv)
    for value in args.sizes.split(","):
        n = int(value)
        if not 1 <= n <= 1000:
            p.error("Sizes must be 1..1000 MB")
        path = Path(args.directory) / f"{n}mb.bin"
        generate(path, n * 1_000_000)
        print(f"Created {path} ({path.stat().st_size:,} bytes)")
    generate(Path(args.directory) / "empty.bin", 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
