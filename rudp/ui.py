"""Terminal-only presentation; transfer code does not depend on ANSI support."""

import csv
import os
import shutil
import sys
import time
from .common import Config, Metrics


class Progress:
    def __init__(self, cfg: Config, role: str):
        self.cfg = cfg
        self.role = role
        self.live = sys.stderr.isatty() and not cfg.plain
        self.color = self.live and not cfg.no_color and "NO_COLOR" not in os.environ
        self.started = time.monotonic()
        self.last = 0.0
        self.drawn = False
        self.writer = None
        self.file = None
        if cfg.trace:
            self.file = open(cfg.trace, "w", newline="", encoding="utf-8")
            self.writer = csv.writer(self.file)
            self.writer.writerow(
                [
                    "time_s",
                    "event",
                    "seq",
                    "cwnd",
                    "inflight",
                    "srtt_ms",
                    "rto_ms",
                    "retransmissions",
                ]
            )

    def banner(self, title: str):
        width = min(76, shutil.get_terminal_size((76, 24)).columns)
        print(("\033[1;36m" if self.color else "") + "=" * width)
        print("  RELIABLE UDP  /  TRANSPORT LAB")
        print("  " + title)
        print("=" * width + ("\033[0m" if self.color else ""), flush=True)

    def event(
        self,
        event: str,
        seq=0,
        cwnd=0.0,
        inflight=0,
        srtt=0.0,
        rto=0.0,
        retransmissions=0,
    ):
        if self.writer:
            self.writer.writerow(
                [
                    f"{time.monotonic()-self.started:.6f}",
                    event,
                    seq,
                    f"{cwnd:.5f}",
                    inflight,
                    f"{srtt*1000:.5f}",
                    f"{rto*1000:.5f}",
                    retransmissions,
                ]
            )
        if self.cfg.verbose:
            self.clear()
            print(
                f"  {event:12s} seq={seq:<8} cwnd={cwnd:6.2f} flight={inflight:<4} RTO={rto*1000:7.1f}ms",
                file=sys.stderr,
            )

    def update(self, done: int, total: int, metrics: Metrics):
        if not self.live or time.monotonic() - self.last < 0.12:
            return
        self.last = time.monotonic()
        elapsed = max(self.last - self.started, 0.001)
        ratio = done / total if total else 1.0
        width = shutil.get_terminal_size((76, 24)).columns
        bar_width = max(8, min(24, width - 50))
        bar = "#" * int(ratio * bar_width) + "-" * (bar_width - int(ratio * bar_width))
        line = f" [{bar}] {ratio*100:5.1f}%  {done/1048576:.2f}/{total/1048576:.2f} MiB  {done*8/elapsed/1e6:.2f} Mb/s"
        if width >= 100:
            line += (
                f"  retry={metrics.retransmissions or 0}  cwnd={metrics.cwnd or 0:.1f}"
            )
        sys.stderr.write("\r" + line[: max(1, width - 1)].ljust(max(1, width - 1)))
        sys.stderr.flush()
        self.drawn = True

    def clear(self):
        if self.drawn:
            sys.stderr.write(
                "\r"
                + " " * max(1, shutil.get_terminal_size((76, 24)).columns - 1)
                + "\r"
            )
            sys.stderr.flush()
            self.drawn = False

    def summary(self, m: Metrics):
        self.clear()
        status = "VERIFIED" if m.success else "FAILED"
        print(f"\n  {status}  |  {m.protocol.upper()}  |  {m.role}")
        rows = [
            ("File", m.filename),
            ("Bytes", str(m.bytes)),
            ("Elapsed", f"{m.elapsed_s:.4f} s"),
            ("Useful throughput", f"{m.goodput_mbps:.3f} Mbit/s"),
            (
                "DATA retransmissions",
                (
                    "N/A (kernel TCP)"
                    if m.retransmissions is None
                    else f"{m.retransmissions} ({m.retransmission_pct:.2f}%)"
                ),
            ),
        ]
        if m.cwnd is not None:
            rows += [
                ("Final cwnd", f"{m.cwnd:.2f} packets"),
                ("SRTT / RTO", f"{m.srtt_ms or 0:.2f} / {m.rto_ms or 0:.2f} ms"),
            ]
        rows += [
            ("Corrupt / duplicates", f"{m.corrupt_packets} / {m.duplicates}"),
            ("SHA-256", m.sha256 or "not verified"),
        ]
        if m.error:
            rows.append(("Error", m.error))
        for label, value in rows:
            print(f"  {label:23s} {value}")
        print(flush=True)

    def close(self):
        self.clear()
        if self.file:
            self.file.close()
