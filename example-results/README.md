# Included measured examples

These files are real measurements from the development host (Windows 11, Python 3.13.9). They demonstrate functioning code and analysis; they are not a replacement for the Ubuntu VM/NetEm experiments.

- `local-five/`: ten real 1,000,000-byte transfers, five per protocol, no intentional network impairment, primary packet traces disabled. Includes the raw CSV, environment manifest and comparison SVGs. All ten succeeded. Detailed per-run logs/JSON and duplicate PNGs were removed from the source repository; the runner creates them again for new experiments.
- `fault-demo/`: one real UDP transfer through the deterministic test proxy. It drops/corrupts/reorders/duplicates traffic and drops the first four final ACKs. Both processes succeeded, with 7 DATA retransmissions, 1 grouped congestion event, 1 corrupt received packet and 4 duplicates observed. Includes fault events, raw sender cwnd CSV and an actual congestion-window SVG.

The fault demo is a correctness diagnostic, not a controlled NetEm throughput comparison. Its trace overhead and deliberate event pattern make it unsuitable for ranking protocols. The fault trace is generated using the same real-socket proxy exercised by the integration tests.

Source and received binary fixture copies are omitted from the repository to keep it small. Recreate fixtures with `tools/fixtures.py` or `tools/benchmark.py`; the scripts generate identical deterministic content for a given size.
