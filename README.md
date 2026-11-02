# Reliable UDP Transport Lab

**Python • Ubuntu VM • Selective Repeat • AIMD • SHA-256 • TCP comparison**

Computer Networks Lab, 24BI15CS313, Odd Semester 2026, JIIT Sector 128.
Team: **Vaibhav Khandelwal (992401030121), Vedika Uthra (992401030210), Krish Chadha (992401030196), batch F2.** Submitted to Mr. Janardan Kumar Verma.

This project transfers actual files through UDP sockets. Reliability, ordering, duplicate handling, individual acknowledgements, per-packet retransmission timers and simplified congestion control are implemented in the application. It also includes a separate TCP baseline, an Ubuntu NetEm lab, automated experiments, CSV/JSON measurements and SVG graphs. The interface is a terminal dashboard, with an optional guided menu. Qt is deferred. The final language is Python, matching the synopsis.

## Start here on Ubuntu

Copy/extract this folder into your Ubuntu VM and open a terminal **inside this folder**. Ubuntu 22.04 or later with Python 3.10+ is recommended.

```bash
sudo apt update
sudo apt install python3 iproute2
python3 --version
python3 tools/fixtures.py
mkdir -p results
```

No third-party Python packages are required for transfers, tests, benchmarking or SVG graphs. `samples/` is generated locally, keeping the source download small. Fixtures use decimal MB: 1 MB = 1,000,000 bytes.

Optional PNG report plots use matplotlib: install `python3-matplotlib` on Ubuntu, then run `python3 tools/plot_matplotlib.py <analysis/summary.csv> --output results/plots`. The SVG graphs work without it.

In terminal 1:

```bash
python3 -m rudp serve --root samples --port 9000 \
  --trace results/sender-trace.csv --metrics results/sender.json
```

In terminal 2, in the same project folder:

```bash
python3 -m rudp get --host 127.0.0.1 --port 9000 \
  --file 1mb.bin --output received-1mb.bin --metrics results/receiver.json
python3 -m rudp inspect received-1mb.bin
sha256sum samples/1mb.bin received-1mb.bin
```

The destination is published only after exact-length and SHA-256 verification. Existing destinations are protected; choose a new output name or explicitly use `--force`. The output directory must already exist. Press Ctrl+C to stop a server. `--once` exits after one accepted request.

## Terminal interface

```text
============================================================================
  RELIABLE UDP  /  TRANSPORT LAB
  RECEIVER  |  UDP
============================================================================
 [############------------]  50.0%  0.48/0.95 MiB  ... Mb/s

  VERIFIED  |  UDP  |  receiver
  File                    1mb.bin
  Bytes                   1000000
  Elapsed                 <measured> s
  Useful throughput       <measured> Mbit/s
  DATA retransmissions    <measured count and percentage>
  Final cwnd              <measured> packets
  SRTT / RTO              <measured> / <measured> ms
  Corrupt / duplicates    <measured> / <measured>
  SHA-256                 <verified digest>
```

This is a format illustration, not experiment data. Interactive terminals get an in-place progress bar and optional color. `--verbose` displays packet events for teaching; it adds overhead and should be omitted during timed comparisons. `--plain` disables animation, `--no-color` disables colors, and the `NO_COLOR` environment variable is respected. Redirected output contains readable summaries without ANSI animation. Run `python3 -m rudp` in a terminal for the guided menu.

## TCP baseline

Use a second port or stop the UDP sender first:

```bash
# Terminal 1
python3 -m rudp serve --protocol tcp --root samples --port 9001
# Terminal 2
python3 -m rudp get --protocol tcp --port 9001 --file 1mb.bin --output received-tcp.bin
```

Both paths verify size/hash and measure request-to-verified-completion time. TCP DATA retransmissions are reported as **N/A**, because application-level writes do not reveal kernel retransmissions. A fair comparison uses identical files, endpoints, impairment conditions, window settings and logging settings. See [measurement definitions](docs/PROTOCOL.md).

## Automated comparisons and graphs

```bash
# Quick validation: 4 real transfers, two small sizes, one repetition.
python3 tools/benchmark.py --smoke --output results/smoke

# Local baseline: both protocols, 1 MB, five repetitions each.
python3 -m rudp benchmark --output results/local-five

# Recreate graphs from measured CSV.
python3 tools/analyze.py results/local-five/runs.csv --output results/local-five/analysis
```

