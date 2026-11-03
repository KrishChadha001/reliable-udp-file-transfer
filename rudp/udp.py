"""Real UDP sockets: Selective Repeat, per-packet timers and simplified AIMD."""

from dataclasses import dataclass
import hashlib
import secrets
import socket
import time
from .common import (
    Config,
    Metrics,
    OutputFile,
    VERSION,
    control,
    parse_control,
    open_shared,
    hash_stream,
    make_meta,
    packet_count,
    udp_socket,
    validate_request,
    validate_meta,
    validate_final,
    error_control,
)
from .congestion import Aimd, Rtt
from .protocol import (
    CHUNK,
    META_ACK,
    FIN_ACK,
    Kind,
    Packet,
    ProtocolError,
    ReceiveWindow,
)
from .ui import Progress


def send_packet(sock, peer, packet):
    sock.sendto(packet.encode(), peer)


def receive_packet(sock, wait, metrics):
    sock.settimeout(max(0.001, wait))
    try:
        raw, peer = sock.recvfrom(65535)
    except socket.timeout:
        return None, None
    try:
        return Packet.decode(raw), peer
    except ProtocolError:
        metrics.corrupt_packets += 1
        return None, peer


def error_packet(request_id, message):
    return Packet(Kind.ERROR, 0, control(error_control(request_id, message)))


def exchange(sock, peer, packet, ack_seq, session, cfg, metrics, ui):
    """Reliable stop-and-wait for META and FIN; DATA uses Selective Repeat."""
    expected = session.encode("ascii")
    for attempt in range(cfg.retries):
        send_packet(sock, peer, packet)
        ui.event("control_send" if not attempt else "control_retry", packet.seq)
        deadline = time.monotonic() + cfg.control_timeout
        while time.monotonic() < deadline:
            got, src = receive_packet(sock, deadline - time.monotonic(), metrics)
            if src != peer or got is None:
                continue
            if got.kind == Kind.ACK and got.seq == ack_seq and got.payload == expected:
                return
    raise TimeoutError(f"{packet.kind.name} acknowledgement retry limit reached")


@dataclass
class Flight:
    payload: bytes
    sent: float
    deadline: float
    attempts: int = 1


def send_file(sock, peer, source, meta, cfg, metrics, ui):
    session = meta["id"]
    metrics.filename = meta["file"]
    metrics.bytes = meta["size"]
    metrics.sha256 = meta["sha256"]
    metrics.packets = packet_count(meta["size"])
    exchange(
        sock,
        peer,
        Packet(Kind.META, 0, control(meta)),
        META_ACK,
        session,
        cfg,
        metrics,
        ui,
    )
    window = meta["window"]
    aimd = Aimd(min(cfg.cwnd, window), window)
    rtt = Rtt()
    base = next_seq = acked_bytes = 0
    outstanding: dict[int, Flight] = {}
    acknowledged = set()
    last_progress = time.monotonic()
    ui.event("start", 0, aimd.cwnd, 0, rtt.srtt, rtt.rto, 0)
    while base < metrics.packets:
        while (
            next_seq < metrics.packets
            and next_seq < base + window
            and len(outstanding) < aimd.limit
        ):
            length = min(CHUNK, meta["size"] - next_seq * CHUNK)
            payload = source.read(length)
            if len(payload) != length:
                raise OSError("Source file changed or read failed")
            timestamp = time.monotonic()
            send_packet(sock, peer, Packet(Kind.DATA, next_seq, payload))
            outstanding[next_seq] = Flight(payload, timestamp, timestamp + rtt.rto)
            ui.event(
                "send",
                next_seq,
                aimd.cwnd,
                len(outstanding),
                rtt.srtt,
                rtt.rto,
                metrics.retransmissions,
            )
            next_seq += 1
        closest = min(
            (f.deadline for f in outstanding.values()), default=time.monotonic() + 0.02
        )
        packet, src = receive_packet(
            sock, max(0.001, min(0.05, closest - time.monotonic())), metrics
        )
        if (
            src == peer
            and packet
            and packet.kind == Kind.ACK
            and packet.payload == session.encode("ascii")
        ):
            flight = outstanding.pop(packet.seq, None)
            if flight is not None:
                rtt.sample(time.monotonic() - flight.sent, flight.attempts > 1)
                aimd.ack()
                acknowledged.add(packet.seq)
                acked_bytes += len(flight.payload)
                last_progress = time.monotonic()
                while base in acknowledged:
                    acknowledged.remove(base)
                    base += 1
                ui.event(
                    "ack",
                    packet.seq,
                    aimd.cwnd,
                    len(outstanding),
                    rtt.srtt,
                    rtt.rto,
                    metrics.retransmissions,
                )
        timestamp = time.monotonic()
        expired = [
            (seq, f) for seq, f in outstanding.items() if f.deadline <= timestamp
        ]
        if expired:
            for seq, flight in expired:
                if flight.attempts >= cfg.retries:
                    raise TimeoutError(f"DATA seq {seq} retry limit reached")
                if aimd.loss(seq, next_seq):
                    metrics.congestion_events += 1
            rtt.backoff()  # One RTO backoff per timeout batch.
            for seq, flight in expired:
                send_packet(sock, peer, Packet(Kind.DATA, seq, flight.payload))
                flight.sent = timestamp
                flight.deadline = timestamp + rtt.rto
                flight.attempts += 1
                metrics.retransmissions += 1
                ui.event(
                    "timeout",
                    seq,
                    aimd.cwnd,
                    len(outstanding),
                    rtt.srtt,
                    rtt.rto,
                    metrics.retransmissions,
                )
        metrics.cwnd = aimd.cwnd
        metrics.srtt_ms = rtt.srtt * 1000
        metrics.rto_ms = rtt.rto * 1000
        ui.update(acked_bytes, meta["size"], metrics)
        if timestamp - last_progress > cfg.timeout:
            raise TimeoutError("No new DATA acknowledgement within inactivity timeout")
    metrics.cwnd = aimd.cwnd
    metrics.srtt_ms = rtt.srtt * 1000
    metrics.rto_ms = rtt.rto * 1000
    final = dict(
        id=session,
        bytes=metrics.bytes,
        packets=metrics.packets,
        retransmissions=metrics.retransmissions,
        congestion_events=metrics.congestion_events,
        cwnd=metrics.cwnd,
        srtt_ms=metrics.srtt_ms,
        rto_ms=metrics.rto_ms,
    )
    exchange(
        sock,
        peer,
        Packet(Kind.FIN, 0, control(final)),
        FIN_ACK,
        session,
        cfg,
        metrics,
        ui,
    )
    metrics.success = True
    # Optional success notice ends receiver linger early. Loss of all notices
    # is harmless: the receiver still serves duplicate FINs for the full horizon.
    for _ in range(3):
        try:
            send_packet(
                sock,
                peer,
                Packet(Kind.ACK, FIN_ACK, session.encode("ascii") + b":done"),
            )
        except OSError:
            pass


