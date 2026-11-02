# Demonstration and Ubuntu lab manual

Use three Ubuntu terminals in the project directory. For a simple demo, all traffic can stay inside one VM; the NetEm setup supplies two isolated endpoints. No port forwarding through the VM host is required.

## Preparation

```bash
sudo apt update
sudo apt install python3 iproute2 tcpdump
python3 tools/fixtures.py
mkdir -p results
python3 -m unittest discover -s tests -v
```

Optional Wireshark GUI: install `wireshark` through Ubuntu's package manager. You can capture with tcpdump and open the resulting PCAP in Wireshark later, without running a GUI as root.

## Demo A — correct basic transfer (two minutes)

Terminal 1:

```bash
python3 -m rudp serve --root samples --port 9000 --once \
  --trace results/demo-clean-trace.csv --metrics results/demo-clean-sender.json
```

Terminal 2:

```bash
python3 -m rudp get --file 1mb.bin --output demo-clean.bin \
  --metrics results/demo-clean-receiver.json
sha256sum samples/1mb.bin demo-clean.bin
```

Explain the receiver-initiated request, metadata, sequence numbers, progress bar, exact byte count and matching hashes. A clean run may have zero retransmissions; that is a valid observation. Repeating with the same output filename should fail unless `--force` is explicitly given.

## Demo B — controlled loss, delay and cwnd (five minutes)

Create the lab once from terminal 3:

```bash
sudo bash tools/netem.sh up --loss 5 --delay 25 --jitter 5 --reorder 1 --rate 10mbit
sudo bash tools/netem.sh status
```

Terminal 1:

```bash
sudo bash tools/netem.sh exec sender python3 -m rudp serve \
  --bind 10.203.0.1 --root samples --port 9000 --once \
  --trace results/demo-loss-trace.csv --metrics results/demo-loss-sender.json
```

Terminal 2:

```bash
sudo bash tools/netem.sh exec receiver python3 -m rudp get \
  --host 10.203.0.1 --port 9000 --file 1mb.bin --output demo-loss.bin \
  --metrics results/demo-loss-receiver.json
sha256sum samples/1mb.bin demo-loss.bin
```

Show a timeout row in `demo-loss-trace.csv`. An ordinary packet loss experiment does not guarantee a particular number of losses, and loss-free streaks are possible. Use a sufficiently large fixture for an observable congestion trace. Do not promise a specific sawtooth shape before inspecting the run.

To render the standalone trace without a benchmark CSV:

```bash
python3 -c "from pathlib import Path; from tools.analyze import cwnd_svg; cwnd_svg(Path('results/demo-loss-trace.csv'), Path('results/demo-loss-cwnd.svg'))"
```

This is a diagnostic trace run. It is intentionally separate from the primary timed comparison, which disables packet-level trace logging.

## Demo C — packet capture

With the NetEm lab still active, use terminal 3 before starting a transfer:

```bash
sudo bash tools/netem.sh exec sender tcpdump -i rudp-veth-s -nn -s 0 \
  -w results/demo-udp.pcap udp
```

Stop tcpdump with Ctrl+C after transfer. Open the capture in Wireshark. Use display filter `udp && ip.addr == 10.203.0.1`. Filtering only port 9000 hides DATA, because the sender chooses a fresh ephemeral transfer port after REQUEST.

Select a UDP payload and inspect the first 11 bytes: one type byte, four sequence bytes, two length bytes and four CRC bytes. DATA is type 3, ACK is type 4. The application CRC is inside the payload, distinct from the UDP checksum in the transport header. META/FIN JSON is readable in the bytes pane. Wireshark does not automatically decode this custom protocol; manual byte inspection is sufficient for the demo.

To demonstrate a repeated sequence, look for DATA with the same sequence after a timeout and the associated individual ACK. For reordering, compare DATA sequence order in the capture with correctly ordered output bytes. Capture itself adds overhead; avoid it during primary performance timing unless both protocol comparisons use the same capture arrangement.

Cleanup after stopping any manual sender/capture processes:

```bash
sudo bash tools/netem.sh down
```

## Demo D — repeated UDP/TCP comparison

```bash
sudo python3 tools/benchmark.py --netem --loss 5 --delay 25 \
  --rate 10mbit --repetitions 5 --sizes 1000000 --output results/demo-five
```

The helper creates/cleans its own lab. Do not leave a manual lab active before this command; it refuses to replace an existing lab. Show `environment.json`, the ten rows in `runs.csv`, `analysis/summary.csv`, and `analysis/throughput.svg`.

For a separate diagnostic trace dataset:

```bash
sudo python3 tools/benchmark.py --netem --loss 5 --delay 25 \
  --sizes 1000000 --repetitions 1 --diagnostic-traces --output results/demo-traces
```

Diagnostic condition labels end in `-diagnostic`, so their timings are not silently pooled with primary comparisons. Open a generated `*-trace.svg` and explain additive growth, multiplicative reduction, RTO and retransmissions.

## Demo E — correctness under deterministic faults

```bash
python3 -m unittest discover -s tests -k loss_corruption -v
python3 -m unittest discover -s tests -k four_final -v
```

The proxy drops DATA and ACKs, corrupts a DATA packet, reorders packets, duplicates DATA and drops control traffic. The second regression drops four consecutive final ACKs. Both sender and receiver must still succeed and the bytes/hash must match. These are correctness tests, not claimed network-performance measurements.

## Troubleshooting

| Symptom | Action |
|---|---|
| No metadata response | Start matching sender protocol/port; check host/bind; for separate VMs allow UDP ephemeral response ports |
| Share directory missing | Run `python3 tools/fixtures.py`, or pass your existing `--root` |
| Destination exists | Choose another filename or explicitly use `--force` |
| Address already in use | Stop the previous sender or use another port |
| Cannot create namespace/qdisc | Use sudo, install iproute2, and check VM kernel support for network namespaces, veth and `sch_netem` |
| `RTNETLINK` / netem missing | Check `sudo modprobe sch_netem`; kernel/module availability depends on VM installation |
| Lab already exists | Stop manual lab processes, inspect `status`, then `down`; helper refuses to replace unrelated namespaces |
| A slow source hash exhausts request retries | Increase `--retries` or `--control-timeout` on the receiver; use stable local files |
| Different figures from included examples | Normal: included examples are Windows localhost measurements, not Ubuntu NetEm results |
| Leftover `.part.*` after hard kill | Inspect and remove only that incomplete output; it is not a verified destination |

For two physical VMs, bind sender to its VM network address or `0.0.0.0`, use the actual sender IP on the receiver, and account for VM firewall/NAT settings. The provided namespace setup is the recommended repeatable experiment environment.
