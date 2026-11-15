"""Traffic density: counts, congestion level and one-minute buckets."""
from __future__ import annotations
from collections import deque


def congestion_level(count: int, medium: int, high: int) -> str:
    return "high" if count >= high else "medium" if count >= medium else "low"


class StatsCollector:
    """Accumulates vehicle counts and produces a bucket each time a minute completes."""

    def __init__(self, medium: int, high: int, keep_minutes: int = 60):
        self.medium, self.high = medium, high
        self._minute = None
        self._counts: list[int] = []
        self.recent: deque = deque(maxlen=keep_minutes)   # rolling window of buckets
        self.current = 0

    def set_thresholds(self, medium: int, high: int):
        self.medium, self.high = medium, high

    def add(self, t: float, count: int):
        """Add a sample. Returns a finished bucket dict when a new minute starts, else None."""
        self.current = count
        minute = int(t // 60) * 60
        bucket = None
        if self._minute is None:
            self._minute = minute
        elif minute != self._minute:
            bucket = self._close()
            self._minute = minute
        self._counts.append(count)
        return bucket

    def _close(self):
        if not self._counts:
            return None
        avg = sum(self._counts) / len(self._counts)
        mx = max(self._counts)
        b = {"minute_ts": self._minute, "avg": round(avg, 2), "max": mx,
             "congestion": congestion_level(round(avg), self.medium, self.high)}
        self.recent.append(b)
        self._counts = []
        return b

    def flush(self):
        return self._close()

    @property
    def level(self) -> str:
        return congestion_level(self.current, self.medium, self.high)

    def rolling_average(self) -> float:
        if not self.recent:
            return float(self.current)
        return sum(b["avg"] for b in self.recent) / len(self.recent)
