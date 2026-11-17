"""Push a file with Selective Repeat over a UDP socket."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import socket
import threading
import time
from typing import Callable, Optional

from .channel import LossyChannel
from .congestion import Aimd, Rtt
from .models import Event, Settings, Stats
from .protocol import CHUNK, Kind, Packet, ProtocolError


EventCallback = Optional[Callable[[Event], None]]


@dataclass
class Flight:
    """Track one packet so only that packet is resent when its timer expires."""

    payload: bytes
    first_sent: float
    sent: float
    deadline: float
    attempts: int = 1


def _emit(callback: EventCallback, event: Event) -> None:
    """Forward a protocol event when a caller requested progress reporting."""
    if callback is not None:
        callback(event)


def _packet_count(size: int) -> int:
    """Calculate how many 1024-byte data packets represent a file."""
    return (size + CHUNK - 1) // CHUNK


def _receive(
    sock: socket.socket, wait: float, stats: Optional[Stats] = None
) -> tuple[Optional[Packet], object]:
    """Read one datagram and discard malformed CRC or length data."""
    sock.settimeout(max(0.001, wait))
    try:
        raw, peer = sock.recvfrom(65535)
    except (socket.timeout, ConnectionResetError):
        return None, None
    try:
        return Packet.decode(raw), peer
    except ProtocolError:
        if stats is not None:
            stats.corrupt_dropped += 1
        return None, peer


def _send_control(
    channel: LossyChannel,
    sock: socket.socket,
    peer,
    packet: Packet,
    expected: Kind,
    settings: Settings,
    callback: EventCallback,
    stop: threading.Event,
) -> bool:
    """Reliably send META or FIN with stop-and-wait retries."""
    for attempt in range(settings.retries):
        if stop.is_set():
            raise RuntimeError("Transfer cancelled")
        channel.sendto(packet.encode(), peer)
        _emit(callback, Event("SEND" if attempt == 0 else "RESEND", packet.seq))
        deadline = time.monotonic() + settings.rto
        while time.monotonic() < deadline:
            if stop.is_set():
                raise RuntimeError("Transfer cancelled")
            answer, source = _receive(sock, deadline - time.monotonic())
            if source == peer and answer is not None and answer.kind == expected:
                return answer.payload == (b"OK" if expected == Kind.FIN_ACK else b"")
    return False


def send_file(
    path: str,
    host: str,
    port: int,
    settings: Settings,
    on_event: EventCallback = None,
    stop: threading.Event = None,
) -> Stats:
    """Hash, send, and selectively retransmit one file to a listening receiver."""
    stop = stop or threading.Event()
    stats = Stats()
    started = time.monotonic()
    channel = None
    try:
        source_path = Path(path)
        if not source_path.is_file():
            raise OSError(f"File does not exist: {path}")
        data = source_path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        total = _packet_count(len(data))
        stats.bytes = len(data)
        stats.packets = total
        peer = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
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
        metadata = json.dumps(
            {
                "name": source_path.name,
                "size": len(data),
                "sha256": digest,
                "window": settings.window,
                "chunk": CHUNK,
            },
            separators=(",", ":"),
        ).encode()
        if not _send_control(
            channel,
            sock,
            peer,
            Packet(Kind.META, 0, metadata),
            Kind.META_ACK,
            settings,
            on_event,
            stop,
        ):
            raise TimeoutError("META acknowledgement retry limit reached")
        _send_data(
            data,
            channel,
            sock,
            peer,
            settings,
            stats,
            on_event,
            stop,
        )
        fin_confirmed = _send_control(
            channel,
            sock,
            peer,
            Packet(Kind.FIN),
            Kind.FIN_ACK,
            settings,
            on_event,
            stop,
        )
        if fin_confirmed:
            stats.verified = True
        else:
            stats.error = "completed, final ACK not confirmed"
        return stats
    except (OSError, ProtocolError, TimeoutError, RuntimeError) as exc:
        stats.error = str(exc)
        return stats
    finally:
        stats.elapsed = max(time.monotonic() - started, 1e-9)
        stats.goodput = stats.bytes / stats.elapsed
        if channel is not None:
            channel.close()


def _send_data(
    data: bytes,
    channel: LossyChannel,
    sock: socket.socket,
    peer,
    settings: Settings,
    stats: Stats,
    callback: EventCallback,
    stop: threading.Event,
) -> None:
    """Run Selective Repeat with one deadline per outstanding data packet."""
    aimd = Aimd(1, settings.window)
    rtt = Rtt()
    packets = _packet_count(len(data))
    base = 0
    next_seq = 0
    acknowledged: set[int] = set()
    outstanding: dict[int, Flight] = {}
    last_progress = time.monotonic()
    while base < packets:
        if stop.is_set():
            raise RuntimeError("Transfer cancelled")
        while (
            next_seq < packets
            and next_seq < base + settings.window
            and len(outstanding) < aimd.limit
        ):
            start = next_seq * CHUNK
            payload = data[start : start + CHUNK]
            now = time.monotonic()
            channel.sendto(Packet(Kind.DATA, next_seq, payload).encode(), peer)
            outstanding[next_seq] = Flight(payload, now, now, now + rtt.rto)
            stats.sent += 1
            _emit(callback, Event("SEND", next_seq))
            next_seq += 1
        closest = min(
            (flight.deadline for flight in outstanding.values()),
            default=time.monotonic() + 0.02,
        )
        packet, source = _receive(
            sock, min(0.05, max(0.001, closest - time.monotonic())), stats
        )
        if source == peer and packet is not None and packet.kind == Kind.ACK:
            flight = outstanding.pop(packet.seq, None)
            if flight is not None:
                rtt.sample(time.monotonic() - flight.first_sent, flight.attempts > 1)
                aimd.ack()
                acknowledged.add(packet.seq)
                stats.acked += 1
                last_progress = time.monotonic()
                while base in acknowledged:
                    acknowledged.remove(base)
                    base += 1
                _emit(callback, Event("ACK", packet.seq))
        now = time.monotonic()
        expired = [
            (seq, flight)
            for seq, flight in outstanding.items()
            if flight.deadline <= now
        ]
        if expired:
            if aimd.loss(expired[0][0], next_seq):
                stats.timeouts += 1
                _emit(callback, Event("TIMEOUT", expired[0][0]))
            rtt.backoff()
            for seq, flight in expired:
                if flight.attempts >= settings.retries:
                    raise TimeoutError(f"DATA seq {seq} retry limit reached")
                channel.sendto(Packet(Kind.DATA, seq, flight.payload).encode(), peer)
                flight.sent = now
                flight.deadline = now + rtt.rto
                flight.attempts += 1
                stats.sent += 1
                stats.retransmissions += 1
                _emit(callback, Event("RESEND", seq))
        stats.cwnd = aimd.cwnd
        stats.srtt_ms = rtt.srtt * 1000
        stats.rto_ms = rtt.rto * 1000
        if time.monotonic() - last_progress > settings.timeouts:
            raise TimeoutError("No DATA acknowledgement within inactivity timeout")
