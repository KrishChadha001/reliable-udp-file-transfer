#!/usr/bin/env bash
# Run explicitly with bash. Operates only on lab-owned network namespaces.
set -euo pipefail
LOCK=/run/rudp-teaching-lab.lock
SENDER=rudp-lab-sender
RECEIVER=rudp-lab-receiver
require_root() { [[ $(id -u) -eq 0 ]] || { echo 'Use sudo for the isolated NetEm lab.' >&2; exit 1; }; }
owns_lab() { [[ -f "$LOCK/owner" ]] && [[ $(cat "$LOCK/owner") == 'reliable-udp-lab-v1' ]]; }
cleanup() {
    owns_lab || { echo 'No owned lab found; nothing removed.'; return 0; }
    ip netns del "$SENDER" 2>/dev/null || true
    ip netns del "$RECEIVER" 2>/dev/null || true
    ip link del rudp-veth-s 2>/dev/null || true
    rm -f -- "$LOCK/owner"
    rmdir -- "$LOCK"
}
case ${1:-help} in
  up)
    require_root
    command -v ip >/dev/null; command -v tc >/dev/null
    shift; loss=0; delay=0; jitter=0; rate=unlimited; reorder=0
    while (($#)); do
      [[ $# -ge 2 ]] || { echo 'Missing option value' >&2; exit 2; }
      case $1 in
        --loss) loss=$2;; --delay) delay=$2;; --jitter) jitter=$2;; --rate) rate=$2;; --reorder) reorder=$2;;
        *) echo "Unknown option: $1" >&2; exit 2;;
      esac
      shift 2
    done
    python3 - "$loss" "$delay" "$jitter" "$reorder" <<'PY'
import math,sys
loss,delay,jitter,reorder=map(float,sys.argv[1:])
if not all(math.isfinite(x) for x in (loss,delay,jitter,reorder)) or not (0<=loss<=100 and 0<=reorder<=100 and 0<=jitter<=delay<=10000):
    raise SystemExit('Invalid impairment: loss/reorder 0..100; 0 <= jitter <= delay <= 10000 ms')
if reorder and delay==0: raise SystemExit('Reordering needs a positive delay')
PY
    [[ $rate == unlimited || $rate =~ ^[0-9]+(\.[0-9]+)?(kbit|mbit|gbit)$ ]] || { echo 'Invalid rate' >&2; exit 2; }
    for ns in "$SENDER" "$RECEIVER"; do
      if ip netns list | awk '{print $1}' | grep -Fxq "$ns"; then echo "Namespace already exists: $ns; refusing to replace it" >&2; exit 1; fi
    done
    if ip link show rudp-veth-s >/dev/null 2>&1 || ip link show rudp-veth-r >/dev/null 2>&1; then echo 'Lab interface name already in use' >&2; exit 1; fi
    mkdir -m 700 -- "$LOCK" || { echo 'Lab lock exists; inspect lab or run down' >&2; exit 1; }
    printf '%s\n' 'reliable-udp-lab-v1' > "$LOCK/owner"
    trap cleanup ERR
    ip netns add "$SENDER"; ip netns add "$RECEIVER"
    ip link add rudp-veth-s type veth peer name rudp-veth-r
    ip link set rudp-veth-s netns "$SENDER"; ip link set rudp-veth-r netns "$RECEIVER"
    ip -n "$SENDER" addr add 10.203.0.1/24 dev rudp-veth-s
    ip -n "$RECEIVER" addr add 10.203.0.2/24 dev rudp-veth-r
    ip -n "$SENDER" link set lo up; ip -n "$RECEIVER" link set lo up
    ip -n "$SENDER" link set rudp-veth-s up; ip -n "$RECEIVER" link set rudp-veth-r up
    # Both directions get identical independent impairments; ACKs may also be lost.
    effects=(loss "${loss}%" delay "${delay}ms" "${jitter}ms")
    [[ $reorder == 0 ]] || effects+=(reorder "${reorder}%" 50%)
    [[ $rate == unlimited ]] || effects+=(rate "$rate")
    ip netns exec "$SENDER" tc qdisc replace dev rudp-veth-s root netem "${effects[@]}"
    ip netns exec "$RECEIVER" tc qdisc replace dev rudp-veth-r root netem "${effects[@]}"
    trap - ERR
    echo "Lab ready: loss=$loss% each direction; one-way delay=$delay ms; jitter=$jitter ms; reorder=$reorder%; rate=$rate each direction"
    ;;
  down) require_root; cleanup;;
  exec)
    require_root; owns_lab || { echo 'Run up first' >&2; exit 1; }
    case ${2:-} in sender) ns=$SENDER;; receiver) ns=$RECEIVER;; *) echo 'Use exec sender|receiver COMMAND...' >&2; exit 2;; esac
    shift 2; [[ $# -gt 0 ]] || exit 2
    exec ip netns exec "$ns" "$@"
    ;;
  status)
    require_root; owns_lab || { echo 'No owned lab'; exit 1; }
    ip netns exec "$SENDER" tc -s qdisc show dev rudp-veth-s
    ip netns exec "$RECEIVER" tc -s qdisc show dev rudp-veth-r
    ;;
  *) echo 'Usage: bash tools/netem.sh up [--loss 5 --delay 25 --jitter 5 --rate 10mbit --reorder 1]'
     echo '       bash tools/netem.sh exec sender|receiver COMMAND...'
     echo '       bash tools/netem.sh status|down';;
esac
