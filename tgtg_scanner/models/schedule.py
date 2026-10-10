from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("tgtg")

WINDOW_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)-([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass
class Schedule:
    """Per-item auto-buy schedule. days: weekday 0-6 (Monday=0), None = any day.

    windows: local "HH:MM-HH:MM" strings (wrap-around allowed), empty = all day.
    """

    days: list[int] | None = None
    windows: list[str] = field(default_factory=list)

    @staticmethod
    def parse(data: object) -> Schedule | None:
        """Parse a schedule dict from reservations.json.

        Returns None when absent or malformed (malformed = treated as always eligible).
        """
        if data is None:
            return None
        if not isinstance(data, dict):
            log.warning("Malformed schedule (not an object): %r - ignoring", data)
            return None
        days = data.get("days")
        if days is not None:
            if not isinstance(days, list) or not all(isinstance(d, int) and 0 <= d <= 6 for d in days):
                log.warning("Malformed schedule days: %r - ignoring schedule", days)
                return None
            if not days:
                days = None
        windows_raw = data.get("windows")
        windows: list[str] = []
        if windows_raw is not None:
            if not isinstance(windows_raw, list):
                log.warning("Malformed schedule windows: %r - ignoring schedule", windows_raw)
                return None
            for w in windows_raw:
                if _parse_window(w) is None:
                    log.warning("Malformed schedule window: %r - ignoring schedule", w)
                    return None
                windows.append(w)  # type: ignore[arg-type]
        return Schedule(days=days, windows=windows)

    def is_now(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        if self.days is not None and now.weekday() not in self.days:
            return False
        if not self.windows:
            return True
        minute = now.hour * 60 + now.minute
        for w in self.windows:
            parsed = _parse_window(w)
            if parsed is not None and _window_contains(parsed[0], parsed[1], minute):
                return True
        return False


def _parse_window(value: object) -> tuple[int, int] | None:
    if not isinstance(value, str):
        return None
    m = WINDOW_RE.match(value.strip())
    if not m:
        return None
    h1, m1, h2, m2 = (int(g) for g in m.groups())
    return (h1 * 60 + m1, h2 * 60 + m2)


def _window_contains(start: int, end: int, minute: int) -> bool:
    if start <= end:
        return start <= minute <= end
    return minute >= start or minute <= end
