# Three-student explanation and viva guide

This guide follows the work distribution in the synopsis. It is a study plan, not a claim that each student independently wrote particular generated files. Each member should run, modify and explain their part, and everyone should understand the full transfer lifecycle.

## Shared opening (about one minute)

“Our project implements reliable file transfer above UDP using application-level Selective Repeat and simplified AIMD. UDP itself does not guarantee delivery or order. We split a file into numbered chunks, check each packet with CRC-32, acknowledge packets individually, and retransmit only packets whose timers expire. The receiver buffers out-of-order chunks and writes them in order. SHA-256 verifies the entire resulting file. We test under Linux NetEm and compare measured performance with a separately framed TCP transfer.”

Show a successful terminal transfer, the matching hashes, a raw metrics file and one graph before going deep into code. Explain that TCP may be faster because its reliable transport and congestion algorithms run in an optimized kernel. The purpose is to implement and study transport principles.

## Vaibhav: packet format, sender and Selective Repeat

### Files to open

1. `rudp/protocol.py`: `HEADER`, `Kind`, `Packet.encode`, `Packet.decode`.
2. `rudp/udp.py`: `Flight`, `send_file`, the new-send loop, ACK processing and timeout loop.
3. `tests/test_core.py`: packet corruption and wire-layout tests.
4. `tests/test_integration.py`: the real UDP proxy fault test.

### Presentation sequence (five to seven minutes)

Draw the 11-byte header and explain why `!` in the struct format matters: sender and receiver agree on network byte order regardless of host architecture. The 1024-byte payload keeps the application datagram small enough for typical Ethernet without ordinary IPv4 fragmentation. Describe zeroing the checksum field before computing CRC so encoding and decoding cover identical bytes.

Draw a window of sequence numbers 0..7. Point to `base`, `next_seq`, `outstanding`, and `acknowledged`. Show the distinction between the fixed receive window and cwnd. A sender can have ACKs for later packets and still be unable to slide base over a missing earlier packet.

Walk through loss of DATA 1 from packets 0,1,2,3. Show that ACKs 0,2,3 remove those flight entries. Only DATA 1 remains timed and is resent. Explain why a per-packet `Flight` record is necessary. The sender retains its original payload, so a retransmission does not reread the wrong file offset.

End with reliability of control messages: REQUEST/META and FIN also need retry handling. A transfer that only retries DATA can hang before the first byte or falsely fail at completion. Mention the regression that drops four consecutive final ACKs.

### Sender pseudocode

```text
while base < total_packets:
    while next_seq is within receive window AND flight_count < floor(cwnd):
        read next chunk, send DATA, store payload/timer, increment next_seq
    receive an ACK with a short bounded wait
    if ACK belongs to an outstanding packet and current session:
        remove flight; update RTT if never retransmitted; increase cwnd
        record ACK; slide base across contiguous ACKs
    for each expired flight:
        group congestion event; back off timer
        resend that flight only; update its expiry and attempt count
```

### Viva questions and answers

1. **Why UDP when TCP already works?** To make reliability and congestion behavior visible in application code; it is an educational protocol, not a replacement for production TCP.
2. **Why not stop-and-wait?** Stop-and-wait allows one packet per RTT; a sliding window keeps multiple packets in flight and can use higher bandwidth-delay products.
3. **Selective Repeat versus Go-Back-N?** SR ACKs/buffers individual packets and resends only missing ones. Go-Back-N generally resends a suffix after a gap and has simpler receiver buffering.
4. **Can CRC detect a lost packet?** No. CRC detects corruption in received bytes. Missing ACKs and expired timers detect loss operationally.
5. **Why is payload length in the header?** To validate exact framing and represent the shorter last chunk; the receiver rejects extra/truncated data.
6. **What if an ACK arrives twice?** Only the first ACK for an outstanding packet changes state/cwnd. Later copies are ignored.
7. **Why keep ACKed sequence numbers above a gap?** To slide base correctly once the missing low ACK arrives, without falsely treating already-ACKed packets as outstanding.
8. **What prevents sequence wraparound?** File-size validation rejects files that would reach reserved ACK sequence values. This teaching protocol does not implement circular sequence arithmetic.
9. **What is a retransmission percentage of 150%?** There were 1.5 repeated DATA sends per unique packet on average; several packets may have needed more than one retransmission.
10. **What if the source changes during transfer?** A changed length/read may fail immediately; otherwise final SHA verification detects mismatch. Serve a stable file for experiments.

