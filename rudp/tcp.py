"""Length-delimited TCP baseline with the same file and integrity rules."""

import hashlib
import socket
import struct
import time
from .common import (
    Metrics,
    OutputFile,
    VERSION,
    control,
    parse_control,
    hash_stream,
    open_shared,
    make_meta,
    validate_request,
    validate_meta,
    packet_count,
    error_control,
)
from .protocol import CHUNK, ProtocolError


def read_exact(sock, n):
    chunks = []
    while n:
        data = sock.recv(min(n, 65536))
        if not data:
            raise ConnectionError("TCP peer closed before the message was complete")
        chunks.append(data)
        n -= len(data)
    return b"".join(chunks)


def send_control(sock, obj):
    raw = control(obj)
    sock.sendall(struct.pack("!I", len(raw)) + raw)


def recv_control(sock):
    length = struct.unpack("!I", read_exact(sock, 4))[0]
    if not 1 <= length <= CHUNK:
        raise ProtocolError("Invalid TCP control-frame length")
    return parse_control(read_exact(sock, length))


def serve(cfg, ui):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((cfg.bind, cfg.port))
        listener.listen(1)
        listener.settimeout(0.5)
        print(f"LISTENING TCP {cfg.bind}:{cfg.port} | share={cfg.root}", flush=True)
        while True:
            try:
                conn, peer = listener.accept()
            except socket.timeout:
                continue
            m = Metrics("tcp", "sender", retransmissions=None, retransmission_pct=None)
            request = {}
            started = time.monotonic()
            with conn:
                conn.settimeout(cfg.timeout)
                try:
                    request = recv_control(conn)
                    validate_request(request)
                    m.filename = request["file"]
                    source, size = open_shared(cfg.root, request["file"])
                    with source:
                        meta = make_meta(cfg, request, size, hash_stream(source))
                        m.bytes = size
                        m.sha256 = meta["sha256"]
                        m.packets = packet_count(size)
                        send_control(conn, meta)
                        done = 0
                        while done < size:
                            block = source.read(min(65536, size - done))
                            if not block:
                                raise OSError("Source file changed or read failed")
                            conn.sendall(block)
                            done += len(block)
                            ui.update(done, size, m)
                        completion = recv_control(conn)
                        if (
                            completion.get("id") != meta["id"]
                            or completion.get("sha256") != m.sha256
                            or completion.get("bytes") != size
                            or completion.get("success") is not True
                        ):
                            raise ProtocolError("Receiver did not verify TCP file")
                        send_control(conn, dict(id=meta["id"], success=True))
                        m.success = True
                except (OSError, ValueError) as exc:
                    m.error = str(exc)
                    try:
                        send_control(conn, error_control(request.get("id"), exc))
                    except OSError:
                        pass
                finally:
                    m.finish(started)
                    m.save(cfg.metrics)
                    ui.summary(m)
            if cfg.once:
                return m


def get(cfg, m, ui, started):
    import secrets

    request = dict(
        version=VERSION, id=secrets.token_hex(16), file=cfg.file, window=cfg.window
    )
    m.retransmissions = None
    m.retransmission_pct = None
    with OutputFile(cfg.output, cfg.force) as output:
        with socket.create_connection(
            (socket.gethostbyname(cfg.host), cfg.port), timeout=cfg.timeout
        ) as conn:
            conn.settimeout(cfg.timeout)
            send_control(conn, request)
            meta = recv_control(conn)
            if "error" in meta:
                raise ProtocolError(str(meta["error"]))
            validate_meta(meta, request)
            h = hashlib.sha256()
            done = 0
            while done < meta["size"]:
                block = read_exact(conn, min(65536, meta["size"] - done))
                output.stream.write(block)
                h.update(block)
                done += len(block)
                ui.update(done, meta["size"], m)
            digest = h.hexdigest()
            if digest != meta["sha256"]:
                raise ProtocolError("TCP SHA-256 mismatch; incomplete output removed")
            output.publish()
            send_control(
                conn, dict(id=meta["id"], success=True, sha256=digest, bytes=done)
            )
            # Same boundary as UDP: verified output + receiver completion send.
            m.bytes = done
            m.packets = packet_count(done)
            m.sha256 = digest
            m.success = True
            m.finish(started)
            confirmed = recv_control(conn)
            if (
                confirmed.get("id") != meta["id"]
                or confirmed.get("success") is not True
            ):
                raise ProtocolError("TCP sender did not confirm completion")
