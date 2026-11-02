"""User commands; no transfer state or socket algorithms live here."""

import argparse
import math
from pathlib import Path
import sys
import time
from . import __version__
from .common import Config, Metrics, hash_stream, valid_name
from .ui import Progress


def parser():
    p = argparse.ArgumentParser(
        prog="rudp",
        description="Reliable UDP Transport Lab | Selective Repeat + AIMD",
        epilog="Ubuntu: python3 -m rudp serve --root samples   |   python3 -m rudp get --file 1mb.bin --output received.bin",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)
    for name, desc in [
        ("serve", "Share files with one receiver at a time"),
        ("get", "Receive one file and verify its SHA-256"),
    ]:
        s = sub.add_parser(name, help=desc, description=desc)
        s.add_argument("--protocol", choices=["udp", "tcp"], default="udp")
        s.add_argument(
            "--port",
            type=int,
            default=9000,
            help="Listening/server port (default: 9000)",
        )
        s.add_argument(
            "--window",
            type=int,
            default=64,
            help="Maximum Selective Repeat window, 1..4096",
        )
        s.add_argument(
            "--cwnd",
            type=float,
            default=4.0,
            help="Initial UDP congestion window (default: 4)",
        )
        s.add_argument(
            "--retries",
            type=int,
            default=30,
            help="Maximum transmissions per packet/control message",
        )
        s.add_argument(
            "--timeout",
            type=float,
            default=15.0,
            help="Inactivity timeout in seconds (default: 15)",
        )
        s.add_argument(
            "--control-timeout",
            type=float,
            default=0.3,
            help="META/FIN retry interval in seconds (default: 0.3)",
        )
        s.add_argument(
            "--metrics", default="", help="Write measured transfer statistics to JSON"
        )
        s.add_argument(
            "--trace", default="", help="Write packet/window event trace to CSV"
        )
        s.add_argument(
            "--plain", action="store_true", help="Disable animated terminal output"
        )
        s.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
        s.add_argument(
            "--verbose",
            action="store_true",
            help="Print packet events (slower; useful for demonstration)",
        )
        if name == "serve":
            s.add_argument(
                "--bind",
                default="127.0.0.1",
                help="Bind IPv4 address; use 0.0.0.0 for another VM",
            )
            s.add_argument(
                "--root",
                default="samples",
                help="Directory containing shareable regular files",
            )
            s.add_argument(
                "--once", action="store_true", help="Exit after one accepted request"
            )
        else:
            s.add_argument(
                "--host", default="127.0.0.1", help="Sender hostname or IPv4 address"
            )
            s.add_argument(
                "--file",
                required=True,
                help="Requested basename in sender share directory",
            )
            s.add_argument(
                "--output", default="", help="Output path (default: received-<file>)"
            )
            s.add_argument(
                "--force",
                action="store_true",
                help="Replace destination after successful hash verification",
            )
    inspect = sub.add_parser("inspect", help="Print local file size and SHA-256")
    inspect.add_argument("path")
    sub.add_parser(
        "benchmark", help="Run repeatable UDP/TCP comparisons (see benchmark --help)"
    )
    return p


def menu():
    print(
        "\n  ================================================\n  RELIABLE UDP / TRANSPORT LAB\n  ================================================"
    )
    print(
        "  [1] Start sender\n  [2] Receive a file\n  [3] Inspect local file\n  [4] Show command help\n  [0] Exit\n"
    )
    choice = input("  Choose: ").strip()
    if choice == "0":
        return None
    if choice == "4":
        return ["--help"]
    if choice == "3":
        return ["inspect", input("  File path: ").strip()]
    if choice not in ("1", "2"):
        raise ValueError("Choose 0, 1, 2, 3 or 4")
    proto = input("  Protocol [udp/tcp, default udp]: ").strip() or "udp"
    port = input("  Port [9000]: ").strip() or "9000"
    if choice == "1":
        return [
            "serve",
            "--protocol",
            proto,
            "--port",
            port,
            "--root",
            input("  Share directory [samples]: ").strip() or "samples",
        ]
    filename = input("  Requested filename: ").strip()
    return [
        "get",
        "--protocol",
        proto,
        "--port",
        port,
        "--host",
        input("  Sender address [127.0.0.1]: ").strip() or "127.0.0.1",
        "--file",
        filename,
        "--output",
        input("  Save as [received-" + filename + "]: ").strip()
        or "received-" + filename,
    ]


