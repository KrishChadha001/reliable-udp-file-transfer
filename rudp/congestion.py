"""Simplified AIMD and RFC-6298-style adaptive retransmission timeout."""

import math


class Rtt:
    def __init__(self):
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rto = 0.5
        self.initialized = False

    def sample(self, seconds: float, retransmitted: bool):
        if retransmitted or seconds <= 0:
            return  # Karn: cannot tell which transmission generated this ACK.
        if not self.initialized:
            self.srtt, self.rttvar = seconds, seconds / 2
            self.initialized = True
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - seconds)
            self.srtt = 0.875 * self.srtt + 0.125 * seconds
        self.rto = max(0.1, min(3.0, self.srtt + max(0.001, 4 * self.rttvar)))

    def backoff(self):
        self.rto = min(3.0, self.rto * 2)


class Aimd:
    def __init__(self, initial: float = 4.0, maximum: int = 64):
        if not math.isfinite(initial) or not 1 <= initial <= maximum:
            raise ValueError("Initial cwnd must be finite and between 1 and window")
        self.cwnd = float(initial)
        self.maximum = maximum
        self.recovery_end = -1

    @property
    def limit(self):
        return max(1, int(self.cwnd))

    def ack(self):
        self.cwnd = min(self.maximum, self.cwnd + 1 / self.cwnd)

    def loss(self, seq: int, next_unsent: int) -> bool:
        if seq < self.recovery_end:
            return False  # Losses in the same flight are one congestion event.
        self.cwnd = max(1.0, self.cwnd / 2)
        self.recovery_end = next_unsent
        return True