def serve(cfg: Config, ui: Progress):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind((cfg.bind, cfg.port))
        print(f"LISTENING UDP {cfg.bind}:{cfg.port} | share={cfg.root}", flush=True)
        while True:
            metrics = Metrics("udp", "sender")
            packet, peer = receive_packet(listener, 0.5, metrics)
            if packet is None or packet.kind != Kind.REQUEST:
                continue
            started = time.monotonic()
            request = {}
            try:
                request = parse_control(packet.payload)
                validate_request(request)
                metrics.filename = request["file"]
                source, size = open_shared(cfg.root, request["file"])
                with source, udp_socket(cfg.bind) as transfer:
                    meta = make_meta(cfg, request, size, hash_stream(source))
                    send_file(transfer, peer, source, meta, cfg, metrics, ui)
            except (OSError, ValueError) as exc:
                metrics.error = str(exc)
                try:
                    send_packet(listener, peer, error_packet(request.get("id"), exc))
                except OSError:
                    pass  # A disconnected requester must not stop the service.
            finally:
                metrics.finish(started)
                metrics.save(cfg.metrics)
                ui.summary(metrics)
            if cfg.once:
                return metrics
            # Drain duplicate REQUESTs queued during the completed session.
            listener.setblocking(False)
            try:
                while True:
                    raw, other = listener.recvfrom(65535)
                    try:
                        queued = Packet.decode(raw)
                        obj = parse_control(queued.payload)
                        if queued.kind == Kind.REQUEST and obj.get("id") != request.get(
                            "id"
                        ):
                            send_packet(
                                listener,
                                other,
                                error_packet(
                                    obj.get("id"), "Sender busy; retry request"
                                ),
                            )
                    except ProtocolError:
                        pass
            except BlockingIOError:
                pass
            finally:
                listener.setblocking(True)


