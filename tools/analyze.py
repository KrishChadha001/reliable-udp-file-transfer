"""Aggregate measured runs and create portable SVG charts (no dependencies)."""

import argparse
import csv
from collections import defaultdict
import html
from pathlib import Path
import statistics


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["protocol"], row["condition"], row["file"])].append(row)
    output = []
    for (protocol, condition, file), group in sorted(groups.items()):
        good = [r for r in group if str(r["success"]).lower() in ("true", "1")]

        def mean(key):
            values = [
                float(r[key]) for r in good if r.get(key) not in ("", None, "null")
            ]
            return statistics.mean(values) if values else None

        durations = [float(r["elapsed_s"]) for r in good]
        output.append(
            dict(
                protocol=protocol,
                condition=condition,
                file=file,
                runs=len(group),
                successes=len(good),
                failures=len(group) - len(good),
                mean_elapsed_s=mean("elapsed_s"),
                stdev_elapsed_s=(
                    statistics.stdev(durations) if len(durations) > 1 else None
                ),
                mean_goodput_mbps=mean("goodput_mbps"),
                mean_retransmissions=mean("retransmissions"),
            )
        )
    return output


def svg_bars(rows, key, title, units, destination):
    width = 1100
    left = 340
    right = 100
    height = max(220, 100 + len(rows) * 34)
    values = [r[key] for r in rows if r[key] is not None]
    maximum = max(values, default=1) or 1
    plot = width - left - right
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0f172a"/>',
        '<g font-family="DejaVu Sans,Arial,sans-serif" fill="#e2e8f0">',
        f'<text x="24" y="30" font-size="19" font-weight="bold">{html.escape(title)}</text>',
        f'<text x="24" y="52" font-size="12">Measured successful-run means | {html.escape(units)} | failed runs remain in summary.csv</text>',
    ]
    for i, r in enumerate(rows):
        y = 80 + i * 34
        label = f"{r['protocol'].upper()} / {r['file']} / {r['condition']}"
        value = r[key]
        bar = 0 if value is None else max(1, value / maximum * plot)
        color = "#38bdf8" if r["protocol"] == "udp" else "#34d399"
        svg.append(
            f'<text x="18" y="{y+15}" font-size="10">{html.escape(label)}</text>'
        )
        svg.append(
            f'<rect x="{left}" y="{y}" width="{bar:.2f}" height="21" rx="3" fill="{color}"/>'
        )
        label_value = "N/A" if value is None else f"{value:.3f}"
        svg.append(
            f'<text x="{left+bar+8:.2f}" y="{y+15}" font-size="11">{label_value}</text>'
        )
    svg.append("</g></svg>")
    destination.write_text("\n".join(svg), encoding="utf-8")


def cwnd_svg(trace: Path, destination: Path):
    with trace.open(newline="", encoding="utf-8") as f:
        points = [
            (float(r["time_s"]), float(r["cwnd"]), r["event"])
            for r in csv.DictReader(f)
            if r["event"] in ("start", "ack", "timeout")
        ]
    if not points:
        return
    width = 1000
    height = 380
    pad = 60
    maxt = max(p[0] for p in points) or 1
    maxy = max(p[1] for p in points) or 1

    def xy(p):
        return pad + p[0] / maxt * (width - 2 * pad), height - pad - p[1] / maxy * (
            height - 2 * pad
        )

    poly = " ".join(f"{xy(p)[0]:.2f},{xy(p)[1]:.2f}" for p in points)
    marks = "".join(
        f'<circle cx="{xy(p)[0]:.2f}" cy="{xy(p)[1]:.2f}" r="3" fill="#fb7185"/>'
        for p in points
        if p[2] == "timeout"
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">
<rect width="100%" height="100%" fill="#0f172a"/>
<g fill="#e2e8f0" font-family="Arial,sans-serif" font-size="13">
<text x="60" y="24">Congestion window over time (red dots: timeout events)</text>
<text x="60" y="48">{html.escape(trace.name)}</text>
<text x="10" y="80">{maxy:.1f}</text><text x="12" y="320">0</text>
<text x="60" y="365">0 s</text><text x="880" y="365">{maxt:.3f} s</text>
<text x="5" y="55">packets</text></g>
<path d="M60 60V320H940" stroke="#64748b" fill="none"/>
<polyline points="{poly}" fill="none" stroke="#38bdf8" stroke-width="1.8"/>{marks}</svg>"""
    destination.write_text(svg, encoding="utf-8")


def analyze(runs: Path, output: Path):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with Path(runs).open(newline="", encoding="utf-8") as f:
        summary = aggregate(list(csv.DictReader(f)))
    if not summary:
        raise ValueError("No measured runs in input CSV")
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    svg_bars(
        summary,
        "mean_goodput_mbps",
        "UDP vs TCP: useful throughput",
        "Mbit/s",
        output / "throughput.svg",
    )
    svg_bars(
        summary,
        "mean_elapsed_s",
        "UDP vs TCP: completion time",
        "seconds",
        output / "completion-time.svg",
    )
    svg_bars(
        summary,
        "mean_retransmissions",
        "UDP DATA retransmissions (TCP: unavailable)",
        "packets",
        output / "retransmissions.svg",
    )
    for trace in sorted(Path(runs).parent.glob("*-trace.csv")):
        cwnd_svg(trace, output / (trace.stem + ".svg"))
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("runs")
    p.add_argument("--output", default="results/analysis")
    args = p.parse_args(argv)
    result = analyze(Path(args.runs), Path(args.output))
    print(f"Analyzed {len(result)} protocol/condition/file groups -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
