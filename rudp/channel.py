"""A small UDP fault injector used by integration tests and demonstrations."""

import random
import socket
import threading
from typing import Optional


class LossyChannel:
    """Apply repeatable loss, duplication, corruption, and delay to UDP sends."""

    def __init__(
        self,
        sock: socket.socket,
        loss: float = 0.0,
        duplicate: float = 0.0,
        corrupt: float = 0.0,
        delay_ms: float = 0.0,
        jitter_ms: float = 0.0,
        seed: Optional[int] = None,
    ):
        """Wrap a socket while keeping receive operations on the real socket."""
        self.socket = sock
        self.loss = self._probability(loss, "loss")
        self.duplicate = self._probability(duplicate, "duplicate")
        self.corrupt = self._probability(corrupt, "corrupt")
        if delay_ms < 0 or jitter_ms < 0:
            raise ValueError("Delay and jitter must not be negative")
        self.delay_ms = delay_ms
        self.jitter_ms = jitter_ms
        self.random = random.Random(seed)
        self._timers: set[threading.Timer] = set()
        self._lock = threading.Lock()
        self._closed = False

    @staticmethod
    def _probability(value: float, name: str) -> float:
        """Convert a percentage into a checked probability used by the injector."""
        if not 0 <= value <= 100:
            raise ValueError(f"{name} must be between 0 and 100")
        return value / 100

    def sendto(self, data: bytes, address) -> int:
        """Schedule zero, one, or two datagrams and return the input byte count."""
        with self._lock:
            if self._closed:
                raise OSError("LossyChannel is closed")
            if self.random.random() < self.loss:
                return len(data)
            copies = 2 if self.random.random() < self.duplicate else 1
            for _ in range(copies):
                packet = self._corrupt(data)
                delay = max(
                    0.0,
                    (self.delay_ms + self.random.uniform(-self.jitter_ms, self.jitter_ms))
                    / 1000,
                )
                self._schedule(packet, address, delay)
            return len(data)

    def _corrupt(self, data: bytes) -> bytes:
        """Flip one byte when corruption is selected, leaving empty ACKs unchanged."""
        if not data or self.random.random() >= self.corrupt:
            return data
        packet = bytearray(data)
        index = self.random.randrange(len(packet))
        packet[index] ^= 1 << self.random.randrange(8)
        return bytes(packet)

    def _schedule(self, data: bytes, address, delay: float):
        """Deliver immediately or through a cancellable timer for reordering."""
        if delay == 0:
            self.socket.sendto(data, address)
            return
        timer = threading.Timer(delay, self._deliver, args=(data, address))
        timer.daemon = True
        self._timers.add(timer)
        timer.start()

    def _deliver(self, data: bytes, address):
        """Send a delayed datagram and remove its timer from the pending set."""
        try:
            with self._lock:
                if not self._closed:
                    self.socket.sendto(data, address)
        finally:
            with self._lock:
                for timer in tuple(self._timers):
                    if not timer.is_alive():
                        self._timers.discard(timer)

    def close(self):
        """Cancel delayed sends and close the wrapped UDP socket."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            timers = tuple(self._timers)
            self._timers.clear()
        for timer in timers:
            timer.cancel()
        self.socket.close()
