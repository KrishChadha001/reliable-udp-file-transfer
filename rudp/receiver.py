"""Listen for a pushed file and verify it before publishing the destination."""

import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
from typing import Callable, Optional

from .channel import LossyChannel
from .models import Event, Settings, Stats
from .protocol import CHUNK, Kind, Packet, ProtocolError, ReceiveWindow


EventCallback = Optional[Callable[[Event], None]]


def _emit(callback: EventCallback, event: Event) -> None:
    """Forward a receiver event without coupling the transport to a user interface."""
    if callback is not None:
        callback(event)


def _receive(sock: socket.socket, wait: float) -> tuple[Optional[Packet], object]:
    """Decode one datagram and report malformed packets as absent."""
    sock.settimeout(max(0.001, wait))
    try:
        raw, peer = sock.recvfrom(65535)
    except socket.timeout:
        return None, None
    try:
        return Packet.decode(raw), peer
    except ProtocolError:
        return None, peer


def _metadata(raw: bytes) -> dict:
    """Validate the small JSON metadata record before creating a receive window."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("META payload must be a JSON object")
    name = value.get("name")
    size = value.get("size")
    digest = value.get("sha256")
    window = value.get("window")
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(size, int)
        or size < 0
        or not isinstance(digest, str)
        or len(digest) != 64
        or not isinstance(window, int)
        or window < 1
        or value.get("chunk") != CHUNK
    ):
        raise ValueError("Invalid transfer metadata")
    return value


def _safe_name(name: str) -> str:
    """Keep only the basename so metadata cannot escape the save directory."""
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _available_path(folder: Path, name: str) -> Path:
    """Choose a non-existing destination by adding `` (1)`` when necessary."""
    candidate = folder / name
    stem, suffix = candidate.stem, candidate.suffix
    number = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({number}){suffix}"
        number += 1
    return candidate


def _linger(
    sock: socket.socket,
    channel: LossyChannel,
    peer,
    response: bytes,
    callback: EventCallback,
) -> None:
    """Re-ACK duplicate FIN packets briefly so a lost final ACK can recover."""
    end = time.monotonic() + 2.0
    while time.monotonic() < end:
        packet, source = _receive(sock, min(0.1, end - time.monotonic()))
        if source == peer and packet is not None and packet.kind == Kind.FIN:
            channel.sendto(Packet(Kind.FIN_ACK, payload=response).encode(), peer)
            _emit(callback, Event("ACK", -1, "duplicate FIN"))


def receive_file(
    port: int,
    save_dir: str,
    settings: Settings,
    on_event: EventCallback = None,
    stop: threading.Event = None,
) -> Stats:
    """Receive one pushed file, verify SHA-256, and publish it without overwriting."""
    stop = stop or threading.Event()
    stats = Stats()
    started = time.monotonic()
    folder = Path(save_dir)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    channel = LossyChannel(
        sock,
        settings.loss,
        settings.dup,
        settings.corrupt,
        settings.delay_ms,
        settings.jitter_ms,
        settings.seed,
    )
    temp_path = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        sock.bind(("0.0.0.0", port))
        peer = None
        metadata = None
        last_progress = time.monotonic()
        while metadata is None:
            if stop.is_set():
                raise RuntimeError("Receive cancelled")
            packet, source = _receive(sock, 0.2)
            if packet is None:
                if time.monotonic() - last_progress > settings.timeouts:
                    raise TimeoutError("No META packet received within inactivity timeout")
                continue
            if packet.kind != Kind.META:
                continue
            try:
                metadata = _metadata(packet.payload)
            except (ValueError, json.JSONDecodeError):
                continue
            peer = source
            channel.sendto(Packet(Kind.META_ACK).encode(), peer)
            _emit(on_event, Event("ACK", -1, "META"))
            last_progress = time.monotonic()
        total = (metadata["size"] + CHUNK - 1) // CHUNK
        stats.packets = total
        window = min(settings.window, metadata["window"])
        receive_window = ReceiveWindow(window, total, metadata["size"])
        output_name = _safe_name(metadata["name"])
        if not output_name:
            raise ValueError("META filename has no basename")
        destination = _available_path(folder, output_name)
        temp_path = folder / f".{destination.name}.{os.getpid()}.part"
        digest = hashlib.sha256()
        with temp_path.open("wb") as output:
            while True:
                if stop.is_set():
                    raise RuntimeError("Receive cancelled")
                packet, source = _receive(sock, 0.2)
                now = time.monotonic()
                if source != peer or packet is None:
                    if now - last_progress > settings.timeouts:
                        raise TimeoutError("No DATA packet received within inactivity timeout")
                    continue
                if packet.kind == Kind.META:
                    channel.sendto(Packet(Kind.META_ACK).encode(), peer)
                    continue
                if packet.kind == Kind.DATA:
                    duplicate = packet.seq < receive_window.base or packet.seq in receive_window.buffer
                    delivery_base = receive_window.base
                    accepted, ready = receive_window.accept(packet.seq, packet.payload)
                    if not accepted:
                        continue
                    channel.sendto(Packet(Kind.ACK, packet.seq).encode(), peer)
                    if duplicate:
                        stats.duplicates += 1
                        _emit(on_event, Event("DUPLICATE", packet.seq))
                    for offset, block in enumerate(ready):
                        output.write(block)
                        digest.update(block)
                        stats.bytes += len(block)
                        _emit(on_event, Event("DELIVERED", delivery_base + offset))
                    stats.max_buffered = max(stats.max_buffered, len(receive_window.buffer))
                    last_progress = now
                    _emit(
                        on_event,
                        Event("BUFFERED", packet.seq)
                        if not ready
                        else Event("DELIVERED", packet.seq),
                    )
                elif packet.kind == Kind.FIN:
                    verified = (
                        receive_window.base == total
                        and stats.bytes == metadata["size"]
                        and digest.hexdigest() == metadata["sha256"]
                    )
                    payload = b"OK" if verified else b"FAIL"
                    channel.sendto(Packet(Kind.FIN_ACK, payload=payload).encode(), peer)
                    stats.verified = verified
                    if verified:
                        output.flush()
                        output.close()
                        os.replace(temp_path, destination)
                        temp_path = None
                        _emit(on_event, Event("VERIFIED", total))
                    else:
                        stats.error = "SHA-256 or file-size verification failed"
                    _linger(sock, channel, peer, payload, on_event)
                    return stats
        return stats
    except (OSError, ValueError, ProtocolError, TimeoutError, RuntimeError) as exc:
        stats.error = str(exc)
        return stats
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        stats.elapsed = max(time.monotonic() - started, 1e-9)
        stats.goodput = stats.bytes / stats.elapsed
        channel.close()
