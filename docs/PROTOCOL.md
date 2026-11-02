# Protocol specification, version 1

## Datagram header

All multi-byte fields are unsigned, in network byte order. Python format: `!BIHI`.

| Offset | Field | Bytes | Meaning |
|---|---|---:|---|
| 0 | type | 1 | REQUEST=1, META=2, DATA=3, ACK=4, FIN=5, ERROR=6 |
| 1 | sequence | 4 | Zero-based DATA index, or reserved ACK purpose |
| 5 | payload length | 2 | Exactly the number of following bytes |
| 7 | checksum | 4 | CRC-32 of entire header+payload with this field zeroed |
| 11 | payload | 0..1024 | DATA bytes, a control JSON object, or ACK session ID |

Largest datagram payload at the socket is 1035 bytes. With ordinary IPv4/UDP headers that fits a 1500-byte Ethernet MTU; tunnels and different path MTUs can change that assumption. CRC covers header and data. A bad checksum, unknown type, impossible length or oversized/truncated datagram is discarded. Corruption counters are local receive observations.

### REQUEST

Type 1, seq 0. ASCII JSON: `{"version":1,"id":"<32 hex chars>","file":"1mb.bin","window":64}`. Only a safe basename is supported, not nested paths. Request IDs correlate metadata/errors with the caller. The client retransmits REQUEST at its control interval until META is accepted or the configured transmission count is exhausted.

### META

Type 2, seq 0. JSON fields: `version`, `id` (fresh session ID), `request` (original request ID), `file`, `size` (bytes), `sha256` (64 lowercase hex digits), `window`, `chunk` (=1024), `control_timeout` (sender's retry interval in seconds), and `control_retries` (sender's bounded transmission count). A new server socket/ephemeral endpoint isolates the transfer from the listening endpoint and most delayed earlier sessions. The receiver pins this endpoint after matching the request ID. ACK carries seq `0xfffffffe` and the 32 ASCII bytes of the session ID.

META is retransmitted using bounded stop-and-wait. The receiver re-ACKs repeated META while waiting for DATA. Source hashing is done before META; its cost is part of the client's request-to-completion measurement. A source that takes longer to hash than the client's metadata retry budget can require a larger `--control-timeout`/`--retries` setting.

### DATA and individual ACK

DATA seq starts at zero. Every full packet carries 1024 bytes; only the final DATA packet may be shorter. An empty file has zero DATA packets. Payloads are binary and may contain zero bytes. ACK type 4 carries the acknowledged DATA sequence and the session ID as ASCII payload. Duplicate ACKs do not increase cwnd or yield RTT samples.

Only the active sender window is retained. ACKed packets above a gap release payload storage but do not advance base across that gap. Receiver buffering and the fixed window cap bound memory. Files requiring `0xfffffffe` or more DATA packets are rejected so reserved ACK sequences and wraparound cannot collide.

### FIN and completion

After every DATA packet is ACKed, FIN type 5 carries JSON `id`, `bytes`, `packets`, `retransmissions`, `congestion_events`, `cwnd`, `srtt_ms`, and `rto_ms`. The receiver validates these types/ranges and matches the session, byte count and packet count. It verifies its accumulated SHA-256 against META, publishes the output and sends ACK seq `0xffffffff` with the session ID.

Sender repeats FIN at its configured control interval until ACKed or exhausted. Receiver's maximum post-completion linger is max(1 second, (control_retries+1)×control_timeout), covering the sender's remaining retry horizon with a grace interval. During it, duplicate FINs are ACKed again. After receiving FIN ACK, sender emits three optional notices: ACK type 4, sequence `0xffffffff`, payload `<session ID>:done`. Receipt of any valid notice ends receiver linger early; losing every notice only prolongs waiting and does not invalidate a verified file. Unlimited loss can still exhaust all sender retries while the receiver already has a valid file; no absolute distributed agreement is claimed.

### ERROR

Type 6 contains bounded JSON with `request` and `error`. Metadata-stage errors from the requested host with the matching request ID are surfaced immediately. Later failures can instead be detected by inactivity/retry exhaustion. There is no authentication, and session IDs are correlation/isolation mechanisms, not credentials. Avoid exposing the sender to untrusted networks.

