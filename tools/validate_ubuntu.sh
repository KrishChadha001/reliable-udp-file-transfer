#!/usr/bin/env bash
# Run inside your Ubuntu VM. Does not install packages or alter normal interfaces.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
[[ $(uname -s) == Linux ]] || { echo 'This validation script requires Linux.' >&2; exit 1; }
command -v python3 >/dev/null
command -v ip >/dev/null
command -v tc >/dev/null
run_dir="results/ubuntu-validation-$(date -u +%Y%m%dT%H%M%S)"
mkdir -p -- "$run_dir"
python3 --version | tee "$run_dir/python-version.txt"
uname -a | tee "$run_dir/kernel.txt"
python3 -m unittest discover -s tests -v 2>&1 | tee "$run_dir/tests.txt"
bash -n tools/netem.sh
python3 tools/benchmark.py --smoke --output "$run_dir/local-smoke"
sudo python3 tools/benchmark.py --netem --loss 5 --delay 25 --jitter 5 \
  --reorder 1 --rate 10mbit --repetitions 5 --sizes 1000000 \
  --output "$run_dir/netem-five"
printf '\nUbuntu validation complete. Inspect %s and update docs/REPORT.md with measured NetEm results.\n' "$run_dir"