def validate(cfg, command):
    if not 1 <= cfg.port <= 65535:
        raise ValueError("Port must be 1..65535")
    if not 1 <= cfg.window <= 4096:
        raise ValueError("Window must be 1..4096")
    if not math.isfinite(cfg.cwnd) or not 1 <= cfg.cwnd <= cfg.window:
        raise ValueError("cwnd must be finite and 1..window")
    if not 1 <= cfg.retries <= 1000:
        raise ValueError("Retries must be 1..1000")
    if not math.isfinite(cfg.timeout) or not 0.1 <= cfg.timeout <= 3600:
        raise ValueError("Timeout must be 0.1..3600 seconds")
    if not math.isfinite(cfg.control_timeout) or not 0.05 <= cfg.control_timeout <= 3:
        raise ValueError("Control timeout must be 0.05..3 seconds")
    if command == "serve":
        if not Path(cfg.root).is_dir():
            raise ValueError(
                "Share directory does not exist; run tools/fixtures.py first"
            )
    elif not valid_name(cfg.file):
        raise ValueError(
            "Invalid file name; use a basename without directory separators"
        )
    artifacts = [Path(x).resolve() for x in (cfg.metrics, cfg.trace) if x]
    if len(set(artifacts)) != len(artifacts):
        raise ValueError("Metrics and trace paths must differ")
    if command == "get" and Path(cfg.output).resolve() in artifacts:
        raise ValueError("Output, metrics and trace paths must differ")
    if command == "serve":
        root = Path(cfg.root).resolve()
        if any(p == root or root in p.parents for p in artifacts):
            raise ValueError("Write metrics and traces outside the share directory")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "benchmark":
        from tools.benchmark import main as benchmark_main

        return benchmark_main(argv[1:])
    if not argv:
        if not sys.stdin.isatty():
            parser().print_help()
            return 0
        try:
            argv = menu()
        except (KeyboardInterrupt, EOFError):
            return 130
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if argv is None:
            return 0
    args = parser().parse_args(argv)
    if args.command == "inspect":
        try:
            path = Path(args.path)
            if not path.is_file():
                raise ValueError("Inspect requires a regular file")
            with path.open("rb") as f:
                digest = hash_stream(f)
            print(f"{path}\nBytes: {path.stat().st_size}\nSHA-256: {digest}")
            return 0
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    fields = {k: v for k, v in vars(args).items() if k != "command"}
    cfg = Config(**fields)
    if args.command == "get" and not cfg.output:
        cfg.output = "received-" + cfg.file
    ui = None
    validated = False
    m = Metrics(cfg.protocol, "receiver", filename=cfg.file)
    if cfg.protocol == "tcp":
        m.retransmissions = m.retransmission_pct = None
    started = time.monotonic()
    try:
        validate(cfg, args.command)
        validated = True
        ui = Progress(cfg, "sender" if args.command == "serve" else "receiver")
        ui.banner(
            ("SENDER" if args.command == "serve" else "RECEIVER")
            + "  |  "
            + cfg.protocol.upper()
        )
        if cfg.protocol == "udp":
            from . import udp as transport
        else:
            from . import tcp as transport
        if args.command == "serve":
            result = transport.serve(cfg, ui)
            return 0 if result.success else 1
        transport.get(cfg, m, ui, started)
        m.save(cfg.metrics)
        ui.summary(m)
        return 0
    except (OSError, ValueError, KeyboardInterrupt) as exc:
        m.error = (
            "Interrupted by user" if isinstance(exc, KeyboardInterrupt) else str(exc)
        )
        m.success = False
        m.finish(started)
        try:
            if validated:
                m.save(cfg.metrics)
        except OSError:
            pass
        if ui:
            ui.clear()
        print(f"Error: {m.error}", file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    finally:
        if ui:
            ui.close()
