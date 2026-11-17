"""Textbook adaptive retransmission timeout and AIMD congestion control."""

import math


class Rtt:
    """Estimate RTT and RTO while ignoring samples from retransmitted packets."""

    def __init__(self):
        """Start with the required half-second timeout before the first sample."""
        self.srtt = 0.0
        self.rttvar = 0.0
        self.rto = 0.5
        self.initialized = False

    def sample(self, seconds: float, retransmitted: bool):
        """Update the estimator only when Karn's Rule makes the sample reliable."""
        if retransmitted or seconds <= 0:
            return
        if not self.initialized:
            self.srtt, self.rttvar = seconds, seconds / 2
            self.initialized = True
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - seconds)
            self.srtt = 0.875 * self.srtt + 0.125 * seconds
        self.rto = max(0.1, min(2.0, self.srtt + 4 * self.rttvar))

    def backoff(self):
        """Double RTO after a timeout, without exceeding the two-second cap."""
        self.rto = min(2.0, self.rto * 2)


class Aimd:
    """Grow a congestion window with slow start and additive increase."""

    def __init__(self, initial: float = 1.0, maximum: int = 64):
        """Start at one packet and use the receive window as the hard ceiling."""
        if not math.isfinite(initial) or not 1 <= initial <= maximum:
            raise ValueError("Initial cwnd must be finite and between 1 and window")
        self.cwnd = float(initial)
        self.maximum = maximum
        self.ssthresh = float(maximum)
        self.recovery_end = -1

    @property
    def limit(self):
        """Return the integer number of packets permitted in flight."""
        return max(1, int(self.cwnd))

    def ack(self):
        """Increase one packet in slow start or one cwnd fraction thereafter."""
        if self.cwnd < self.ssthresh:
            self.cwnd = min(self.maximum, self.cwnd + 1)
        else:
            self.cwnd = min(self.maximum, self.cwnd + 1 / self.cwnd)

    def loss(self, seq: int, next_unsent: int) -> bool:
        """Halve cwnd once per flight and ignore later losses in that flight."""
        if seq < self.recovery_end:
            return False
        self.ssthresh = max(self.cwnd / 2, 2.0)
        self.cwnd = min(self.maximum, self.ssthresh)
        self.recovery_end = next_unsent
        return True
