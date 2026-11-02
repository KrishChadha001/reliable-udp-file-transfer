# Architecture and code walkthrough

## What the application adds to UDP

UDP supplies datagrams and endpoint addressing. It does not make a file arrive completely, remove duplicates or reorder chunks for us. This project builds those services above UDP: an explicit request/metadata exchange, CRC-checked packets, sequence numbers, a bounded receive window, ACKs, retransmission timers, a congestion window and an end-to-end hash check.

The project is a real sender/receiver application. The NetEm lab and test proxy are outside the protocol. Disabling those helpers does not replace the transport with a simulation.

```text
                        reusable protocol core
     +-----------------------------------------------------------+
     | protocol.py       Packet codec + receive-window buffering  |
     | congestion.py     RTT estimator + AIMD controller          |
     | common.py         Safe files + control validation + metrics|
     +-----------------------------------------------------------+
             ^                       ^
     udp.py: Selective Repeat     tcp.py: framed TCP baseline
             ^                       ^
             +---------- cli.py -----+
                           |
                    ui.py: presentation

     tools/benchmark.py -> runs both transports -> raw CSV/JSON
     tools/netem.sh     -> impairs namespace link in both directions
     tools/analyze.py   -> successful-run tables and measured SVGs
```

The networking modules take a `Progress` object instead of formatting their own screen layouts. A future Qt UI can consume the same progress/event information without rewriting packet encoding or congestion control.

## Transfer lifecycle

1. Sender listens on a configured UDP port, accepting a filename request containing a fresh request ID and receive-window capacity.
2. Sender validates the basename, safely opens a regular file, hashes it and creates a fresh UDP transfer socket on an ephemeral port.
3. META gives the client its request ID, a new session ID, file size/hash, chunk size, negotiated window and control retry interval. Client checks all fields, pins that response endpoint and ACKs META.
4. Sender fills the permitted Selective Repeat window. Each packet retains its own payload, last-send time, retry count and expiry.
5. Receiver accepts correct in-window DATA, ACKs it individually, and flushes contiguous chunks to a temporary file. A gap prevents subsequent buffered chunks from being written early.
6. A valid first ACK removes that packet from the outstanding map, supplies an RTT sample only if unambiguous, increases cwnd and may slide the base. Expired packets alone are resent.
7. Once all DATA is ACKed, sender reliably transmits FIN. Receiver checks total bytes and SHA-256, publishes the complete output and ACKs FIN. It briefly remains available to re-ACK duplicate FINs.

### Sender state to recognize in code

`base`: lowest sequence not yet covered by contiguous ACKs. `next_seq`: next never-sent sequence. `outstanding`: packets still awaiting individual ACKs. `acknowledged`: ACKed sequences above a gap that cannot yet advance the base. `Flight`: one packet's stored payload, timer and attempt count.

Two constraints must both hold before sending new DATA:

```text
next_seq < base + negotiated_window
len(outstanding) < floor(cwnd)
```

The first prevents moving beyond the receiver's window when low sequences are missing. The second limits packets in flight according to congestion control. It would be incorrect to use cwnd alone: ACKs for later packets can free flight slots while the receiver is still blocked by an earlier gap.

### Receiver state to recognize in code

`ReceiveWindow.base` is the next sequence needed on disk. `buffer` maps future sequences to bytes. A packet below base is an old duplicate and can be ACKed again without writing. A packet at or above base+window is rejected. A correct packet inside the window is stored once; a loop removes base, base+1, and so on while contiguous data exists.

Memory is O(window × 1024), plus small ACK/window bookkeeping and temporary flush references. Large files are streamed rather than loaded entirely. Source hashing performs a preliminary linear pass, then the same open file is rewound for transfer; changing the source while it is served is unsupported and detected by final hash/length checks.

## Selective Repeat example

Suppose the sender transmits 0, 1, 2, 3 and packet 1 is lost. Receiver writes 0 and stores 2 and 3, ACKing each valid arrival. Sender removes 0, 2 and 3 from its outstanding map but keeps the base at 1. When 1's timer expires, only 1 is retransmitted. Receiver then writes 1, 2, 3 in order. This saves bandwidth compared with retransmitting every later packet as in Go-Back-N.

If ACK 2 is lost instead, DATA 2 eventually arrives again. Receiver already has or has written it, so it sends ACK 2 again and does not duplicate file content. Corrupt DATA is dropped without ACK, causing exactly the same timer-based recovery as loss.

## RTT and congestion control

`Rtt.sample` initializes SRTT to the first sample and RTTVAR to half that sample. Later samples update RTTVAR using the old SRTT, then update SRTT. RTO uses SRTT + 4×RTTVAR with a small clock-granularity floor and lab bounds. Retransmitted packets do not contribute samples (Karn's rule). Timeout batches exponentially back off RTO.

`Aimd.ack` adds 1/cwnd per first ACK. Roughly cwnd ACKs per RTT therefore add one packet per RTT. `Aimd.loss` halves cwnd, with a lower bound of one. It records the highest already-sent sequence as a recovery boundary: losses among that same flight do not each halve cwnd again. Shrinking cwnd stops new sends; already-sent packets remain tracked and may still be ACKed or retransmitted.

This intentionally omits slow start and fast retransmit/recovery. Loss is detected only by timeout. TCP's kernel implementation has a richer implementation and different overheads, so a result where TCP is faster is expected to be explainable, not a failure of the learning objective.

## Failure and file handling

Names may not contain directory separators, control characters or traversal names. The sender only opens regular files under its chosen share directory; on Ubuntu O_NOFOLLOW rejects final-component symlinks. The client creates a private uniquely named `.part.*` file alongside the requested destination. It does not expose that as a completed file. After verified completion it atomically publishes it, protecting an existing destination unless `--force` is explicit.

Retry counts, control intervals and inactivity timers are bounded. A dead peer fails rather than waiting forever. Ctrl+C unwinds file/socket context managers. A hard kill or machine failure cannot run cleanup, so a leftover `.part.*` may need removal. A final ACK can be lost beyond the finite linger interval; then the receiver may have a valid file while the sender reports uncertain completion. No finite UDP acknowledgement chain guarantees both peers know the other's final state under unlimited loss.

## Experiments are a separate layer

The experiment runner starts a fresh sender for each run, waits for its LISTENING message, launches the receiver, checks both exit statuses and saves raw records. The Linux helper connects sender and receiver namespaces through veth interfaces with symmetrical NetEm effects. The analysis tool groups by protocol, exact condition and file. Failed runs are retained with counts, while successful-run averages and sample standard deviation describe measured completions.

Primary timed runs disable per-packet traces for both protocols. Separate `--diagnostic-traces` runs collect UDP cwnd CSVs and are labeled diagnostic; their timing contains extra logging overhead. Runs alternate UDP then TCP deterministically; randomization and confidence intervals are optional extensions, not completed features.
