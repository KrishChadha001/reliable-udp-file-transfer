# Simplified protocol

## Packet format

All integers use network byte order. The header is `!BIHI` and is 11 bytes.

| Field | Bytes | Meaning |
|---|---:|---|
| type | 1 | META, META_ACK, DATA, ACK, FIN, or FIN_ACK |
| sequence | 4 | DATA sequence number; zero for control packets |
| payload length | 2 | Number of payload bytes |
| CRC-32 | 4 | CRC over header with this field zero plus payload |

DATA chunks are 1024 bytes except for the final chunk. ACK payloads are empty.
META is JSON containing `{name, size, sha256, window, chunk}`. FIN_ACK is `OK`
or `FAIL`.

## Sender workflow

1. Hash the local file and send META until META_ACK arrives.
2. Send DATA while the next sequence is below `base + window` and below the
   congestion-window limit.
3. Keep one deadline per outstanding packet.
4. ACKs remove only their packet; `base` advances across contiguous ACKs.
5. On timeout, resend that packet, back off RTO, and apply one AIMD decrease
   per flight.
6. Send FIN and wait for FIN_ACK. If all DATA was ACKed but the final ACK is
   lost, report that completion was not confirmed.

## Receiver workflow

1. Listen for META and remember its sender address.
2. Validate metadata and send META_ACK.
3. Accept only packets from that address.
4. Use `ReceiveWindow`: buffer in-window out-of-order DATA, re-ACK old DATA,
   and drop packets beyond the window without ACKing them.
5. Write contiguous chunks to a temporary file.
6. On FIN, verify size and SHA-256, send OK or FAIL, and publish only verified
   output. Duplicate FIN packets receive the same answer for about two seconds.

## Timers and congestion

The first RTO is 0.5 seconds and stays between 0.1 and 2.0 seconds:

```text
EstimatedRTT = 0.875 * EstimatedRTT + 0.125 * SampleRTT
DevRTT       = 0.75 * DevRTT + 0.25 * abs(SampleRTT - EstimatedRTT)
RTO          = EstimatedRTT + 4 * DevRTT
```

Karn's Rule means samples from retransmitted packets are ignored. AIMD starts
at one packet, grows by one packet per ACK during slow start, then by `1/cwnd`
per ACK. A timeout sets `ssthresh` and `cwnd` to half the current window, with
the minimum two-packet rule and a clamp to the configured window.

## Module responsibilities

- `protocol.py`: header codec, CRC validation, packet types, and receive window.
- `models.py`: plain settings, statistics, and event records.
- `congestion.py`: RTT/RTO estimator and AIMD.
- `channel.py`: test/demo fault injection around a UDP socket.
- `sender.py`: hashing, META/DATA/FIN, timers, ACKs, and retransmission.
- `receiver.py`: address filtering, buffering, temporary output, and verification.
- `__main__.py`: plain CLI and optional trace CSV writing.
- `app.py`: Tkinter presentation only; it does not define networking rules.