### Individual demonstration

Run UDP with a sender trace, then run a 5% loss NetEm condition. Point to the measured retransmission count and a timeout trace row. Use the small fault test to explain selective recovery without claiming the proxy is NetEm.

## Vedika: receiver, AIMD, adaptive timeout and logging

### Files to open

1. `rudp/protocol.py`: `ReceiveWindow.accept`.
2. `rudp/udp.py`: `get`, META handling, DATA acceptance, verification/FIN handling.
3. `rudp/congestion.py`: `Rtt` and `Aimd`.
4. `rudp/ui.py`: timestamped events and progress output.

### Presentation sequence (five to seven minutes)

Draw receiver base=1 with packets 2 and 3 buffered. Explain why writing 2 immediately would corrupt file order. `accept` returns only newly contiguous chunks. A duplicate below base is ACKed but not stored or written; a duplicate already buffered does not consume another slot. The allowed receive window bounds memory even if high sequences arrive first.

Show a buffer test and an end-to-end hash match. Explain the two integrity layers: CRC checks each datagram (including header), whereas SHA-256 checks the complete file. Neither authenticates an adversarial sender because there is no trusted secret or signature.

Then derive AIMD. At cwnd=4, each first ACK adds .25 approximately; over a full RTT, approximately four ACKs add about one packet. A timeout halves the window, down to at least one. Multiple losses within the recovery boundary count as one event, preventing one damaged flight from causing repeated halves. Show a real trace and describe its actual shape; do not draw a perfect sawtooth if the measured run had no timeouts.

Explain RTO. First sample R gives SRTT=R and RTTVAR=R/2; later samples use weighted smoothing. Uncertainty increases the safety margin. Karn's rule skips ambiguous samples from retransmissions, and timeout backoff avoids immediately repeating the same unsuccessful timing. The lab bounds differ from full TCP/RFC defaults.

### Receiver pseudocode

```text
request file; validate metadata; ACK metadata
create bounded receive window and temporary output
for each correct packet from the chosen transfer endpoint:
    if DATA is valid and inside window (or an old duplicate):
        buffer once; flush contiguous chunks; update SHA; ACK sequence
    if repeated META: ACK again
    if FIN after complete data:
        validate final metadata; compare SHA; publish file; ACK FIN
        answer duplicate FINs for sender's retry horizon
        exit early if sender's completion notice arrives
```

### Viva questions and answers

1. **Why ACK out-of-order packets?** They have arrived correctly and need not be resent; buffering preserves them until earlier gaps are filled.
2. **What if ACK loss creates duplicate DATA?** Receiver re-ACKs the sequence and discards the duplicate content, preserving the output exactly once.
3. **Window versus congestion window?** The fixed window protects receiver capacity/sequence position; cwnd adapts sender flight size to network feedback.
4. **Does halving cwnd remove in-flight packets?** No. They may already be on the wire. We stop new sends until outstanding count fits and keep tracking previous sends.
5. **Why use monotonic time?** Wall-clock corrections should not change timeout duration or experiment elapsed time.
6. **Why not use every ACK as an RTT sample?** For a retransmitted packet an ACK could correspond to either the original or resent copy, making the delay ambiguous.
7. **What is RTTVAR?** A smoothed estimate of timing variation; it contributes a safety margin to RTO.
8. **Why clamp RTO?** Very small estimates can cause spurious retransmissions; an upper bound keeps lab failures bounded. The bounds are simplifications and should not be presented as TCP compliance.
9. **Why does an empty file work?** META advertises zero bytes, there are no DATA packets, and FIN triggers verification of the SHA-256 of an empty byte string.
10. **Can the receiver succeed while the sender is uncertain?** Under enough lost completion traffic, yes. This is a distributed agreement limit. Negotiated retry horizon and optional success notice handle ordinary final-ACK loss without inventing absolute certainty.

