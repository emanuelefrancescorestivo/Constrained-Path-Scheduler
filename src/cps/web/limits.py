"""
Rate limits, in memory: enough for one server process, which is what the pilot
runs (docs/ROADMAP.md, D3). They bound what one address can make the server do
(read timetable links for strangers, replan), not what a determined attacker with
many addresses can; that needs the host's own protection.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable


class Limiter:
    """At most `limit` events per `seconds` for each key, over a sliding window.
    Keeps at most `max_keys` keys, forgetting the least recently seen."""

    def __init__(
        self,
        limit: int,
        seconds: float,
        *,
        max_keys: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.seconds = seconds
        self.max_keys = max_keys
        self.clock = clock
        self._seen: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            times = self._seen.pop(key, None) or deque()
            while times and times[0] <= now - self.seconds:
                times.popleft()
            allowed = len(times) < self.limit
            if allowed:
                times.append(now)
            self._seen[key] = times
            while len(self._seen) > self.max_keys:
                self._seen.popitem(last=False)
            return allowed
