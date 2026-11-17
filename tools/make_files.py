"""Create deterministic binary files for repeatable transfer experiments."""

from pathlib import Path
import argparse


def make_file(path: Path, size: int, seed: int = 0) -> None:
    """Write a deterministic byte pattern without using random global state."""
    if size < 0:
        raise ValueError("size must not be negative")
    path.parent.mkdir(parents=True, exist_ok=True)
    block = bytes((seed + index) % 256 for index in range(256))
    with path.open("wb") as output:
        remaining = size
        while remaining:
            chunk = block[: min(remaining, len(block))]
            output.write(chunk)
            remaining -= len(chunk)


def main(argv=None):
    """Create one or more files from size arguments in bytes."""
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("size", type=int)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv)
    make_file(args.output, args.size, args.seed)
    print(f"created {args.output} ({args.size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
