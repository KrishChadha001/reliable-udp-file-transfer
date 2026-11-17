"""Plot benchmark CSV files; matplotlib is optional and isolated to this tool."""

import argparse
import csv
from pathlib import Path


def read_rows(path: Path):
    """Read measured rows while leaving aggregation decisions to the plotter."""
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def main(argv=None):
    """Create simple loss plots when matplotlib is installed."""
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", type=Path)
    parser.add_argument("--output", type=Path, default=Path("results/plot.png"))
    args = parser.parse_args(argv)
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit("Install matplotlib to use plot_results.py") from exc
    rows = read_rows(args.csv)
    losses = sorted({float(row.get("loss", 0)) for row in rows})
    elapsed = [
        sum(float(row["elapsed"]) for row in rows if float(row.get("loss", 0)) == loss)
        / max(1, sum(1 for row in rows if float(row.get("loss", 0)) == loss))
        for loss in losses
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    plt.plot(losses, elapsed, marker="o")
    plt.xlabel("Loss (%)")
    plt.ylabel("Elapsed seconds")
    plt.title("Reliable UDP transfer time")
    plt.savefig(args.output, bbox_inches="tight")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
