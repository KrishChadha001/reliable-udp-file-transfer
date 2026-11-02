"""Run matched real-socket UDP/TCP comparisons, optionally in Linux NetEm."""

import argparse
import csv
from datetime import datetime, timezone
import itertools
import json
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.fixtures import generate
from tools.analyze import analyze

FIELDS = [
    "run",
    "protocol",
    "condition",
    "file",
    "repetition",
    "success",
    "bytes",
    "elapsed_s",
    "goodput_mbps",
    "retransmissions",
    "retransmission_pct",
    "sha256",
    "error",
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def conditions(args):
    if args.full:
        result = []
        for loss, delay, rate in itertools.product(
            (0, 1, 5, 10), (0, 25, 50), ("unlimited", "10mbit")
        ):
            result.append(dict(loss=loss, delay=delay, jitter=0, rate=rate, reorder=0))
            if delay:
                result.append(
                    dict(loss=loss, delay=delay, jitter=5, rate=rate, reorder=1)
                )
        return result
    return [
        dict(
            loss=args.loss,
            delay=args.delay,
            jitter=args.jitter,
            rate=args.rate,
            reorder=args.reorder,
        )
    ]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--smoke",
        action="store_true",
        help="Two small sizes, one repetition unless overridden",
    )
    p.add_argument(
        "--full",
        action="store_true",
        help="40 NetEm conditions x 3 sizes x 2 protocols x 5 repeats = 1200 runs",
    )
    p.add_argument(
        "--netem",
        action="store_true",
        help="Create isolated Linux namespaces; requires sudo",
    )
    p.add_argument(
        "--diagnostic-traces",
        action="store_true",
        help="Collect UDP cwnd traces; labels runs diagnostic (extra UDP logging overhead)",
    )
    p.add_argument("--repetitions", type=int, default=None)
    p.add_argument("--sizes", help="Comma-separated file sizes in bytes")
    p.add_argument(
        "--output", help="New result directory (must not already contain runs.csv)"
    )
    p.add_argument("--loss", type=float, default=0.0)
    p.add_argument(
        "--delay", type=float, default=0.0, help="One-way delay in milliseconds"
    )
    p.add_argument("--jitter", type=float, default=0.0)
    p.add_argument("--reorder", type=float, default=0.0)
    p.add_argument("--rate", default="unlimited", help="unlimited or e.g. 10mbit")
    p.add_argument("--window", type=int, default=64)
    p.add_argument("--cwnd", type=float, default=4.0)
    p.add_argument(
        "--run-timeout",
        type=float,
        default=600.0,
        help="Maximum wall time per client run in seconds",
    )
    args = p.parse_args(argv)
    if args.full and args.smoke:
        p.error("Choose either --full or --smoke")
    if args.full and not args.netem:
        p.error("--full requires --netem; emulated results must use actual NetEm")
    if args.netem and (platform.system() != "Linux" or os.geteuid() != 0):
        p.error("--netem requires Linux and sudo")
    if args.netem and not all(shutil.which(x) for x in ("ip", "tc", "bash")):
        p.error("Install iproute2 and bash")
    if not args.netem and (
        args.loss
        or args.delay
        or args.jitter
        or args.reorder
        or args.rate != "unlimited"
    ):
        p.error("Impairment flags require --netem")
    if not all(
        math.isfinite(x)
        for x in (
            args.loss,
            args.delay,
            args.jitter,
            args.reorder,
            args.cwnd,
            args.run_timeout,
        )
    ):
        p.error("Numeric parameters must be finite")
    if not (
        0 <= args.loss <= 100
        and 0 <= args.reorder <= 100
        and 0 <= args.jitter <= args.delay <= 10000
    ):
        p.error("Invalid impairment range")
    if args.reorder and not args.delay:
        p.error("Reordering requires a positive delay")
    if not 1 <= args.window <= 4096 or not 1 <= args.cwnd <= args.window:
        p.error("Use 1 <= cwnd <= window <= 4096")
    if not 1 <= args.run_timeout <= 86400:
        p.error("Run timeout must be 1..86400 seconds")
    repeats = (
        args.repetitions if args.repetitions is not None else (1 if args.smoke else 5)
    )
    if not 1 <= repeats <= 100:
        p.error("Repetitions must be 1..100")
    try:
        sizes = (
            [int(x) for x in args.sizes.split(",")]
            if args.sizes
            else (
                [32768, 131072]
                if args.smoke
                else [1000000, 10000000, 50000000] if args.full else [1000000]
            )
        )
    except ValueError:
        p.error("Sizes must be comma-separated integers")
    if not sizes or any(not 0 <= s <= 1_000_000_000 for s in sizes):
        p.error("Sizes must be 0..1,000,000,000 bytes")
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = Path(args.output or f"results/{tag}").resolve()
    if (output / "runs.csv").exists():
        p.error("Result directory already contains runs.csv; choose a new directory")
    output.mkdir(parents=True, exist_ok=True)
    share = output / "fixtures"
    share.mkdir(exist_ok=True)
    for size in sizes:
        generate(share / f"{size}bytes.bin", size)
    environment = dict(
        created_utc=tag,
        python=sys.version,
        platform=platform.platform(),
        mode="linux-netem" if args.netem else "local-unimpaired",
        arguments=vars(args),
        repetitions=repeats,
        sizes=sizes,
        source=str(ROOT),
        conditions=conditions(args),
    )
    if args.netem:
        environment["tc_version"] = subprocess.check_output(
            ["tc", "-V"], text=True
        ).strip()
        environment["ip_version"] = subprocess.check_output(
            ["ip", "-V"], text=True
        ).strip()
    (output / "environment.json").write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8"
    )
    helper = ["bash", str(ROOT / "tools" / "netem.sh")]
    lab_active = False
    server = None
    failures = 0
    run = 0
    try:
        with (output / "runs.csv").open("w", newline="", encoding="utf-8") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=FIELDS)
            writer.writeheader()
            for condition in conditions(args):
                if args.netem:
                    opts = [
                        part
                        for key, value in condition.items()
                        for part in ("--" + key, str(value))
                    ]
                    subprocess.run([*helper, "up", *opts], check=True, cwd=ROOT)
                    lab_active = True
                    with (output / f"condition-{run:04d}-qdisc-before.txt").open(
                        "w"
                    ) as qlog:
                        subprocess.run([*helper, "status"], check=True, stdout=qlog)
                label = (
                    ("netem-" + "-".join(f"{k}{v}" for k, v in condition.items()))
                    if args.netem
                    else "local-clean"
                )
                if args.diagnostic_traces:
                    label += "-diagnostic"
                for size, repetition, protocol in itertools.product(
                    sizes, range(1, repeats + 1), ("udp", "tcp")
                ):
                    run += 1
                    prefix = output / f"run-{run:04d}-{protocol}"
                    port = 9000 if args.netem else free_port()
                    sender_metrics = Path(str(prefix) + "-sender.json")
                    client_metrics = Path(str(prefix) + "-client.json")
                    received = Path(str(prefix) + "-received.bin")
                    name = f"{size}bytes.bin"
                    shared = [
                        "--protocol",
                        protocol,
                        "--port",
                        str(port),
                        "--plain",
                        "--window",
                        str(args.window),
                        "--cwnd",
                        str(args.cwnd),
                    ]
                    sender = [
                        sys.executable,
                        "-m",
                        "rudp",
                        "serve",
                        *shared,
                        "--root",
                        str(share),
                        "--once",
                        "--metrics",
                        str(sender_metrics),
                    ]
                    client = [
                        sys.executable,
                        "-m",
                        "rudp",
                        "get",
                        *shared,
                        "--file",
                        name,
                        "--output",
                        str(received),
                        "--metrics",
                        str(client_metrics),
                    ]
                    if protocol == "udp" and args.diagnostic_traces:
                        sender += ["--trace", str(prefix) + "-trace.csv"]
                    if args.netem:
                        sender += ["--bind", "10.203.0.1"]
                        client += ["--host", "10.203.0.1"]
                        sender = [*helper, "exec", "sender", *sender]
                        client = [*helper, "exec", "receiver", *client]
                    record = dict(
                        run=run,
                        protocol=protocol,
                        condition=label,
                        file=name,
                        repetition=repetition,
                        success="false",
                        bytes=size,
                        elapsed_s="",
                        goodput_mbps="",
                        retransmissions="",
                        retransmission_pct="",
                        sha256="",
                        error="",
                    )
                    print(
                        f"[{run:04d}] {protocol.upper():3s} {name:18s} rep={repetition} {label}",
                        flush=True,
                    )
                    try:
                        with Path(str(prefix) + "-sender.log").open(
                            "w", encoding="utf-8"
                        ) as log:
                            server = subprocess.Popen(
                                sender,
                                cwd=ROOT,
                                stdout=log,
                                stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL,
                            )
                            deadline = time.monotonic() + 8
                            while "LISTENING" not in Path(log.name).read_text(
                                encoding="utf-8", errors="replace"
                            ):
                                if server.poll() is not None:
                                    raise RuntimeError(
                                        "Sender startup failed; inspect sender log"
                                    )
                                if time.monotonic() > deadline:
                                    raise TimeoutError("Sender startup timeout")
                                time.sleep(0.02)
                            with Path(str(prefix) + "-client.log").open(
                                "w", encoding="utf-8"
                            ) as logclient:
                                result = subprocess.run(
                                    client,
                                    cwd=ROOT,
                                    stdout=logclient,
                                    stderr=subprocess.STDOUT,
                                    timeout=args.run_timeout,
                                )
                            sender_code = server.wait(timeout=12)
                            data = (
                                json.loads(client_metrics.read_text())
                                if client_metrics.exists()
                                else {}
                            )
                            for key in (
                                "bytes",
                                "elapsed_s",
                                "goodput_mbps",
                                "retransmissions",
                                "retransmission_pct",
                                "sha256",
                                "error",
                            ):
                                if key in data:
                                    record[key] = (
                                        data[key] if data[key] is not None else ""
                                    )
                            record["success"] = (
                                "true"
                                if result.returncode == 0
                                and sender_code == 0
                                and data.get("success")
                                else "false"
                            )
                            if record["success"] == "false" and not record["error"]:
                                record["error"] = (
                                    "Client or sender failed; inspect per-run logs"
                                )
                    except (
                        OSError,
                        ValueError,
                        RuntimeError,
                        subprocess.TimeoutExpired,
                    ) as exc:
                        record["error"] = str(exc)
                    finally:
                        if server and server.poll() is None:
                            server.terminate()
                            try:
                                server.wait(timeout=3)
                            except subprocess.TimeoutExpired:
                                server.kill()
                                server.wait()
                        server = None
                        # Retain statistics/logs, not duplicate full-size output files.
                        received.unlink(missing_ok=True)
                        for part in output.glob(received.name + ".part.*"):
                            part.unlink(missing_ok=True)
                    if record["success"] == "false":
                        failures += 1
                    writer.writerow(record)
                    csvfile.flush()
                if lab_active:
                    with (output / f"condition-{run:04d}-qdisc-after.txt").open(
                        "w"
                    ) as qlog:
                        subprocess.run([*helper, "status"], check=True, stdout=qlog)
                    subprocess.run([*helper, "down"], check=True)
                    lab_active = False
    finally:
        if server and server.poll() is None:
            server.terminate()
            server.wait(timeout=5)
        if lab_active:
            subprocess.run([*helper, "down"], check=False)
    analyze(output / "runs.csv", output / "analysis")
    print(f"Completed {run} measured runs, {failures} failed. Results: {output}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
