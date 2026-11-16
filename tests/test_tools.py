"""Tests for deterministic file generation and the in-process benchmark."""

import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.make_files import make_file


class ToolTests(unittest.TestCase):
    def test_file_generation_is_exact_and_reproducible(self):
        """Verify generated files have the requested bytes and stable content."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = root / "a.bin"
            second = root / "b.bin"
            make_file(first, 12345, 7)
            make_file(second, 12345, 7)
            self.assertEqual(first.stat().st_size, 12345)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_simple_benchmark_writes_measured_csv(self):
        """Verify benchmark rows come from real transfer runs."""
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "runs.csv"
            result = subprocess.run(
                [
                    sys.executable,
                    "tools/benchmark.py",
                    "--simple",
                    "--losses",
                    "0",
                    "--sizes",
                    "1024",
                    "--repeats",
                    "1",
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with output.open(newline="", encoding="utf-8") as source:
                rows = list(csv.DictReader(source))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["success"], "True")
            self.assertEqual(rows[0]["size"], "1024")


if __name__ == "__main__":
    unittest.main()
