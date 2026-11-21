"""Explicit clocks for offline orchestration tests."""

from datetime import datetime, timedelta


class FakeClock:
    def __init__(self, now: datetime) -> None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("fake clock requires an aware datetime")
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("fake clock cannot move backwards")
        self._now += timedelta(seconds=seconds)
