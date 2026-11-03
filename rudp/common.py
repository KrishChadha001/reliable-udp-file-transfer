"""Configuration, bounded control encoding, safe files and measured metrics."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import socket
import stat
import tempfile
import time
from typing import BinaryIO
from .protocol import CHUNK, META_ACK, ProtocolError

VERSION = 1


@dataclass
class Config:
    protocol: str = "udp"
    host: str = "127.0.0.1"
    bind: str = "127.0.0.1"
    port: int = 9000
    root: str = "samples"
    file: str = ""
    output: str = ""
    window: int = 64
    cwnd: float = 4.0
    retries: int = 30
    timeout: float = 15.0
    control_timeout: float = 0.3
    once: bool = False
    force: bool = False
    plain: bool = False
    no_color: bool = False
    verbose: bool = False
    metrics: str = ""
    trace: str = ""


@dataclass
class Metrics:
    protocol: str
    role: str
    filename: str = ""
    success: bool = False
    bytes: int = 0
    packets: int = 0
    retransmissions: int | None = 0
    retransmission_pct: float | None = 0.0
    elapsed_s: float = 0.0
    goodput_mbps: float = 0.0
    sha256: str = ""
    corrupt_packets: int = 0
    duplicates: int = 0
    max_buffered: int = 0
    congestion_events: int = 0
    cwnd: float | None = None
    srtt_ms: float | None = None
    rto_ms: float | None = None
    error: str = ""

    def finish(self, started: float):
        self.elapsed_s = max(time.monotonic() - started, 1e-9)
        self.goodput_mbps = (
            self.bytes * 8 / self.elapsed_s / 1e6 if self.success else 0.0
        )
        self.retransmission_pct = (
            (100 * self.retransmissions / self.packets if self.packets else 0.0)
            if self.retransmissions is not None
            else None
        )

    def save(self, path: str):
        if path:
            Path(path).write_text(
                json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8"
            )


def control(data: dict) -> bytes:
    raw = json.dumps(data, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    if len(raw) > CHUNK:
        raise ProtocolError("Control message exceeds 1024 bytes")
    return raw


def parse_control(raw: bytes) -> dict:
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
        return obj
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError("Malformed control message") from exc


def valid_name(name) -> bool:
    return (
        isinstance(name, str)
        and 0 < len(name) <= 200
        and name not in (".", "..")
        and all(32 <= ord(c) < 127 and c not in "/\\:" for c in name)
    )


def valid_token(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(c in "0123456789abcdef" for c in value)
    )


def error_control(request_id, message) -> dict:
    """An invalid request must never make its error reply exceed the wire cap."""
    safe_id = request_id if valid_token(request_id) else None
    # Escape before truncating: JSON can expand a non-ASCII codepoint to 12
    # bytes, so a character-count limit on the original string is insufficient.
    safe_message = str(message)[:250].encode("unicode_escape").decode("ascii")[:250]
    return {"request": safe_id, "error": safe_message}


def packet_count(size: int) -> int:
    return (size + CHUNK - 1) // CHUNK


def hash_stream(f: BinaryIO) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: f.read(65536), b""):
        h.update(block)
    f.seek(0)
    return h.hexdigest()


def open_shared(root: str, name: str):
    if not valid_name(name):
        raise ProtocolError(
            "Invalid file name; request a basename inside the share directory"
        )
    path = Path(root).resolve(strict=True) / name
    if path.is_symlink():
        raise ProtocolError("Symlink files are not served")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ProtocolError("File not found or cannot open regular file") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ProtocolError("Not a regular file")
        if packet_count(info.st_size) >= META_ACK:
            raise ProtocolError("File exceeds sequence number limit")
        return os.fdopen(fd, "rb"), info.st_size
    except BaseException:
        os.close(fd)
        raise


class OutputFile:
    """Write alongside destination; only publish a verified complete file."""

    def __init__(self, path: str, force: bool = False):
        self.path = Path(path).absolute()
        self.force = force
        self.published = False
        if not force and os.path.lexists(self.path):
            raise FileExistsError("Output already exists; use --force to replace it")
        fd, name = tempfile.mkstemp(
            prefix=self.path.name + ".part.", dir=self.path.parent
        )
        self.temp = Path(name)
        self.stream = os.fdopen(fd, "wb")

    def __enter__(self):
        return self

    def publish(self):
        self.stream.flush()
        self.stream.close()
        if self.force:
            os.replace(self.temp, self.path)
        elif os.name == "nt":
            os.rename(self.temp, self.path)  # Windows rename fails if target exists.
        else:
            os.link(self.temp, self.path)  # Atomic no-clobber, even after a race.
            self.temp.unlink()
        self.published = True

    def __exit__(self, *_):
        self.stream.close()
        if not self.published:
            self.temp.unlink(missing_ok=True)


def make_meta(cfg: Config, request: dict, size: int, digest: str) -> dict:
    window = request.get("window")
    if type(window) is not int or not 1 <= window <= 4096:
        raise ProtocolError("Invalid receive window")
    return dict(
        version=VERSION,
        id=secrets.token_hex(16),
        request=request["id"],
        file=request["file"],
        size=size,
        sha256=digest,
        window=min(cfg.window, window),
        chunk=CHUNK,
        control_timeout=cfg.control_timeout,
        control_retries=cfg.retries,
    )


def validate_request(request: dict):
    if (
        request.get("version") != VERSION
        or not valid_token(request.get("id"))
        or not valid_name(request.get("file"))
    ):
        raise ProtocolError("Invalid file request or protocol version")
    w = request.get("window")
    if type(w) is not int or not 1 <= w <= 4096:
        raise ProtocolError("Invalid receive window")


def validate_meta(meta: dict, request: dict):
    size = meta.get("size")
    w = meta.get("window")
    digest = meta.get("sha256")
    if (
        meta.get("version") != VERSION
        or meta.get("request") != request["id"]
        or not valid_token(meta.get("id"))
        or meta.get("file") != request["file"]
        or meta.get("chunk") != CHUNK
        or type(size) is not int
        or size < 0
        or packet_count(size) >= META_ACK
        or type(w) is not int
        or not 1 <= w <= request["window"]
        or not isinstance(digest, str)
        or len(digest) != 64
        or any(c not in "0123456789abcdef" for c in digest)
    ):
        raise ProtocolError("Invalid transfer metadata")
    interval = meta.get("control_timeout")
    if (
        type(interval) not in (int, float)
        or not math.isfinite(interval)
        or not 0.05 <= interval <= 3
    ):
        raise ProtocolError("Invalid sender control retry interval")
    retries = meta.get("control_retries")
    if type(retries) is not int or not 1 <= retries <= 1000:
        raise ProtocolError("Invalid sender control retry count")


def validate_final(final: dict, session: str, size: int, packets: int):
    if (
        final.get("id") != session
        or type(final.get("bytes")) is not int
        or final["bytes"] != size
        or type(final.get("packets")) is not int
        or final["packets"] != packets
    ):
        raise ProtocolError("Invalid completion metadata")
    for key in ("retransmissions", "congestion_events"):
        if type(final.get(key)) is not int or final[key] < 0:
            raise ProtocolError("Invalid completion counters")
    for key, low, high in (
        ("cwnd", 1, 4096),
        ("srtt_ms", 0, 3600000),
        ("rto_ms", 0, 3000),
    ):
        value = final.get(key)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not low <= value <= high
        ):
            raise ProtocolError("Invalid completion timing/window statistics")


def udp_socket(bind: str = "0.0.0.0"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind((bind, 0))
        return sock
    except BaseException:
        sock.close()
        raise
