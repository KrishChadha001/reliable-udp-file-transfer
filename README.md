# Reliable UDP File Transfer

This project demonstrates reliable file transfer over UDP using concepts from a
standard Computer Networks course. Three students can explain the complete path:
the sender hashes and segments a file, Selective Repeat retransmits individual
packets, and the receiver verifies the reconstructed file before publishing it.

## Architecture

```text
  sender.py                         receiver.py
  file -> META/DATA/FIN -> UDP -> META/DATA/FIN
          ^        ACK/FIN_ACK <- UDP <-
          |       LossyChannel (tests/demo)
          +-- per-packet timers, CRC-32, SHA-256
```

The receiver listens first. The sender pushes one selected local file. The
receiver stores only the basename from metadata and never overwrites an existing
file. Packets from addresses other than the address learned from META are
ignored.

## Install and run

Python 3.10+ and the standard library are enough for transfers:

```text
python -m rudp recv --port 9000 --out received
python -m rudp send --file samples/1mb.bin --host 127.0.0.1 --port 9000
```

Useful options include `--window`, `--loss`, `--dup`, `--corrupt`, `--delay`,
`--jitter`, `--seed`, `--verbose`, and `--trace trace.csv`.

The optional GUI is separate:

```text
python app.py
```

If Tkinter is missing on Debian/Ubuntu, install it with
`sudo apt install python3-tk`.

## Selective Repeat in this project

The sender may have at most `window` packets outstanding. Every DATA packet has
its own deadline. An ACK confirms only its sequence number, and the sender
advances the base only across contiguous acknowledged packets. A timeout resends
only the expired packet. The receiver buffers packets above the base, re-ACKs
duplicates below the base, and does not ACK packets beyond its window.

The fixed header is 11 bytes: type (1), sequence number (4), payload length (2),
and CRC-32 (4). DATA payloads are at most 1024 bytes. META contains JSON with
`name`, `size`, `sha256`, `window`, and `chunk`.

## Demo and tests

```text
python tools/demo.py
python -m unittest discover -s tests -v
python tools/make_files.py samples/generated.bin 100000
python tools/benchmark.py --simple --repeats 1 --output results/benchmark.csv
```

The demo uses a real loopback transfer with 5% simulated loss. It reports the
actual result; no benchmark values are included in this repository.

## Limitations

- This is a teaching implementation, not a replacement for TCP.
- Localhost measurements include Python, operating-system, and scheduling
  overhead and make no TCP-fairness claims.
- The receiver handles one transfer at a time and has no authentication.
- The optional fault channel is for tests and demonstrations, not a production
  network service.

TODO: add screenshot from a real GUI run.
