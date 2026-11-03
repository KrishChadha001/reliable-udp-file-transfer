# Project report draft and measurement worksheet

**Design and Implementation of a Reliable UDP-Based File Transfer Protocol with Selective Repeat and AIMD Congestion Control**

Computer Networks Lab (24BI15CS313), Odd Semester 2026, Department of Computer Science & Engineering, Jaypee Institute of Information Technology, Sector 128, Noida.

Vaibhav Khandelwal — 992401030121; Vedika Uthra — 992401030210; Krish Chadha — 992401030196; batch F2. Submitted to Mr. Janardan Kumar Verma.

This report contains completed implementation explanations and actual local verification results. The controlled Ubuntu NetEm evaluation section is a worksheet to fill from your VM's measured CSV, not a claim that those experiments have already been executed.

## Abstract

This project builds a reliable file-transfer application above UDP in Python. The application splits a file into numbered datagrams, checks each packet with CRC-32 and acknowledges packets individually. Selective Repeat uses a bounded receive buffer and per-packet timers to recover losses while preserving file order. An adaptive RTT estimator supplies retransmission timeouts, and a simplified AIMD congestion window controls new packets in flight. The receiver verifies final file size and SHA-256 before publishing the output. A framed TCP baseline and Linux NetEm experiment tools support repeatable comparison of completion time, useful throughput and UDP retransmission behavior. The implementation provides a terminal dashboard, raw metrics, CSV traces and generated comparison graphs.

## 1. Introduction and problem

UDP delivers datagrams without an application-level guarantee that every file chunk arrives, arrives once or arrives in order. A file-transfer application must therefore identify chunks, reject damage, recognize missing data, retry transmissions and reconstruct the original byte sequence. Sending at an uncontrolled rate can also overload a constrained path. TCP normally supplies these services inside the operating system, making their individual mechanisms less visible to students.

The problem is to implement those mechanisms above UDP while keeping the project small enough to inspect. The chosen scope is one sender, one receiver and one file at a time. The goal is correct and explainable behavior under loss/delay/reordering, followed by an honest measured comparison with TCP. It is not to prove that a simplified Python transport is faster than an optimized kernel implementation.

## 2. Objectives and implementation

| Objective | Implementation |
|---|---|
| Design packet framing | Fixed 11-byte network-order header with type, sequence, length and CRC-32 |
| Reliable delivery | Per-packet ACKs, retransmission deadlines and bounded retries |
| In-order reconstruction | Selective Repeat receive window and contiguous flush |
| Congestion adaptation | Per-first-ACK additive increase; timeout-group multiplicative decrease |
| Adaptive timing | SRTT/RTTVAR estimator, Karn's rule and timeout backoff |
| End-to-end correctness | Exact byte count plus SHA-256 comparison before atomic output publication |
| Controlled conditions | Linux namespace/veth NetEm helper with symmetric link effects |
| TCP comparison | Framed TCP stream using the same files and verification rules |
| Reproducibility | Per-run JSON/logs, raw CSV, environment manifest and analysis scripts |

## 3. System design

The receiver initiates a transfer using REQUEST. The sender validates the requested filename, opens a regular non-symlink file under its share directory, computes the source hash and sends META from a fresh transfer endpoint. META identifies the request/session and gives file size/hash, chunk size and receive-window capacity. After metadata acknowledgement, DATA packets are sent subject to both the fixed sliding-window boundary and the congestion window.

The sender maintains independent flight records for unacknowledged packets. An ACK removes its own packet without pretending that lower missing packets were received. Contiguous ACK information advances the base. Only expired flights are resent. The receiver ACKs accepted packets, buffers out-of-order data and writes only newly contiguous chunks. Duplicate DATA is re-ACKed but never written twice.

After all DATA is acknowledged, FIN carries completion metadata and final sender counters. The receiver validates size and hash and publishes the completed output, then acknowledges FIN. A negotiated retry horizon keeps final acknowledgements available after ordinary loss. An optional repeated sender success notice lets that final waiting period end early without making file correctness depend on the notice.

