import json
import logging
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger("tgtg")

DROP_HISTORY_FILE = "drop_history.json"
MAX_EVENTS = 100
SLOT_MINUTES = 15
SLOTS_PER_DAY = 96  # 24 * 60 / 15
MIN_EVENTS_FOR_PREDICTION = 2
DEDUPE_WINDOW = 5  # seconds; collapse the transition + lost-race double signal


class DropHistory:
    """Timestamp log of observed drop events per item, persisted as JSON.

    Prediction buckets events in memory by (weekday, 15-min slot); the file stays
    a dumb per-item list of epoch timestamps (capped at MAX_EVENTS).
    """

    def __init__(self, persist_dir: str | None = None) -> None:
        self._events: dict[str, list[int]] = {}
        self._persist_path = Path(persist_dir) / DROP_HISTORY_FILE if persist_dir else None
        self._load()

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.is_file():
            return
        try:
            with self._persist_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError(f"expected object, got {type(data).__name__}")
            self._events = {
                str(item_id): [int(ts) for ts in timestamps][-MAX_EVENTS:]
                for item_id, timestamps in data.items()
                if isinstance(timestamps, list)
            }
        except (json.JSONDecodeError, OSError, ValueError, TypeError, OverflowError) as err:
            log.error("Failed to load drop history: %s", err)
            self._quarantine()

    def _quarantine(self) -> None:
        if self._persist_path and self._persist_path.is_file():
            target = self._persist_path.with_name(f"{self._persist_path.name}.corrupt-{int(time.time())}")
            try:
                self._persist_path.rename(target)
                log.warning("Quarantined corrupt drop history to %s", target)
            except OSError as err:
                log.error("Failed to quarantine %s: %s", self._persist_path, err)
        self._events = {}

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._persist_path.with_name(f"{self._persist_path.name}.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(self._events, f, indent=2)
            tmp_path.replace(self._persist_path)
        except OSError as err:
            log.error("Failed to save drop history: %s", err)

    def record(self, item_id: str, timestamp: int | None = None) -> None:
        """Record a drop event (observed transition or lost race) for an item.

        Events for the same item within DEDUPE_WINDOW of the previous one are
        skipped: a lost race fires both signals milliseconds apart.
        """
        ts = int(timestamp if timestamp is not None else time.time())
        events = self._events.setdefault(item_id, [])
        if events and 0 <= ts - events[-1] < DEDUPE_WINDOW:
            return
        events.append(ts)
        del events[:-MAX_EVENTS]
        self._save()

    def in_predicted_window(self, item_id: str, now: datetime | None = None) -> bool:
        """True when the current (weekday, 15-min slot) or an adjacent slot has >= 2 events."""
        events = self._events.get(item_id)
        if not events:
            return False
        now = now or datetime.now()
        slot = (now.hour * 60 + now.minute) // SLOT_MINUTES
        weekday = now.weekday()
        counts: dict[tuple[int, int], int] = {}
        for ts in events:
            dt = datetime.fromtimestamp(ts)
            key = (dt.weekday(), (dt.hour * 60 + dt.minute) // SLOT_MINUTES)
            counts[key] = counts.get(key, 0) + 1
        for s in (slot, (slot - 1) % SLOTS_PER_DAY, (slot + 1) % SLOTS_PER_DAY):
            if counts.get((weekday, s), 0) >= MIN_EVENTS_FOR_PREDICTION:
                return True
        return False
