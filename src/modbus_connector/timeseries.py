"""Ограниченный ряд (t, value) для графиков и спарклайнов; без Qt."""

from collections import deque
from collections.abc import Iterator

MAX_SAMPLES = 20000


class TimeSeries:
    """Кольцевой буфер отсчётов (time.monotonic(), value)."""

    def __init__(self, maxlen: int = MAX_SAMPLES) -> None:
        self._times: deque[float] = deque(maxlen=maxlen)
        self._values: deque[float] = deque(maxlen=maxlen)

    def append(self, t: float, value: float) -> None:
        self._times.append(t)
        self._values.append(value)

    def clear(self) -> None:
        self._times.clear()
        self._values.clear()

    def __len__(self) -> int:
        return len(self._times)

    def __iter__(self) -> Iterator[tuple[float, float]]:
        return zip(self._times, self._values, strict=True)

    def points(self) -> tuple[list[float], list[float]]:
        return list(self._times), list(self._values)

    def stats(self, t0: float, t1: float) -> tuple[float, float, float] | None:
        """(min, max, avg) по отсчётам в [t0, t1]; None, если отсчётов нет."""
        values = [v for t, v in self if t0 <= t <= t1]
        if not values:
            return None
        return min(values), max(values), sum(values) / len(values)
