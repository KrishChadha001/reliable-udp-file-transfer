# Validation record and remaining Ubuntu checks

## Verified on the development host

Environment: Windows 11, Python 3.13.9 (Anaconda), real IPv4 localhost UDP/TCP sockets. No WSL Linux distribution was available for running the Ubuntu namespace lab.

Commands executed:

```text
python -m unittest discover -s tests -v
python -m compileall -q rudp tools tests
bash -n tools/netem.sh
bash tools/netem.sh help
python tools/benchmark.py --repetitions 5 --sizes 1000000 --output example-results/local-five
python tools/plot_matplotlib.py example-results/local-five/analysis/summary.csv --output example-results/local-five/plots --title "Windows localhost: 1 MB, five runs per protocol"
```

Test suite: **33 tests discovered; 32 passed, 1 skipped** because this Windows session cannot create symlinks. The symlink test is present and should execute on Ubuntu. Both protocols passed binary and empty-file transfers. UDP passed real-socket fault injection including lost META/DATA/ACK/FIN, corruption, reordering, duplication and four consecutive lost final ACKs. Invalid output/metrics path validation preserves existing data. A TCP timing regression verifies that delayed final sender confirmation does not inflate the shared verification-time metric. Additional regressions exercise repeated transfers on one persistent sender and malformed oversized request IDs: both senders return bounded errors and remain available for the next valid transfer. Unicode and control-character error messages are also bounded by the wire limit.

An independent source review identified three important issues before delivery: final-ACK retry horizon, mismatched TCP timing boundary and UDP-only trace overhead in primary benchmarks. Each was reproduced by a failing behavioral test and fixed; the full suite then passed with the stated platform skip. No critical issue was reported.

The local benchmark completed ten measured runs (five UDP and five TCP), all successful. The fault demonstration completed with both sender and receiver successful and matching SHA-256. The generated PNG throughput graph was visually inspected for readable labels and layout. Bash parsing/help was checked with Git Bash; this is **not** a claim that Linux traffic control was exercised.

A continuation size study completed **30 additional real transfers**, all successful: 1/10/50 MB files, five repetitions per protocol and size. Compact raw CSV, manifest, summaries and SVGs are included under `example-results/size-study`. These are Windows localhost measurements with no NetEm, and do not replace the pending Ubuntu study. A second source review found no important issue in the malformed-request fix; its control-character coverage suggestion was added to the bounding regression.

## Run in the Ubuntu VM before final submission

```bash
sudo apt update
sudo apt install python3 iproute2 tcpdump
bash tools/validate_ubuntu.sh
```

The validation helper runs the test suite, local smoke comparisons and a five-repeat NetEm condition using the isolated lab. It records versions/logs under a new result directory. Inspect all results and update the report with actual Ubuntu measurements. For complete planned parameter coverage, run the full matrix separately:

```bash
sudo python3 tools/benchmark.py --netem --full --output results/full-study
```

Check that namespace creation/veth/NetEm are supported by the VM kernel, qdisc snapshots show intended settings, file hashes match, all failure counts are retained, packet capture confirms retransmission behavior, and cleanup removes only the lab-owned namespaces. If the full matrix is too large for the available time, report exactly the subset that was measured.

## Honest limits of this record

Ubuntu-specific kernel effects and Linux final-component symlink protection were not executed on this Windows host. Their code and instructions are supplied, but empirical results must come from the Ubuntu VM. No measurements in this package have been fabricated or labeled as a different environment. Tests verify meaningful correctness paths; they do not establish production security, universal reliability under unlimited loss, or performance superiority over TCP.