Refer to `PROTOCOL.md` for field offsets, message schemas, formulas, defaults and exact timing definitions, and `ARCHITECTURE.md` for the state-machine walkthrough.

## 4. Reliability and integrity

CRC-32 is checked for each datagram, including the custom header. A failed CRC causes silent rejection and later timer recovery. SHA-256 covers the completed byte stream and detects end-to-end mismatch even if an ordering/assembly bug passed packet checks. A temporary destination prevents partially received data appearing under the final filename. Existing destinations are protected unless the user explicitly requests replacement.

Controlled correctness tests exercise binary and empty files, corrupted/truncated datagrams, dropped metadata, dropped DATA/ACKs, repeated packets, reordering and lost FIN/final ACKs. Timeout and retry limits bound nonresponsive-peer behavior. These tests demonstrate failure handling; they do not replace measurements under Linux NetEm.

## 5. Congestion and timing

The congestion window begins at a configurable small value (default four packets). Every first ACK adds 1/cwnd, giving approximately one packet of growth per RTT. A timeout halves cwnd with a floor of one. A sequence-based recovery boundary prevents several losses within the same flight from being counted as separate congestion events. Slow start and fast retransmit/recovery are intentionally excluded, matching the synopsis's simplified scope.

The RTT estimator uses weighted smoothing of RTT and variation. It skips retransmitted packets to avoid ambiguous samples (Karn's rule). Timeout expiry backs off RTO. The chosen initial/clamped lab values differ from normative TCP RFC values; the implementation is RFC-inspired rather than a complete TCP timer implementation.

## 6. Experimental method

The planned Ubuntu environment has two network namespaces joined by veth. NetEm applies equal independent effects to both directions, so ACK traffic is impaired as well as DATA. The full supported matrix includes 0/1/5/10% loss, 0/25/50 ms one-way delay, stable versus jitter/reordering profiles, unlimited versus 10 Mbit/s rates, and 1/10/50 MB files. Zero-delay cases omit the positive-delay-dependent reordering profile. Five repetitions per condition/protocol/file give 1200 full-matrix runs.

Primary timing runs disable per-packet traces in both protocols. Separate diagnostic UDP runs record cwnd. Both protocols use the same source fixture and virtual path. Client elapsed time is request setup through verified output and sending the receiver completion message; post-completion confirmation/linger is excluded for both. Useful throughput is file bytes × 8 divided by elapsed seconds, expressed in decimal Mbit/s.

The runner records success/failure, size/hash, elapsed time, goodput, UDP retransmissions and condition. Summary means exclude failures while retaining failure counts. TCP retransmission values remain unavailable rather than being assumed zero. NetEm conditions, platform/tool versions and qdisc counters are retained with each dataset.

## 7. Actual local validation results

The included `example-results/local-five` dataset contains ten real transfers on the development machine: Windows localhost, Python 3.13, 1,000,000-byte file, no NetEm, five repetitions per protocol, packet traces disabled. All ten transfers succeeded. The environment manifest and raw CSV with hashes are included so these observations can be checked. Redundant detailed logs were removed from the repository; new benchmark runs retain them locally.

| Protocol | Successful / attempted | Mean elapsed (s) | Sample stddev (s) | Mean goodput (Mbit/s) | Mean UDP DATA retransmissions |
|---|---:|---:|---:|---:|---:|
| UDP | 5 / 5 | 0.083024 | 0.015739 | 99.940 | 0 |
| TCP | 5 / 5 | 0.024889 | 0.008579 | 350.282 | N/A |

These are measured local sanity-check results, **not Ubuntu VM/NetEm findings**, and they must not be relabeled as such. The mean of per-run goodputs is not generally equal to file bits divided by mean time; the script computes each run's goodput first and then averages. The values demonstrate working transfer/measurement tooling and host-specific overhead, not performance over a physical network.

## 8. Ubuntu evaluation worksheet — complete after running

An additional local size study covers all three planned file sizes. Like the initial baseline, it ran on Windows localhost without NetEm; its raw evidence is under `example-results/size-study`. All 30 transfers succeeded, with five repetitions per protocol and size.

| File size | Protocol | Successes / runs | Mean time (s) | Mean goodput (Mbit/s) |
|---|---|---:|---:|---:|
| 1 MB | UDP | 5 / 5 | 0.068770 | 126.556 |
| 1 MB | TCP | 5 / 5 | 0.020468 | 414.945 |
| 10 MB | UDP | 5 / 5 | 0.444299 | 181.166 |
| 10 MB | TCP | 5 / 5 | 0.060327 | 1390.906 |
| 50 MB | UDP | 5 / 5 | 3.018778 | 137.490 |
| 50 MB | TCP | 5 / 5 | 0.232288 | 1752.354 |

These goodputs describe the virtual localhost path and processing overhead, not a physical network link. There were zero observed UDP DATA retransmissions in this size-study dataset; loss behavior is demonstrated separately by the fault test and must be measured under NetEm in the VM.

Record Ubuntu version, kernel, VM CPU/RAM, Python version, interface topology, iproute2 version, requested/effective impairment, window/cwnd, fixture hash, repetitions and logging/capture policy. Copy actual values from `summary.csv`; include failure counts and dispersion.

| Condition | File | Protocol | Successes / runs | Mean time | Stddev | Mean goodput | UDP retransmissions |
|---|---|---|---|---|---|---|---|
| 0% loss, 0 ms, unlimited | 1 MB | UDP/TCP | from CSV | from CSV | from CSV | from CSV | from CSV / N/A |
| 1% loss, 25 ms, 10 Mbit/s | 1 MB | UDP/TCP | from CSV | from CSV | from CSV | from CSV | from CSV / N/A |
| 5% loss, 25 ms, jitter/reorder | 10 MB | UDP/TCP | from CSV | from CSV | from CSV | from CSV | from CSV / N/A |
| 10% loss, 50 ms, 10 Mbit/s | 50 MB | UDP/TCP | from CSV | from CSV | from CSV | from CSV | from CSV / N/A |

Use the actual full matrix where time permits; otherwise state exactly which conditions were run. An unfinished row is a measurement task, not permission to invent a plausible value.

For each plot, discuss the observation and its uncertainty: Did loss increase retransmissions and reduce cwnd? Did larger RTT reduce useful throughput for the limited window? Did jitter create timing variation? Did the rate cap become the bottleneck? Did TCP and the user-space implementation react differently? If a predicted trend is absent, report it and investigate rather than adjusting the numbers.

## 9. Limitations

Single client/file, IPv4, no authentication/encryption/resume/NAT traversal, no concurrent service, no full TCP congestion machinery, finite retry/completion windows, and no optimization claim. The RTO floor/cap and AIMD recovery grouping are simplified. Hashing is included in elapsed time, diagnostic tracing adds overhead, benchmark protocol order is deterministic and the analysis is descriptive rather than a full statistical study. TCP's actual kernel algorithm is environment-specific; record the VM's configuration before discussing it.

## 10. Conclusion and future work

The implementation makes transport-layer reliability visible: packet checks, selective retries, bounded buffering, adaptive timing, congestion response and complete-file verification operate together above UDP. The local tests and measured localhost runs validate the application paths. Completing the Ubuntu NetEm matrix supplies the evidence needed for conclusions about behavior under controlled network impairment.

Future extensions include resume, authenticated sessions, negotiated flow control, richer congestion algorithms, randomized experiment ordering, confidence intervals, and a Qt interface using the existing networking core. These are proposals, not completed project features.

## References

Use the synopsis bibliography and the official RFC links in `PROTOCOL.md`. Retain RFC 768 (UDP), RFC 793 (historical TCP reference from synopsis), RFC 1350 (TFTP), RFC 8085 (UDP usage), RFC 5681 (TCP congestion control), RFC 6298 (RTO), Kurose & Ross, and Linux NetEm documentation. Distinguish standards from this project's explicitly stated simplifications.
