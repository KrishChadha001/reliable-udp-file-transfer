import csv
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys
from tools.analyze import aggregate, analyze
from tools.fixtures import generate


class ToolTests(unittest.TestCase):
    def test_smoke_benchmark_produces_actual_udp_and_tcp_runs(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "runs"
            p = subprocess.run(
                [
                    sys.executable,
                    "tools/benchmark.py",
                    "--smoke",
                    "--repetitions",
                    "1",
                    "--output",
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=40,
            )
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            with (out / "runs.csv").open(newline="") as f:
                rows = list(csv.DictReader(f))
            self.assertEqual(len(rows), 4)
            self.assertEqual({r["protocol"] for r in rows}, {"udp", "tcp"})
            self.assertTrue(
                all(r["success"] == "true" and float(r["elapsed_s"]) > 0 for r in rows)
            )
            self.assertTrue((out / "environment.json").exists())
            self.assertFalse(
                list(out.glob("*-trace.csv")),
                "Primary timed runs must not enable UDP-only packet traces",
            )

    def test_fixture_generation_exact_size_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as d:
            a = Path(d) / "a"
            b = Path(d) / "b"
            generate(a, 12345)
            generate(b, 12345)
            self.assertEqual(a.stat().st_size, 12345)
            self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_averages_exclude_failures_and_keep_failure_count(self):
        rows = [
            dict(
                protocol="udp",
                condition="loss5",
                file="f",
                success="true",
                elapsed_s="2",
                goodput_mbps="4",
                retransmissions="3",
            ),
            dict(
                protocol="udp",
                condition="loss5",
                file="f",
                success="true",
                elapsed_s="4",
                goodput_mbps="2",
                retransmissions="5",
            ),
            dict(
                protocol="udp",
                condition="loss5",
                file="f",
                success="false",
                elapsed_s="99",
                goodput_mbps="0",
                retransmissions="",
            ),
        ]
        result = aggregate(rows)[0]
        self.assertEqual(result["successes"], 2)
        self.assertEqual(result["failures"], 1)
        self.assertEqual(result["mean_elapsed_s"], 3)
        self.assertEqual(result["mean_goodput_mbps"], 3)
        self.assertEqual(result["mean_retransmissions"], 4)

    def test_all_failed_and_tcp_retransmissions_are_not_invented(self):
        rows = [
            dict(
                protocol="tcp",
                condition="clean",
                file="f",
                success="false",
                elapsed_s="1",
                goodput_mbps="0",
                retransmissions="",
            )
        ]
        r = aggregate(rows)[0]
        self.assertIsNone(r["mean_elapsed_s"])
        self.assertIsNone(r["mean_retransmissions"])

    def test_analysis_exports_summary_and_real_svg(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runs = root / "runs.csv"
            with runs.open("w", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "protocol",
                        "condition",
                        "file",
                        "success",
                        "elapsed_s",
                        "goodput_mbps",
                        "retransmissions",
                    ],
                )
                w.writeheader()
                w.writerow(
                    dict(
                        protocol="udp",
                        condition="local",
                        file="f",
                        success="true",
                        elapsed_s=2,
                        goodput_mbps=4,
                        retransmissions=0,
                    )
                )
            analyze(runs, root / "analysis")
            self.assertTrue((root / "analysis" / "summary.csv").exists())
            self.assertIn("<svg", (root / "analysis" / "throughput.svg").read_text())


if __name__ == "__main__":
    unittest.main()
