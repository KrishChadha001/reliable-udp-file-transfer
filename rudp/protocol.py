"""Wire codec and bounded Selective Repeat receive window."""

from dataclasses import dataclass
from enum import IntEnum
import struct
import zlib

CHUNK = 1024
HEADER = struct.Struct("!BIHI")  # 1 + 4 + 2 + 4 = 11 bytes; network byte order


class Kind(IntEnum):
    """Packet kinds used by the transfer state machines."""

    META = 1
    META_ACK = 2
    DATA = 3
    ACK = 4
    FIN = 5
    FIN_ACK = 6


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Packet:
    kind: Kind
    seq: int = 0
    payload: bytes = b""

    def encode(self) -> bytes:
        """Build a datagram and calculate CRC-32 with its checksum field zeroed."""
        if len(self.payload) > CHUNK or not 0 <= self.seq <= 0xFFFFFFFF:
            raise ProtocolError("Packet size or sequence is outside the wire limits")
        try:
            kind = Kind(self.kind)
        except ValueError as exc:
            raise ProtocolError("Unknown packet type") from exc
        wire = HEADER.pack(kind, self.seq, len(self.payload), 0) + self.payload
        checksum = zlib.crc32(wire) & 0xFFFFFFFF
        return HEADER.pack(kind, self.seq, len(self.payload), checksum) + self.payload

    @classmethod
    def decode(cls, wire: bytes):
        """Validate length, packet type, and CRC-32 before exposing a datagram."""
        if not HEADER.size <= len(wire) <= HEADER.size + CHUNK:
            raise ProtocolError("Truncated or oversized datagram")
        kind, seq, length, checksum = HEADER.unpack_from(wire)
        if len(wire) != HEADER.size + length:
            raise ProtocolError("Payload length mismatch")
        expected = zlib.crc32(wire[:7] + b"\0" * 4 + wire[11:]) & 0xFFFFFFFF
        if expected != checksum:
            raise ProtocolError("CRC-32 mismatch")
        try:
            return cls(Kind(kind), seq, wire[HEADER.size :])
        except ValueError as exc:
            raise ProtocolError("Unknown packet type") from exc


class ReceiveWindow:
    """Buffer packets inside a Selective Repeat window until they are contiguous."""

    def __init__(self, window: int, packets: int, file_size: int):
        """Create a window whose packet lengths are checked against file metadata."""
        self.window = window
        self.packets = packets
        self.file_size = file_size
        self.base = 0
        self.buffer: dict[int, bytes] = {}

    def accept(self, seq: int, payload: bytes) -> tuple[bool, list[bytes]]:
        """Accept in-window data, re-ACK old data, and deliver contiguous chunks."""
        if not 0 <= seq < self.packets:
            return False, []
        expected = min(CHUNK, self.file_size - seq * CHUNK)
        if len(payload) != expected:
            return False, []
        if seq < self.base:  # Previously written duplicate: ACK it again.
            return True, []
        if seq >= self.base + self.window:
            return False, []
        self.buffer.setdefault(seq, payload)
        ready = []
        while self.base in self.buffer:
            ready.append(self.buffer.pop(self.base))
            self.base += 1
        return True, ready
