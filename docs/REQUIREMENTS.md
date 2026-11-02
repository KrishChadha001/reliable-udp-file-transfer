# Synopsis-to-implementation checklist

The source synopsis is the supplied five-page CN project document. The final implementation uses Python as specified there; the temporary C++ proposal was superseded by the user's final instruction. Terminal UI replaces the later-proposed Qt interface for this phase.

| Synopsis section / objective | Where implemented / demonstrated | Evidence / remaining action |
|---|---|---|
| 1.1–1.2 Reliable application transport over UDP | `rudp/udp.py` | Real-socket binary and fault-injected transfers pass |
| 1.3 Single sender/receiver, one file at a time | sequential `serve`; `--once` | Explicit scope; no multi-client feature claimed |
| 2.1 Study transport principles | architecture, protocol and team/viva guides | Explanations and per-student walkthroughs |
| 2.2 Custom wire format | `rudp/protocol.py` | Independent header/CRC tests |
| 2.3 Individual ACKs and selective retransmission | `Flight`, sender ACK/expiry loops | Fault proxy drops individual DATA/ACKs; resulting file verified |
| 2.4 AIMD flight control | `rudp/congestion.py`, `send_file` | Growth/halving/recovery-boundary tests and actual trace support |
| 2.5 SHA-256 after every transfer | UDP/TCP receivers, `common.py` | Binary/empty-file hashes match; no unchecked publish path |
| 2.6 Controlled loss/delay/jitter/bandwidth | `tools/netem.sh`, benchmark flags/full matrix | Linux helper written; bash syntax checked; execute actual NetEm study on Ubuntu |
| 2.7 TCP comparison | `rudp/tcp.py`, `tools/benchmark.py`, analysis | Ten included local measured runs, five per protocol; Ubuntu conditions pending |
| 3.1 Receiver starts with request | REQUEST/META handshake | Request token, metadata ACK, retries |
| 3.2 Header 1+4+2+4 bytes; payload <=1024 | `HEADER = struct.Struct('!BIHI')` | CRC covers zeroed-checksum header+payload; length/corruption tests |
| 3.3 Per-packet timers and bounded receive window | `Flight`, `ReceiveWindow` | Reordering, duplicates, out-of-window and final-length checks |
| 3.3 Adaptive RTO / Karn | `Rtt` | SRTT/RTTVAR, lab clamps, timeout backoff, retransmitted samples excluded |
| 3.4 cwnd +1/cwnd, half on timeout once per flight | `Aimd` | Initial configurable cwnd, sequence recovery boundary, no slow start |
| 3.4 Log cwnd against time | `--trace`, `--diagnostic-traces`, SVG analysis | Actual sender event traces; diagnostic runs separate from primary timing |
| 3.5 Loss 0/1/5/10%; delay 0/25/50 ms | `--full` matrix | Implemented runner, not falsely represented as already measured |
| 3.5 With/without jitter/reorder, unlimited/10 Mbit/s | full condition profiles | Delay-dependent reordering profile; both traffic directions impaired |
| 3.5 Files 1/10/50 MB; >=5 repetitions | fixtures tool, full benchmark | Decimal MB, five-run default; smoke explicitly uses one repeat |
| 3.6 Tables/graphs, transfer time/goodput/retransmissions | raw JSON/CSV + `tools/analyze.py` | Summary, standard deviation, throughput/time/retry SVGs, failure counts |
| 3.6 Explain comparison limits | report and protocol docs | Matched timing/logging; TCP retransmissions N/A; Python/kernel differences |
| 3.7 Three-member ownership | `TEAM_AND_VIVA.md` | Original roles preserved; code tours, pseudocode, 30 answered viva questions |
| 4 Ubuntu/Python standard libraries, tools | README, demo, optional plotting helper | Python 3.10+, iproute2; optional Wireshark/tcpdump/matplotlib |
| 5 Working transfer + documented source | entire package | Runnable directly with `python3 -m rudp` |
| 5 Repeatable conditions and measured analysis | benchmark + included local data | Complete tools; finish Ubuntu matrix and update report with actual measurements |
| 5 Final report and demonstration | `REPORT.md`, `DEMO.md` | Implementation report and honest evaluation worksheet; no fabricated NetEm data |

## Deliberate teaching choices

RTO uses a 0.5 s initial value and 0.1..3 s lab clamps; these differ from normative RFC 6298 TCP requirements. Control payloads use bounded JSON and session IDs, extending the synopsis's conceptual packet format without changing its header. An ERROR kind and optional completion notice improve diagnostics/retry behavior. Linux namespace/veth emulation is one of the virtual-interface setups contemplated by the synopsis. Packet-level tracing is separated from primary timing to reduce measurement bias.

The project supplies everything needed to perform the planned study. A result under Ubuntu impairment is an empirical task and must be measured in that environment; it cannot be completed honestly by substituting made-up numbers or relabeling Windows results.
