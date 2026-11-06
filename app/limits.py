"""Process-wide and per-session limits for provider extraction.

The bucket key is a random session tag, never the API key.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from app.config import EXTRACT_MAX_PER_MINUTE, EXTRACT_MIN_INTERVAL_SECONDS


class ExtractionRateLimiter:
    """Reject bursts without storing anything about the document or the key."""

    def __init__(
        self,
        *,
        min_interval: float,
        max_per_minute: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval = min_interval
        self.max_per_minute = max_per_minute
        self._clock = clock
        self._lock = threading.Lock()
        self._hits: list[float] = []
        self._last_by_session: dict[str, float] = {}

    def check(self, session_id: str) -> str | None:
        """Return a user-facing message when the caller should wait."""

        now = self._clock()
        with self._lock:
            last = self._last_by_session.get(session_id)
            if last is not None and now - last < self.min_interval:
                return "Wait a few seconds before extracting again."
            cutoff = now - 60.0
            self._hits = [hit for hit in self._hits if hit >= cutoff]
            if len(self._hits) >= self.max_per_minute:
                return "Too many extraction requests. Wait a minute and try again."
            self._hits.append(now)
            self._last_by_session[session_id] = now
            self._trim_sessions()
            return None

    def _trim_sessions(self) -> None:
        if len(self._last_by_session) <= 2000:
            return
        oldest = sorted(self._last_by_session.items(), key=lambda item: item[1])[:1000]
        for key, _stamp in oldest:
            del self._last_by_session[key]


extraction_limiter = ExtractionRateLimiter(
    min_interval=EXTRACT_MIN_INTERVAL_SECONDS,
    max_per_minute=EXTRACT_MAX_PER_MINUTE,
)