### Individual demonstration

Run the receive-window unit test and point to `[a,b]` being flushed after reordering. Compare a clean UDP trace with a loss trace: identify an additive growth section, a timeout and a multiplicative decrease. Show `.part.*` cleanup after an interrupted or failed transfer.

## Krish: Ubuntu lab, TCP baseline and analysis

### Files to open

1. `tools/netem.sh`: namespaces, veth link, symmetrical qdiscs and cleanup ownership.
2. `rudp/tcp.py`: four-byte control framing, `read_exact`, `sendall` and hash verification.
3. `tools/benchmark.py`: conditions, repetitions, process management and raw CSV.
4. `tools/analyze.py`: grouping, successful-run means, failure counts and SVG generation.

### Presentation sequence (five to seven minutes)

Draw sender namespace 10.203.0.1 and receiver namespace 10.203.0.2 connected by veth. Explain why this protects the VM's normal interface. NetEm applies independently on each direction: both DATA and ACK traffic can be lost. A configured 25 ms one-way delay creates about 50 ms of extra RTT, plus processing/queueing.

Explain TCP framing before comparison: TCP is a byte stream, so it does not preserve an application's control-message boundaries. Four bytes describe JSON length, exact reads reconstruct it, then exactly the advertised number of file bytes follows. TCP reliability/reordering are provided by the kernel. Application send calls are not TCP retransmission counters, so that column is N/A.

Show the experiment manifest, five repetitions per selected condition and raw rows. Define useful throughput and the shared timing boundary. Explain why primary runs disable packet-level traces and diagnostic runs enable them separately. Both endpoints/protocols use the same virtual path and files.

Show failures as a separate count and successful-run averages/stddev. Explain that a mean without the number of failures can mislead. Interpret a graph in terms of RTT, cwnd, loss timeouts, Python overhead and TCP's richer implementation; do not claim that the custom implementation should beat TCP.

### Viva questions and answers

1. **What is NetEm?** A Linux traffic-control facility that introduces controlled link effects such as loss, delay, variation, reordering and rate limits.
2. **Why five runs?** Repetition reveals variation from scheduling, timing and random loss; one run is not reliable evidence for a trend.
3. **Why not compare UDP on loopback to TCP between VMs?** Different paths/hardware/RTTs confound the protocol comparison. Match conditions.
4. **Goodput versus throughput?** Our goodput counts verified file bytes only; wire traffic also contains headers, ACKs and retransmissions.
5. **Why does delay hurt small windows?** Flight size divided by RTT bounds how quickly new useful data can arrive, even on a high-rate link.
6. **Why not enter zero TCP retransmissions?** They are not measured by the application. A zero would falsely claim no retransmissions occurred.
7. **What does jitter change?** RTT variability can affect RTO and packet arrival order; small fixed timeouts can misidentify delayed packets as lost.
8. **Is a proxy loss test the same as NetEm?** No. The proxy deterministically checks correctness; NetEm supplies the controlled Linux conditions used for reported network experiments.
9. **Why record failures rather than delete them?** Omitting failures can make an unreliable condition look fast by showing only its lucky completions.
10. **Why avoid verbose/trace output in primary timing?** Formatting and disk logging add CPU/I/O overhead. We collect cwnd behavior separately as diagnostic evidence.

### Individual demonstration

Run a short five-repeat loss/delay condition, show qdisc counters and compare UDP/TCP summary rows. Open the throughput SVG and a diagnostic cwnd SVG. Explain why included Windows local measurements cannot stand in for the Ubuntu NetEm study.

## Shared final discussion

Be ready to identify limitations: single transfer, IPv4, no resume/authentication/encryption, finite retry windows, sequence limit, source-hash pass in elapsed time, fixed ordering of benchmark protocols and platform scheduling effects. Suggested future work includes advertised flow control, resume, authenticated sessions, better congestion algorithms, confidence intervals and a Qt interface. Clearly separate implemented behavior from future work.

Before presenting, each student should run the complete demo independently, explain one failure case, modify one meaningful parameter and predict how it changes a graph. Share screenshots and measured CSVs so the report and spoken claims agree.