## Timers and AIMD

| Setting | Default / rule |
|---|---|
| Maximum window | 64 packets, configurable 1..4096 |
| Initial cwnd | 4 packets, configurable 1..window |
| New-send limit | outstanding < floor(cwnd), next_seq < base+window |
| Additive increase | each first ACK: cwnd = min(window, cwnd+1/cwnd) |
| Multiplicative decrease | first timeout in recovery window: max(1,cwnd/2) |
| Initial data RTO | 0.5 s |
| First RTT sample R | SRTT=R, RTTVAR=R/2 |
| Later samples | RTTVAR=.75×RTTVAR+.25×abs(old SRTT−R); SRTT=.875×SRTT+.125×R |
| New RTO | clamp(SRTT+max(.001,4×RTTVAR), .1, 3.0) seconds |
| Karn's rule | no samples from retransmitted DATA |
| Timeout backoff | double RTO once per expiry batch, capped at 3 s |
| Control interval | 0.3 s, configurable .05..3 s |
| Transmission limit | 30 attempts per DATA/control, configurable 1..1000 |
| Inactivity timeout | 15 s without accepted DATA / a new DATA ACK |

Each DATA packet stores its expiry when sent. RTO updates affect new sends/retransmissions; outstanding packet deadlines are not retroactively rewritten. The .5-second initial RTO and .1..3-second lab bounds differ from RFC 6298's normative TCP settings. This is an RFC-inspired teaching estimator, not a claim of full RFC compliance. Recovery grouping is sequence-boundary based and loss detection is timeout-only.

## TCP baseline wire flow

TCP control objects are prefixed by a four-byte network-order length, capped at 1024 bytes. The request and metadata objects match the UDP control schemas. Metadata is followed by exactly `size` raw file bytes. Receiver then sends a verified-completion JSON with session ID, bytes, SHA-256 and success flag; sender returns a completion confirmation. `read_exact` handles partial TCP reads, and `sendall` handles partial writes. A TCP stream has no application-message boundaries unless the application frames them.

## Metric definitions

- Client elapsed time: from just before transfer setup/request until the output is verified/published and the receiver completion message is sent (FIN ACK for UDP, verified completion object for TCP). Includes network handshake, source hash pass, data transfer and destination verification. Excludes post-completion sender confirmation, UDP duplicate-FIN linger and screen-summary formatting. Success is still checked before the process exits.
- Sender elapsed time: from accepted request until final acknowledgement/confirmation. It is diagnostic and differs slightly from the client's interval. Comparison graphs use client metrics.
- Useful throughput (Mbit/s): verified file bytes × 8 / client elapsed seconds / 1,000,000. This is goodput, not total wire bandwidth.
- UDP retransmissions: repeated DATA sends only. REQUEST/META/FIN retries are trace events but excluded from this number.
- Retransmission percentage: 100 × repeated DATA sends / unique DATA packets. It can exceed 100% if packets are resent multiple times.
- TCP retransmissions: unavailable from this application and represented by JSON null/CSV blank, never an invented zero.
- Failure goodput: zero; summary averages include only successes and separately count failures. Duration for a failed attempt is still recorded where available.
- Trace fields: monotonic relative seconds, event, sequence, cwnd in packets, number in flight, SRTT/RTO in ms, cumulative retransmissions. Sender traces show actual AIMD behavior, rather than a synthetic sawtooth.

## Suggested reading

The synopsis references [RFC 768](https://www.rfc-editor.org/rfc/rfc768), [RFC 793](https://www.rfc-editor.org/rfc/rfc793), [RFC 1350](https://www.rfc-editor.org/rfc/rfc1350), [RFC 8085](https://www.rfc-editor.org/rfc/rfc8085), [RFC 5681](https://www.rfc-editor.org/rfc/rfc5681), [RFC 6298](https://www.rfc-editor.org/rfc/rfc6298), and Kurose & Ross, *Computer Networking: A Top-Down Approach*, 8th edition. See also the Linux [tc-netem manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html). These explain the source principles; this project's stated simplifications remain intentional.
