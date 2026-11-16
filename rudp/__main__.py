import argparse
import csv
from pathlib import Path
import sys
import threading

from .models import Event, Settings
from .receiver import receive_file
from .sender import send_file


def _settings(args):
    """Build transport settings from the small command-line option set."""
    return Settings(
        window=args.window,
        loss=args.loss,
        dup=args.dup,
        corrupt=args.corrupt,
        delay_ms=args.delay,
        jitter_ms=args.jitter,
        seed=args.seed,
        timeouts=args.timeout,
        retries=args.retries,
    )


def _new_parser():
    """Create the plain send/receive parser for the simplified API."""
    parser = argparse.ArgumentParser(prog="python -m rudp")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("send", "recv"):
        command = sub.add_parser(name)
        command.add_argument("--port", type=int, required=True)
        command.add_argument("--window", type=int, default=32)
        command.add_argument("--loss", type=float, default=0)
        command.add_argument("--dup", type=float, default=0)
        command.add_argument("--corrupt", type=float, default=0)
        command.add_argument("--delay", type=float, default=0)
        command.add_argument("--jitter", type=float, default=0)
        command.add_argument("--seed", type=int)
        command.add_argument("--timeout", type=float, default=15)
        command.add_argument("--retries", type=int, default=20)
        command.add_argument("--verbose", action="store_true")
        command.add_argument("--trace", default="")
    send = sub.choices["send"]
    send.add_argument("--file", required=True)
    send.add_argument("--host", required=True)
    recv = sub.choices["recv"]
    recv.add_argument("--out", required=True)
    return parser


def _new_main(argv):
    """Run the simplified CLI while leaving legacy commands available temporarily."""
    args = _new_parser().parse_args(argv)
    settings = _settings(args)
    trace = None
    writer = None
    if args.trace:
        trace = Path(args.trace).open("w", newline="", encoding="utf-8")
        writer = csv.writer(trace)
        writer.writerow(("kind", "seq", "info"))

    def event(event: Event):
        """Print notable events and optionally append every event to a trace."""
        if writer is not None:
            writer.writerow((event.kind, event.seq, event.info))
            trace.flush()
        if args.verbose or event.kind in {"TIMEOUT", "RESEND", "VERIFIED"}:
            print(f"{event.kind} seq={event.seq} {event.info}".rstrip(), flush=True)

    stop = threading.Event()
    try:
        if args.command == "send":
            print(f"Sending {args.file} to {args.host}:{args.port}", flush=True)
            stats = send_file(args.file, args.host, args.port, settings, event, stop)
        else:
            print(f"Listening on UDP port {args.port}", flush=True)
            stats = receive_file(args.port, args.out, settings, event, stop)
        print(
            f"verified={stats.verified} bytes={stats.bytes} "
            f"retransmissions={stats.retransmissions} "
            f"rto_ms={stats.rto_ms:.1f} error={stats.error or 'none'}"
        )
        return 0 if stats.verified else 1
    except KeyboardInterrupt:
        stop.set()
        print("cancelled", file=sys.stderr)
        return 130
    finally:
        if trace is not None:
            trace.close()


def main(argv=None):
    """Run the simplified plain CLI."""
    values = list(sys.argv[1:] if argv is None else argv)
    if not values:
        _new_parser().print_help()
        return 0
    return _new_main(values)

raise SystemExit(main())