def get(cfg: Config, metrics: Metrics, ui: Progress, started: float):
    request = dict(
        version=VERSION, id=secrets.token_hex(16), file=cfg.file, window=cfg.window
    )
    server = (socket.gethostbyname(cfg.host), cfg.port)
    with OutputFile(cfg.output, cfg.force) as output, udp_socket() as sock:
        meta = None
        peer = None
        for _ in range(cfg.retries):
            send_packet(sock, server, Packet(Kind.REQUEST, 0, control(request)))
            deadline = time.monotonic() + cfg.control_timeout
            while time.monotonic() < deadline:
                packet, src = receive_packet(sock, deadline - time.monotonic(), metrics)
                if packet is None or src[0] != server[0]:
                    continue
                if packet.kind not in (Kind.META, Kind.ERROR):
                    continue
                try:
                    obj = parse_control(packet.payload)
                except ProtocolError:
                    continue
                if obj.get("request") != request["id"]:
                    continue
                if packet.kind == Kind.ERROR:
                    raise ProtocolError(
                        str(obj.get("error", "Server rejected request"))
                    )
                validate_meta(obj, request)
                meta = obj
                peer = src
                break
            if meta is not None:
                break
        if meta is None:
            raise TimeoutError(
                "No metadata response; check host, port, protocol and sender"
            )
        session = meta["id"].encode("ascii")
        send_packet(sock, peer, Packet(Kind.ACK, META_ACK, session))
        total = packet_count(meta["size"])
        window = ReceiveWindow(meta["window"], total, meta["size"])
        digest = hashlib.sha256()
        received = 0
        last_packet = time.monotonic()
        metrics.packets = total
        while True:
            packet, src = receive_packet(sock, min(0.2, cfg.timeout), metrics)
            timestamp = time.monotonic()
            if src == peer and packet:
                if packet.kind == Kind.META:
                    # ACK may have been lost before the sender started DATA.
                    try:
                        duplicate = parse_control(packet.payload)
                    except ProtocolError:
                        duplicate = {}
                    if duplicate.get("id") == meta["id"]:
                        send_packet(sock, peer, Packet(Kind.ACK, META_ACK, session))
                        last_packet = timestamp
                elif packet.kind == Kind.DATA:
                    duplicate = packet.seq < window.base or packet.seq in window.buffer
                    accepted, ready = window.accept(packet.seq, packet.payload)
                    if accepted:
                        last_packet = timestamp
                        if duplicate:
                            metrics.duplicates += 1
                        for block in ready:
                            output.stream.write(block)
                            digest.update(block)
                            received += len(block)
                        metrics.max_buffered = max(
                            metrics.max_buffered, len(window.buffer)
                        )
                        send_packet(sock, peer, Packet(Kind.ACK, packet.seq, session))
                        ui.event("receive", packet.seq, inflight=len(window.buffer))
                        ui.update(received, meta["size"], metrics)
                elif packet.kind == Kind.FIN:
                    try:
                        final = parse_control(packet.payload)
                    except ProtocolError:
                        continue
                    if final.get("id") != meta["id"]:
                        continue
                    if window.base != total or received != meta["size"]:
                        raise ProtocolError("Premature FIN; file is incomplete")
                    actual = digest.hexdigest()
                    if actual != meta["sha256"]:
                        raise ProtocolError(
                            "SHA-256 mismatch; incomplete output removed"
                        )
                    validate_final(final, meta["id"], received, total)
                    output.publish()
                    metrics.bytes = received
                    metrics.sha256 = actual
                    metrics.success = True
                    for key in (
                        "retransmissions",
                        "congestion_events",
                        "cwnd",
                        "srtt_ms",
                        "rto_ms",
                    ):
                        setattr(metrics, key, final.get(key))
                    send_packet(sock, peer, Packet(Kind.ACK, FIN_ACK, session))
                    metrics.finish(
                        started
                    )  # Excludes post-completion duplicate-FIN linger.
                    ui.event("verified", total)
                    # Re-ACK duplicate FIN when the first final ACK is lost.
                    linger = time.monotonic() + max(
                        1.0, (meta["control_retries"] + 1) * meta["control_timeout"]
                    )
                    while time.monotonic() < linger:
                        try:
                            again, other = receive_packet(
                                sock, min(0.1, linger - time.monotonic()), metrics
                            )
                        except ConnectionResetError:
                            continue
                        if (
                            other == peer
                            and again
                            and again.kind == Kind.ACK
                            and again.seq == FIN_ACK
                            and again.payload == session + b":done"
                        ):
                            break
                        if other == peer and again and again.kind == Kind.FIN:
                            try:
                                same = (
                                    parse_control(again.payload).get("id") == meta["id"]
                                )
                            except ProtocolError:
                                same = False
                            if same:
                                send_packet(
                                    sock, peer, Packet(Kind.ACK, FIN_ACK, session)
                                )
                    return
            if timestamp - last_packet > cfg.timeout:
                raise TimeoutError(
                    "Receiver inactivity timeout; incomplete output removed"
                )
