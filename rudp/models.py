"""Small data objects shared by the sender, receiver, CLI, and GUI."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Settings:
    """Hold protocol and fault-injection values without hiding networking rules."""

    window: int = 32
    loss: float = 0.0
    dup: float = 0.0
    corrupt: float = 0.0
    delay_ms: float = 0.0
    jitter_ms: float = 0.0
    seed: Optional[int] = None
    timeouts: float = 15.0
    retries: int = 20
    rto: float = 0.5


@dataclass
class Stats:
    """Expose counters and final measurements without coupling them to a UI."""

    bytes: int = 0
    packets: int = 0
    sent: int = 0
    acked: int = 0
    retransmissions: int = 0
    timeouts: int = 0
    duplicates: int = 0
    corrupt_dropped: int = 0
    max_buffered: int = 0
    cwnd: float = 1.0
    srtt_ms: float = 0.0
    rto_ms: float = 500.0
    elapsed: float = 0.0
    goodput: float = 0.0
    verified: bool = False
    error: str = ""


@dataclass(frozen=True)
class Event:
    """Describe one observable protocol action for logs and progress displays."""

    kind: str
    seq: int = -1
    info: str = ""