Every output directory must be new: existing `runs.csv` files are not replaced. Each run retains sender/client logs and metrics. Primary timed runs disable packet-level traces for a fair comparison; add `--diagnostic-traces` for a separate cwnd dataset labeled diagnostic. Large received copies are deleted after verification; source fixtures remain. Analysis exports `summary.csv`, throughput/time/retransmission SVGs and, when traces exist, per-trace cwnd SVGs. Open an SVG in Firefox or attach it to a report. Failed runs remain in CSV and failure counts; averages use only successful runs. A single successful run has no sample standard deviation.

## Linux NetEm experiments

The helper creates two **isolated network namespaces** joined by a veth pair. It does not apply impairments to your VM's ordinary Wi-Fi, Ethernet or loopback interface. Both directions get the configured loss, delay, jitter, reordering and bandwidth limit. Thus ACK loss is included, and 25 ms one-way delay adds approximately 50 ms of network RTT.

```bash
# A controlled condition: five runs per protocol, same 1 MB file.
sudo python3 tools/benchmark.py --netem --loss 5 --delay 25 \
  --jitter 5 --reorder 1 --rate 10mbit \
  --repetitions 5 --sizes 1000000 --output results/netem-example

# Full planned parameter coverage: 1200 runs; allow substantial time and disk space.
sudo python3 tools/benchmark.py --netem --full --output results/full-study
```

The full matrix uses 0/1/5/10% loss, 0/25/50 ms one-way delay, unlimited/10 Mbit/s links, and jitter/reordering both off and on where delay is positive. It uses 1/10/50 MB files, UDP/TCP, and five repetitions. A 600-second per-run wall limit is the default; change `--run-timeout` explicitly if needed. All failures are reported rather than hidden. Running helpers with sudo can create root-owned result files; use a user-writable copy for report editing.

The runner records platform/Python versions, arguments, exact conditions, repetitions and qdisc snapshots. **Local proxy tests are fault-injection tests, not NetEm measurements.** Ubuntu-specific checks still need to be run in your VM; see [validation notes](docs/VALIDATION.md).

For manual NetEm, packet capture and cleanup, see [the demo guide](docs/DEMO.md). If a run is forcibly killed, inspect the lab and clean it with `sudo bash tools/netem.sh down`; this helper only removes its owned lab. No GUI is required inside the VM.

## Run the tests

```bash
python3 -m unittest discover -s tests -v
```

Tests use real localhost sockets and a deterministic UDP fault proxy. They exercise malformed/corrupt packets, receive-window boundaries, duplicates, RTT estimation, Karn's rule, AIMD recovery grouping, binary/empty files, dropped META/DATA/ACK/FIN packets, corruption/reordering, missing files, output protection, unavailable peers, CSV aggregation, and a real benchmark smoke run. Symlink tests may be skipped on Windows where symlink creation is restricted.

## Reading guide for three students

| Student | Main code to explain | Concepts |
|---|---|---|
| Vaibhav | `rudp/protocol.py`, sender half of `rudp/udp.py` | Header/CRC, sequence numbers, sender window, per-packet timers, selective retransmission |
| Vedika | `ReceiveWindow`, receiver half of `rudp/udp.py`, `rudp/congestion.py` | Buffering/ordering, duplicate ACKs, AIMD, RTT/RTO, trace logging |
| Krish | `rudp/tcp.py`, `tools/netem.sh`, `tools/benchmark.py`, `tools/analyze.py` | NetEm, reproducibility, TCP framing, comparison tables/graphs, interpretation |

Read [architecture](docs/ARCHITECTURE.md), [wire protocol](docs/PROTOCOL.md), [team walkthrough and viva](docs/TEAM_AND_VIVA.md), [demo](docs/DEMO.md), [report](docs/REPORT.md), and [synopsis coverage](docs/REQUIREMENTS.md). Everyone should understand the complete flow, even when presenting separate modules.

## Boundaries

This is a one-to-one teaching protocol. It has no encryption, authentication, NAT traversal, resume, simultaneous-client service, slow start, fast retransmit/recovery, or production hardening. CRC/SHA detect damage; they do not authenticate a malicious peer. IPv4 is supported. Only safe basename requests for non-symlink regular files are accepted. Transfer memory is bounded by the configured window, not the whole file. Ctrl+C removes incomplete temporary outputs; a hard kill/power failure may leave a `.part.*` file, which is never treated as a verified destination. The simplified RTO limits are chosen for lab responsiveness and do not claim complete RFC 6298 compliance.
