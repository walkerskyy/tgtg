# Smart Reserve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add purchase-confirmation tracking, per-item buy schedules, and learned fast polling to the scanner's existing reservation-queue flow (spec: `docs/superpowers/specs/2026-10-10-smart-reserve-design.md`).

**Architecture:** Single-threaded dynamic-sleep polling. The existing run loop computes a shorter sleep (~8-12s) when a queued item is inside a learned drop window or an unpaid order is being watched, and runs a reduced "fast job" (one `get_favorites()` call + one round-robin queued non-favorite + order-status checks) instead of the full job. Order lifecycle after `create_order` is tracked via `get_order_status`: `CONFIRMED` → purchased notification; stuck `RESERVED` past ~4.5 min → one unpaid warning. Schedule gating and drop history live in the queue/state JSON files next to `reservations.json`.

**Tech Stack:** Python 3.10+, stdlib only for new models (json/pathlib/dataclasses/datetime), pytest + pytest-mock + responses for tests. Run everything with `poetry run`.

**Key existing facts (read before coding):**
- `tgtg_scanner/models/reservations.py` — `Reservations` holds `reservation_query: list[Reservation]` + `active_orders: dict[str, Order]`, persists to `reservations.json`, tolerates legacy array format in `_load()`.
- `tgtg_scanner/scanner.py` — `_job()` loads items, `monitor.observe(item)` returns True on 0→available transition, then `_attempt_reservation(item)` runs only if `is_queued`. `run()` sleeps `config.sleep_time * (0.9 + 0.2 * random())` inside the cron gate.
- `tgtg_scanner/notifiers/telegram.py:226` — `isinstance(item, Reservation)` → hardcoded text "is reserved for 5 minutes". Line 308 calls `reservations.update_active_orders()` (to be replaced).
- `Config` dataclass in `tgtg_scanner/models/config.py` with per-section `BaseConfig` subclasses that have `_read_ini`/`_read_env` (see `TelegramConfig` for the boolean pattern). Wire new sections in BOTH branches of `__post_init__` (ini at ~line 573, env at ~line 589).
- Tests: `tests/test_reservations.py` fixture `reservations()` uses `MagicMock()` client; `tests/test_scanner.py` fixture patches `TgtgClient.login` and `Metrics`.
- **Existing test to update:** `tests/test_reservations.py::test_update_active_orders` asserts old behavior (drop any non-`RESERVED`) — this task deliberately changes it.
- Lint: `make lint` (ruff line-length 130, mypy). Tests: `make test` (excludes `tgtg_api`).

---

### Task 1: Schedule model

**Files:**
- Create: `tgtg_scanner/models/schedule.py`
- Test: `tests/test_schedule.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schedule.py`:

```python
from datetime import datetime

from tgtg_scanner.models.schedule import Schedule


def test_parse_none():
    assert Schedule.parse(None) is None


def test_parse_valid_full():
    s = Schedule.parse({"days": [0, 1, 2, 3, 4], "windows": ["16:00-20:00"]})
    assert s is not None
    assert s.days == [0, 1, 2, 3, 4]
    assert s.windows == ["16:00-20:00"]


def test_parse_empty_dict_gives_always_eligible():
    s = Schedule.parse({})
    assert s is not None
    assert s.days is None
    assert s.windows == []


def test_parse_malformed_days_returns_none():
    assert Schedule.parse({"days": [7, 8]}) is None
    assert Schedule.parse({"days": "monday"}) is None
    assert Schedule.parse({"days": []}) is not None  # empty list = no restriction


def test_parse_malformed_window_returns_none():
    assert Schedule.parse({"windows": ["25:00-26:00"]}) is None
    assert Schedule.parse({"windows": ["16:00"]}) is None
    assert Schedule.parse({"windows": [123]}) is None


def test_is_now_day_gating():
    s = Schedule.parse({"days": [0]})  # Monday only
    monday = datetime(2026, 10, 5, 12, 0)  # 2026-10-05 is a Monday
    tuesday = datetime(2026, 10, 6, 12, 0)
    assert s.is_now(monday)
    assert not s.is_now(tuesday)


def test_is_now_window_gating():
    s = Schedule.parse({"windows": ["16:00-20:00"]})
    assert s.is_now(datetime(2026, 10, 5, 17, 0))
    assert not s.is_now(datetime(2026, 10, 5, 15, 59))
    assert not s.is_now(datetime(2026, 10, 5, 20, 1))


def test_is_now_wrap_around_window():
    s = Schedule.parse({"windows": ["22:00-02:00"]})
    assert s.is_now(datetime(2026, 10, 5, 23, 0))
    assert s.is_now(datetime(2026, 10, 5, 1, 0))
    assert not s.is_now(datetime(2026, 10, 5, 12, 0))


def test_is_now_no_restrictions():
    s = Schedule.parse({})
    assert s.is_now(datetime(2026, 10, 5, 3, 0))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_schedule.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgtg_scanner.models.schedule'`

- [ ] **Step 3: Write the implementation**

Create `tgtg_scanner/models/schedule.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_schedule.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/models/schedule.py tests/test_schedule.py
git commit -m "feat: add Schedule model for per-item auto-buy gating"
```

---

### Task 2: DropHistory model

**Files:**
- Create: `tgtg_scanner/models/drop_history.py`
- Test: `tests/test_drop_history.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_drop_history.py`:

```python
import json
from datetime import datetime
from pathlib import Path

from tgtg_scanner.models.drop_history import DROP_HISTORY_FILE, MAX_EVENTS, DropHistory


def _ts(weekday: int, hour: int, minute: int) -> int:
    """Epoch timestamp for a Monday-based weekday at a fixed week (2026-10-05 is a Monday)."""
    from datetime import timedelta

    base = datetime(2026, 10, 5, 0, 0)  # Monday
    dt = base + timedelta(days=weekday, hours=hour, minutes=minute)
    return int(dt.timestamp())


def test_record_and_predict_single_event_not_enough(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    h.record("123", _ts(0, 17, 0))
    assert not h.in_predicted_window("123", datetime(2026, 10, 5, 17, 5))


def test_record_and_predict_two_events_same_slot(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    h.record("123", _ts(0, 17, 0))  # Monday 17:00
    h.record("123", _ts(0, 17, 5))  # Monday 17:05 (same 15-min slot)
    assert h.in_predicted_window("123", datetime(2026, 10, 5, 17, 7))
    assert not h.in_predicted_window("123", datetime(2026, 10, 5, 12, 0))


def test_adjacent_slot_counts(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    h.record("123", _ts(0, 17, 0))  # slot for 17:00
    h.record("123", _ts(0, 17, 0))
    # 16:55 is in the previous slot -> adjacent -> still in window
    assert h.in_predicted_window("123", datetime(2026, 10, 5, 16, 55))


def test_slot_wrap_around_same_weekday(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    h.record("123", _ts(0, 23, 50))  # slot 95
    h.record("123", _ts(0, 23, 55))  # slot 95
    # midnight+5 is slot 0, adjacent to 95
    assert h.in_predicted_window("123", datetime(2026, 10, 6, 0, 5))


def test_persistence_roundtrip(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    h.record("123", 1000)
    h.record("123", 2000)
    h2 = DropHistory(str(tmp_path))
    assert h2.in_predicted_window.__self__ is h2  # loaded instance
    data = json.loads((tmp_path / DROP_HISTORY_FILE).read_text())
    assert data["123"] == [1000, 2000]


def test_trim_to_max_events(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    for i in range(MAX_EVENTS + 25):
        h.record("123", i)
    data = json.loads((tmp_path / DROP_HISTORY_FILE).read_text())
    assert len(data["123"]) == MAX_EVENTS
    assert data["123"][-1] == MAX_EVENTS + 24


def test_corrupt_file_quarantined(tmp_path: Path):
    path = tmp_path / DROP_HISTORY_FILE
    path.write_text("{not json")
    h = DropHistory(str(tmp_path))
    assert h._events == {}
    quarantined = list(tmp_path.glob(f"{DROP_HISTORY_FILE}.corrupt-*"))
    assert len(quarantined) == 1


def test_no_persist_dir(tmp_path: Path):
    h = DropHistory(None)
    h.record("123", 1000)  # must not raise
    assert not h.in_predicted_window("999")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_drop_history.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgtg_scanner.models.drop_history'`

- [ ] **Step 3: Write the implementation**

Create `tgtg_scanner/models/drop_history.py`:

```python
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
        except (json.JSONDecodeError, OSError, ValueError) as err:
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
            with self._persist_path.open("w", encoding="utf-8") as f:
                json.dump(self._events, f, indent=2)
        except OSError as err:
            log.error("Failed to save drop history: %s", err)

    def record(self, item_id: str, timestamp: int | None = None) -> None:
        """Record a drop event (observed transition or lost race) for an item."""
        ts = int(timestamp if timestamp is not None else time.time())
        events = self._events.setdefault(item_id, [])
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_drop_history.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/models/drop_history.py tests/test_drop_history.py
git commit -m "feat: add DropHistory with slot-based drop prediction"
```

---

### Task 3: Reservations data model — new fields, schedule, back-compat, corrupt recovery

**Files:**
- Modify: `tgtg_scanner/models/reservations.py` (`Order`, `Reservation`, `_load`, `_save`)
- Test: `tests/test_reservations.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_reservations.py`:

```python
def test_reservation_with_schedule_and_amount_roundtrip(tmp_path: Path):
    mock_client = MagicMock()
    r1 = Reservations(mock_client, str(tmp_path))
    schedule = Schedule.parse({"days": [1, 2], "windows": ["16:00-20:00"]})
    r1.reservation_query.append(Reservation("123", 2, "Test Item", schedule=schedule))
    r1._save()

    r2 = Reservations(mock_client, str(tmp_path))
    loaded = r2.reservation_query[0]
    assert loaded.amount == 2
    assert loaded.schedule is not None
    assert loaded.schedule.days == [1, 2]
    assert loaded.schedule.windows == ["16:00-20:00"]


def test_order_with_status_created_at_roundtrip(tmp_path: Path):
    mock_client = MagicMock()
    r1 = Reservations(mock_client, str(tmp_path))
    r1.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=123)
    r1._save()

    r2 = Reservations(mock_client, str(tmp_path))
    o = r2.active_orders["o1"]
    assert o.status == "RESERVED"
    assert o.created_at == 123


def test_legacy_array_load_defaults(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text('[{"item_id": "1", "amount": 1, "display_name": "A"}]')
    r = Reservations(mock_client, str(tmp_path))
    assert len(r.reservation_query) == 1
    assert r.reservation_query[0].schedule is None
    assert r.reservation_query[0].amount == 1


def test_entry_missing_amount_defaults_to_1(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text(
        '{"reservation_query": [{"item_id": "1", "display_name": "A"}], "active_orders": []}'
    )
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query[0].amount == 1


def test_malformed_schedule_treated_as_no_schedule(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text(
        '{"reservation_query": [{"item_id": "1", "amount": 1, "display_name": "A", '
        '"schedule": {"windows": ["bogus"]}}], "active_orders": []}'
    )
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query[0].schedule is None


def test_corrupt_reservations_file_quarantined(tmp_path: Path):
    mock_client = MagicMock()
    path = tmp_path / "reservations.json"
    path.write_text("{broken")
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query == []
    assert list(tmp_path.glob("reservations.json.corrupt-*"))


def test_get_reservation(reservations: Reservations):
    reservations.reserve("123", "Test Item")
    got = reservations.get_reservation("123")
    assert got is not None
    assert got.item_id == "123"
    assert reservations.get_reservation("999") is None


def test_register_order_sets_status_and_created_at(reservations: Reservations):
    reservations.register_order("o1", "123", 1, "Test")
    o = reservations.active_orders["o1"]
    assert o.status == "RESERVED"
    assert isinstance(o.created_at, int)


def test_make_order_creates_order_with_status(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    reservations.client.create_order.return_value = {"id": "ord_1"}  # type: ignore[attr-defined]
    reservations.reserve("123", "Test Item")
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock)
    o = reservations.active_orders["ord_1"]
    assert o.status == "RESERVED"
    assert isinstance(o.created_at, int)


def test_make_orders_respects_schedule(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    schedule = Schedule.parse({"days": [0], "windows": ["00:00-00:01"]})
    schedule.is_now = lambda now=None: False  # type: ignore[method-assign]
    reservations.reservation_query = [Reservation("123", 1, "Test", schedule=schedule)]
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock)
    reservations.client.create_order.assert_not_called()  # type: ignore[attr-defined]
    callback_mock.assert_not_called()
    assert len(reservations.reservation_query) == 1  # stays queued
```

Also update the imports at the top of `tests/test_reservations.py`:

```python
from tgtg_scanner.models.reservations import Order, Reservation, Reservations
from tgtg_scanner.models.schedule import Schedule
from pathlib import Path
```

(`Path` may already be imported — keep one copy.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_reservations.py`
Expected: new tests FAIL (`Reservation() got an unexpected keyword argument 'schedule'` / `get_reservation` missing / etc.)

- [ ] **Step 3: Implement model changes**

In `tgtg_scanner/models/reservations.py`:

Add imports at top (after existing imports):

```python
import time

from tgtg_scanner.models.schedule import Schedule
```

Replace the `Order` and `Reservation` dataclasses with:

```python
UNPAID_WARNING = "UNPAID_WARNING"
PAYMENT_DEADLINE = 270  # seconds; TGTG reservation hold is ~5 minutes
SOLD_OUT_MARKERS = ("sold out", "no longer available", "not available", "out of stock")


def is_sold_out_error(err: Exception) -> bool:
    """Heuristic: does this create_order failure mean we lost the race?"""
    msg = str(err).lower()
    return any(marker in msg for marker in SOLD_OUT_MARKERS)


@dataclass
class Order:
    id: str
    item_id: str
    amount: int
    display_name: str
    status: str | None = None
    created_at: int | None = None


@dataclass
class Reservation:
    item_id: str
    amount: int
    display_name: str
    schedule: Schedule | None = None
    status: str | None = None
```

Replace `_load` body's parsing with helper-based parsing (replace the whole `_load` method):

```python
    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.is_file():
            return
        try:
            with self._persist_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, (list, dict)):
                raise ValueError(f"expected list or object, got {type(data).__name__}")
            if isinstance(data, list):
                entries, orders = data, []
            else:
                entries, orders = data.get("reservation_query", []), data.get("active_orders", [])
            self.reservation_query = [_reservation_from_dict(r) for r in entries]
            self.active_orders = {o["id"]: _order_from_dict(o) for o in orders}
            log.info(
                "Loaded %d reservations + %d orders from %s",
                len(self.reservation_query),
                len(self.active_orders),
                self._persist_path,
            )
        except (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError) as err:
            log.error("Failed to load reservations: %s", err)
            self._quarantine()

    def _quarantine(self) -> None:
        if self._persist_path and self._persist_path.is_file():
            target = self._persist_path.with_name(f"{self._persist_path.name}.corrupt-{int(time.time())}")
            try:
                self._persist_path.rename(target)
                log.warning("Quarantined corrupt reservations file to %s", target)
            except OSError as err:
                log.error("Failed to quarantine %s: %s", self._persist_path, err)
        self.reservation_query = []
        self.active_orders = {}
```

Add module-level parsers (after the dataclasses, before `RESERVATIONS_FILE`):

```python
def _reservation_from_dict(data: dict) -> Reservation:
    return Reservation(
        item_id=data["item_id"],
        amount=int(data.get("amount", 1)),
        display_name=data.get("display_name", ""),
        schedule=Schedule.parse(data.get("schedule")),
    )


def _order_from_dict(data: dict) -> Order:
    return Order(
        id=data["id"],
        item_id=data.get("item_id", ""),
        amount=int(data.get("amount", 1)),
        display_name=data.get("display_name", ""),
        status=data.get("status"),
        created_at=data.get("created_at"),
    )
```

Update `register_order` to set status/created_at:

```python
    def register_order(self, order_id: str, item_id: str, amount: int, display_name: str) -> None:
        self.active_orders[order_id] = Order(order_id, item_id, amount, display_name, status="RESERVED", created_at=int(time.time()))
        self._save()
```

Add `get_reservation` (after `is_queued`):

```python
    def get_reservation(self, item_id: str) -> Reservation | None:
        """Return the first queued reservation for item_id, or None."""
        for r in self.reservation_query:
            if r.item_id == item_id:
                return r
        return None
```

Update `_create_order` to set status/created_at (replace the Order construction inside it):

```python
        res = self.client.create_order(reservation.item_id, reservation.amount)
        order_id = res.get("id")
        if order_id:
            self.active_orders[order_id] = Order(
                order_id,
                reservation.item_id,
                reservation.amount,
                reservation.display_name,
                status="RESERVED",
                created_at=int(time.time()),
            )
            self._save()
            log.info("Order created for %s (order_id=%s)", reservation.display_name, order_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_reservations.py`
Expected: all PASS (including previously existing ones — `Reservation(**r)` legacy constructions still work because new fields have defaults; the old `_load` is gone but its tests go through the new parsers).

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/models/reservations.py tests/test_reservations.py
git commit -m "feat: extend reservation model with schedule, order status, corrupt recovery"
```

---

### Task 4: Purchase lifecycle — process_active_orders

**Files:**
- Modify: `tgtg_scanner/models/reservations.py` (replace `update_active_orders`)
- Modify: `tgtg_scanner/notifiers/telegram.py:308` (call site)
- Test: `tests/test_reservations.py` (replace `test_update_active_orders`)

- [ ] **Step 1: Write the failing tests**

In `tests/test_reservations.py`, **delete** the old `test_update_active_orders` and append:

```python
def test_process_confirmed_order_notifies_and_keeps(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "CONFIRMED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert [o.id for o in notified] == ["o1"]
    assert notified[0].status == "CONFIRMED"
    assert "o1" in reservations.active_orders  # kept in ledger


def test_process_reserved_order_no_notification_before_deadline(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert reservations.active_orders["o1"].status == "RESERVED"


def test_process_unpaid_warning_fires_once_past_deadline(reservations: Reservations):
    old = int(time.time()) - 300
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=old)
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert len(notified) == 1
    assert notified[0].status == UNPAID_WARNING
    assert reservations.active_orders["o1"].status == UNPAID_WARNING
    # second call: no repeat warning
    notified2 = reservations.process_active_orders()
    assert notified2 == []


def test_process_terminal_state_removes_and_notifies(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "CANCELED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert len(notified) == 1
    assert notified[0].status == "CANCELED"
    assert "o1" not in reservations.active_orders


def test_process_status_error_skips_order(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert "o1" in reservations.active_orders


def test_confirmed_order_pruned_when_leaves_active(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="CONFIRMED", created_at=int(time.time()))
    reservations.client.get_active.return_value = {"orders": []}  # type: ignore[attr-defined]
    reservations.process_active_orders()
    assert "o1" not in reservations.active_orders


def test_confirmed_order_kept_while_still_active(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="CONFIRMED", created_at=int(time.time()))
    reservations.client.get_active.return_value = {"orders": [{"id": "o1"}]}  # type: ignore[attr-defined]
    reservations.process_active_orders()
    assert "o1" in reservations.active_orders
```

Update the imports line to include `UNPAID_WARNING`:

```python
import time

from tgtg_scanner.models.reservations import UNPAID_WARNING, Order, Reservation, Reservations
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_reservations.py -k process`
Expected: FAIL with `AttributeError: 'Reservations' object has no attribute 'process_active_orders'`

- [ ] **Step 3: Implement process_active_orders**

In `tgtg_scanner/models/reservations.py`, **replace** the entire `update_active_orders` method with:

```python
    def process_active_orders(self) -> list[Order]:
        """Check every tracked order's status and report transitions.

        - CONFIRMED (paid): notify once, keep in ledger; prune when it leaves get_active().
        - RESERVED past PAYMENT_DEADLINE: set UNPAID_WARNING and notify once.
        - Any other state (CANCELED/EXPIRED/...): notify and remove.

        Returns:
            list[Order]: orders that got an event this call (send these to notifiers).
        """
        changed = False
        events: list[Order] = []
        now = int(time.time())
        for order_id, order in list(self.active_orders.items()):
            try:
                res = self.client.get_order_status(order_id)
            except Exception as exc:
                log.warning("Failed to get status for order %s: %s", order_id, exc)
                continue
            state = res.get("state", "")
            if state == "CONFIRMED":
                if order.status != "CONFIRMED":
                    order.status = "CONFIRMED"
                    changed = True
                    events.append(order)
                    log.info("Order %s CONFIRMED (purchased) for %s", order_id, order.display_name)
            elif state == "RESERVED":
                if order.status == "RESERVED" and order.created_at and now - order.created_at >= PAYMENT_DEADLINE:
                    order.status = UNPAID_WARNING
                    changed = True
                    events.append(order)
                    log.warning("Order %s still unpaid for %s - sending warning", order_id, order.display_name)
            elif state:
                log.info("Order %s entered terminal state %s for %s", order_id, state, order.display_name)
                order.status = state
                events.append(order)
                del self.active_orders[order_id]
                changed = True
        if any(o.status == "CONFIRMED" for o in self.active_orders.values()):
            if self._prune_collected_orders():
                changed = True
        if changed:
            self._save()
        return events

    def _prune_collected_orders(self) -> bool:
        """Drop CONFIRMED orders that no longer appear in get_active(). Returns True when changed."""
        try:
            active = self.client.get_active()
            active_ids = {o.get("id") for o in active.get("orders", [])}
        except Exception as exc:
            log.warning("Failed to list active orders: %s", exc)
            return False
        pruned = False
        for order_id in [oid for oid, o in self.active_orders.items() if o.status == "CONFIRMED"]:
            if order_id not in active_ids:
                log.info("Confirmed order %s no longer active - removing from ledger", order_id)
                del self.active_orders[order_id]
                pruned = True
        return pruned
```

Also update `make_orders` to respect the schedule — inside the loop, after `item = state.get(...)`, change the condition block to:

```python
        for reservation in list(self.reservation_query):
            if reservation.schedule and not reservation.schedule.is_now():
                continue
            item = state.get(reservation.item_id)
            if item and item.items_available > 0 and item.reservation_status is None:
                log.info("Processing queued reservation for %s", reservation.display_name)
                try:
                    self._create_order(reservation)
                    item.reservation_status = "reserved"
                    item.reservation_amount = reservation.amount
                    callback(reservation)
                    self.remove(reservation.item_id)
                    log.info("Reservation completed for %s and removed from queue", reservation.display_name)
                except Exception as exc:
                    log.warning("Order failed for %s: %s", reservation.display_name, exc)
```

In `tgtg_scanner/notifiers/telegram.py`, replace line ~308:

```python
        self.reservations.update_active_orders()
```

with:

```python
        self.reservations.process_active_orders()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_reservations.py tests/test_notifiers.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/models/reservations.py tgtg_scanner/notifiers/telegram.py tests/test_reservations.py
git commit -m "feat: track order purchase lifecycle (confirmed/unpaid warning/terminal)"
```

---

### Task 5: Config — ReservationConfig.fast_poll

**Files:**
- Modify: `tgtg_scanner/models/config.py`
- Test: `tests/test_config.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (following the style of existing env/ini tests in that file):

```python
def test_fast_poll_default():
    config = Config(disable_tests=True)
    assert config.reservation.fast_poll is True


def test_fast_poll_env(monkeypatch):
    monkeypatch.setenv("RESERVATION_FAST_POLL", "false")
    config = Config(disable_tests=True)
    assert config.reservation.fast_poll is False


def test_fast_poll_ini(tmp_path, monkeypatch):
    import os

    ini = tmp_path / "config.ini"
    ini.write_text("[RESERVATION]\nFastPoll = false\n")
    monkeypatch.delenv("RESERVATION_FAST_POLL", raising=False)
    config = Config(file=str(ini), disable_tests=True)
    assert config.reservation.fast_poll is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_config.py -k fast_poll`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'reservation'`

- [ ] **Step 3: Implement ReservationConfig**

In `tgtg_scanner/models/config.py`, add (place near `TelegramConfig`, i.e. with the other section configs):

```python
@dataclass
class ReservationConfig(BaseConfig):
    """Reservation / smart-reserve settings."""

    fast_poll: bool = True

    def _read_ini(self, parser: configparser.ConfigParser):
        self._ini_get_boolean(parser, "RESERVATION", "FastPoll", "fast_poll")

    def _read_env(self):
        self._env_get_boolean("RESERVATION_FAST_POLL", "fast_poll")
```

In the `Config` dataclass, add the field next to `price_monitoring`:

```python
    price_monitoring: bool = False
    reservation: ReservationConfig = field(default_factory=ReservationConfig)
```

In `Config.__post_init__`, add to the **ini** branch (after `self.telegram._read_ini(parser)`):

```python
            self.reservation._read_ini(parser)
```

and to the **env** branch (after `self.telegram._read_env()`):

```python
            self.reservation._read_env()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_config.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/models/config.py tests/test_config.py
git commit -m "feat: add RESERVATION FastPoll config (ini + env)"
```

---

### Task 6: Scanner — schedule gate, amount, drop recording, sold-out matcher

**Files:**
- Modify: `tgtg_scanner/scanner.py` (`__init__`, `_attempt_reservation`, `_job`)
- Test: `tests/test_scanner.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scanner.py` (keep existing imports; add `from tgtg_scanner.models.schedule import Schedule`):

```python
def test_auto_reserve_uses_queue_amount(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name, amount=2)
    mock_create_order = mocker.patch.object(scanner.tgtg_client, "create_order", return_value={"id": "ord_1"})
    mocker.patch.object(scanner.reservations, "register_order")

    scanner._attempt_reservation(test_item)

    mock_create_order.assert_called_once_with(test_item.item_id, 2)
    assert test_item.reservation_amount == 2


def test_auto_reserve_outside_schedule_skips(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Reservation

    schedule = Schedule.parse({"days": [0], "windows": ["00:00-00:01"]})
    schedule.is_now = lambda now=None: False  # type: ignore[method-assign]
    scanner.reservations.reservation_query = [Reservation(test_item.item_id, 1, test_item.display_name, schedule=schedule)]
    mock_create_order = mocker.patch.object(scanner.tgtg_client, "create_order")

    scanner._attempt_reservation(test_item)

    mock_create_order.assert_not_called()
    assert test_item.reservation_status == "skipped_schedule"


def test_auto_reserve_inside_schedule_proceeds(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    schedule = Schedule.parse({"days": [0], "windows": ["00:00-00:01"]})
    schedule.is_now = lambda now=None: True  # type: ignore[method-assign]
    from tgtg_scanner.models.reservations import Reservation

    scanner.reservations.reservation_query = [Reservation(test_item.item_id, 1, test_item.display_name, schedule=schedule)]
    mocker.patch.object(scanner.tgtg_client, "create_order", return_value={"id": "ord_1"})
    mock_register = mocker.patch.object(scanner.reservations, "register_order")

    scanner._attempt_reservation(test_item)

    mock_register.assert_called_once()
    assert test_item.reservation_status == "reserved"


def test_lost_race_records_drop_event(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mocker.patch.object(scanner.tgtg_client, "create_order", side_effect=TgtgAPIError(400, b"Item is sold out"))
    mock_record = mocker.patch.object(scanner.drop_history, "record")

    scanner._attempt_reservation(test_item)

    mock_record.assert_called_once_with(test_item.item_id)


def test_non_sold_out_error_records_nothing(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mocker.patch.object(scanner.tgtg_client, "create_order", side_effect=TgtgAPIError(403, b"captcha challenge"))
    mock_record = mocker.patch.object(scanner.drop_history, "record")

    scanner._attempt_reservation(test_item)

    mock_record.assert_not_called()


def test_job_records_drop_on_transition(scanner: Scanner, test_item: Item, tgtg_item: dict, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    # StockMonitor.observe returns False on first sight - pre-seed with an unavailable snapshot
    scanner.monitor.state[test_item.item_id] = Item(dict(tgtg_item, items_available=0))
    mocker.patch.object(scanner, "_load_items", return_value=[test_item])
    mocker.patch.object(scanner.tgtg_client, "create_order", side_effect=TgtgAPIError(400, b"Item is sold out"))
    mocker.patch.object(scanner, "_send_messages")
    mocker.patch.object(scanner, "_save_tokens")
    scanner.notifiers = mocker.MagicMock()
    mock_record = mocker.patch.object(scanner.drop_history, "record")

    scanner._job()

    mock_record.assert_called_once_with(test_item.item_id)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_scanner.py`
Expected: new tests FAIL (`'Scanner' object has no attribute 'drop_history'`, amount still 1, etc.)

- [ ] **Step 3: Implement scanner changes**

In `tgtg_scanner/scanner.py`:

Add imports:

```python
from tgtg_scanner.models.drop_history import DropHistory
from tgtg_scanner.models.reservations import is_sold_out_error
```

In `__init__`, after `self.reservations = Reservations(...)` (line ~66):

```python
        self.drop_history = DropHistory(reservations_dir)
```

**Replace** `_attempt_reservation` entirely:

```python
    def _attempt_reservation(self, item: Item) -> None:
        reservation = self.reservations.get_reservation(item.item_id)
        if reservation is None:
            return
        if reservation.schedule and not reservation.schedule.is_now():
            log.info("Outside schedule for %s - skipping reservation", item.display_name)
            item.reservation_status = "skipped_schedule"
            return
        amount = reservation.amount or 1
        log.info("Reserving %d bag(s) for %s", amount, item.display_name)
        try:
            if self.tgtg_client.session:
                self.tgtg_client.session.last_api_request = None
            order = self.tgtg_client.create_order(item.item_id, amount)
            item.reservation_status = "reserved"
            item.reservation_amount = amount
            self.reservations.register_order(order["id"], item.item_id, amount, item.display_name)
            log.info("Reserved %d bag(s) for %s (order_id=%s)", amount, item.display_name, order["id"])
        except TgtgAPIError as err:
            item.reservation_status = "failed"
            item.reservation_error = str(err)
            log.warning("Reservation failed for %s: %s", item.display_name, err)
            if is_sold_out_error(err):
                self.drop_history.record(item.item_id)
```

**Replace** `_job` entirely (extract shared item processing so Task 8 can reuse it):

```python
    def _job(self) -> None:
        """Job iterates over all monitored items."""
        if self.notifiers is None:
            raise RuntimeError("Notifiers not initialized!")

        self._process_items(self._load_items())

        amounts = {item_id: item.items_available for item_id, item in self.monitor.state.items()}
        log.debug("new State: %s", amounts)
        self.reservations.make_orders(self.monitor.state, self.notifiers.send)

        if not self.monitor.state:
            log.warning("No items in observation! Did you add any favorites?")

        self._process_order_events()
        self._save_tokens()

    def _process_items(self, items: list[Item]) -> None:
        """Observe items, attempt reservations on transitions, send notifications."""
        for item in items:
            if self.monitor.observe(item):
                if self.reservations.is_queued(item.item_id):
                    self.drop_history.record(item.item_id)
                self._attempt_reservation(item)
                if item.reservation_status == "reserved":
                    self.reservations.remove(item.item_id)
                    if self.notifiers:
                        self.notifiers.send(ReservationObj(item.item_id, item.reservation_amount or 1, item.display_name, status="reserved"))
                self._send_messages(item)
                self.metrics.send_notifications.labels(item.item_id, item.display_name).inc()
            self.metrics.update(item)
```

Note: the `ReservationObj(...)` construction now passes `status="reserved"` — requires Task 3's `status` field on `Reservation` (done).

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_scanner.py tests/test_reservations.py`
Expected: all PASS (existing auto-reserve tests still pass: unqueued item → `get_reservation` returns None → early return; queued success/failure paths unchanged in observable behavior)

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/scanner.py tests/test_scanner.py
git commit -m "feat: scanner honors queue amount, schedule gating, drop recording"
```

---

### Task 7: Scanner — order-event notifications each cycle

**Files:**
- Modify: `tgtg_scanner/scanner.py` (add `_process_order_events`, `_notify_order_event`)
- Test: `tests/test_scanner.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scanner.py`:

```python
def test_process_order_events_sends_reservation_notifications(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Order

    order = Order("o1", "123", 1, "Bakery", status="CONFIRMED", created_at=1)
    scanner.notifiers = mocker.MagicMock()
    mocker.patch.object(scanner.reservations, "process_active_orders", return_value=[order])

    scanner._process_order_events()

    sent = scanner.notifiers.send.call_args[0][0]
    assert sent.item_id == "123"
    assert sent.status == "CONFIRMED"
    assert sent.display_name == "Bakery"


def test_job_calls_process_order_events(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_process_items")
    mocker.patch.object(scanner, "_save_tokens")
    scanner.notifiers = mocker.MagicMock()
    mocker.patch.object(scanner.reservations, "make_orders")
    mock_events = mocker.patch.object(scanner, "_process_order_events")

    scanner._job()

    mock_events.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_scanner.py -k order_event`
Expected: FAIL with `AttributeError: ... no attribute '_process_order_events'`

- [ ] **Step 3: Implement**

In `tgtg_scanner/scanner.py`, add (after `_process_items`):

```python
    def _process_order_events(self) -> None:
        """Poll tracked orders and notify on purchase/unpaid/terminal events."""
        for order in self.reservations.process_active_orders():
            self._notify_order_event(order)

    def _notify_order_event(self, order) -> None:
        if self.notifiers:
            self.notifiers.send(ReservationObj(order.item_id, order.amount, order.display_name, status=order.status))
```

(Import `Order` only if you prefer annotating `order: Order`; otherwise leave untyped — ruff config does not require annotations.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_scanner.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/scanner.py tests/test_scanner.py
git commit -m "feat: scanner sends purchase/unpaid/terminal order notifications"
```

---

### Task 8: Scanner — fast mode: prediction, fast job, round-robin, dynamic sleep

**Files:**
- Modify: `tgtg_scanner/scanner.py` (`__init__`, `_fast_mode_active`, `_fast_job`, `_next_sleep_time`, `run`)
- Test: `tests/test_scanner.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scanner.py`:

```python
def test_fast_mode_when_predicted_window(scanner: Scanner, mocker: MockerFixture):
    scanner.reservations.reserve("123", "Test")
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=True)
    assert scanner._fast_mode_active()


def test_fast_mode_when_unpaid_order_pending(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Order

    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    scanner.reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=1)
    assert scanner._fast_mode_active()


def test_fast_mode_off_without_queue_or_orders(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    assert not scanner._fast_mode_active()


def test_fast_mode_disabled_by_config(scanner: Scanner, mocker: MockerFixture):
    scanner.config.reservation.fast_poll = False
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=True)
    # _fast_mode_active still reports True (it's the signal); the gate is in run()/_next_sleep_time
    assert scanner._fast_mode_active()
    assert scanner._next_sleep_time() >= scanner.config.sleep_time * 0.9


def test_next_sleep_time_fast_range(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_fast_mode_active", return_value=True)
    for _ in range(20):
        assert 8.0 <= scanner._next_sleep_time() <= 12.0


def test_next_sleep_time_normal_range(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_fast_mode_active", return_value=False)
    for _ in range(20):
        assert scanner.config.sleep_time * 0.9 <= scanner._next_sleep_time() <= scanner.config.sleep_time * 1.1


def test_fast_job_favorites_and_one_non_favorite(scanner: Scanner, tgtg_item: dict, mocker: MockerFixture):
    fav = dict(tgtg_item, item_id="fav1")
    scanner.reservations.reserve("nf1", "NF One")
    scanner.reservations.reserve("nf2", "NF Two")
    scanner.reservations.reserve("fav1", "Fav")
    mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[fav])
    mock_get_item = mocker.patch.object(scanner.tgtg_client, "get_item", return_value=tgtg_item)
    mocker.patch.object(scanner, "_process_items")
    mocker.patch.object(scanner, "_process_order_events")
    mocker.patch.object(scanner, "_save_tokens")
    scanner.notifiers = mocker.MagicMock()

    scanner._fast_job()
    first = mock_get_item.call_args[0][0]
    scanner._fast_job()
    second = mock_get_item.call_args[0][0]

    assert {first, second} == {"nf1", "nf2"}
    assert mock_get_item.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_scanner.py -k "fast_mode or next_sleep or fast_job"`
Expected: FAIL with missing attributes

- [ ] **Step 3: Implement fast mode**

In `tgtg_scanner/scanner.py`:

Add imports:

```python
from datetime import datetime

from tgtg_scanner.models.reservations import UNPAID_WARNING
```

(`random` is already imported as `from random import random`.)

In `__init__`, after `self.drop_history = ...`:

```python
        self._rr_index = 0
```

Add methods (place after `_process_order_events`):

```python
    def _fast_mode_active(self) -> bool:
        """True when a queued item is in a predicted drop window or an unpaid order is pending."""
        now = datetime.now()
        for reservation in self.reservations.reservation_query:
            if self.drop_history.in_predicted_window(reservation.item_id, now):
                return True
        for order in self.reservations.active_orders.values():
            if order.status in ("RESERVED", UNPAID_WARNING):
                return True
        return False

    def _next_sleep_time(self) -> float:
        if self.config.reservation.fast_poll and self._fast_mode_active():
            return 8.0 + 4.0 * random()
        return self.config.sleep_time * (0.9 + 0.2 * random())

    def _fast_job(self) -> None:
        """Reduced cycle while fast mode is active."""
        if self.notifiers is None:
            raise RuntimeError("Notifiers not initialized!")
        try:
            items = [self._item_from_api(data) for data in self.tgtg_client.get_favorites()]
        except TgtgAPIError as err:
            log.error(err)
            items = []
        favorite_ids = {item.item_id for item in items}
        non_favorites = sorted(r.item_id for r in self.reservations.reservation_query if r.item_id not in favorite_ids)
        if non_favorites:
            item_id = non_favorites[self._rr_index % len(non_favorites)]
            self._rr_index += 1
            try:
                items.append(self._item_from_api(self.tgtg_client.get_item(item_id)))
            except TgtgAPIError as err:
                log.error(err)
        self._process_items(items)
        self._process_order_events()
        self._save_tokens()
```

In `run()`, replace the job dispatch and sleep computation. Replace:

```python
                try:
                    self._job()
                except Exception:
                    log.error("Job Error! - %s", sys.exc_info())
                finally:
                    sleep_time = self.config.sleep_time * (0.9 + 0.2 * random())
```

with:

```python
                try:
                    if self.config.reservation.fast_poll and self._fast_mode_active():
                        self._fast_job()
                    else:
                        self._job()
                except Exception:
                    log.error("Job Error! - %s", sys.exc_info())
                finally:
                    sleep_time = self._next_sleep_time()
```

(The `steps`/`activity` sleep loop below stays unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_scanner.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/scanner.py tests/test_scanner.py
git commit -m "feat: learned fast polling with dynamic sleep and reduced fast job"
```

---

### Task 9: Telegram status-aware reservation messages

**Files:**
- Modify: `tgtg_scanner/notifiers/telegram.py` (`_send`, new `_reservation_text`)
- Test: `tests/test_telegram_text.py` (new, pure-function test — no bot network)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_telegram_text.py`:

```python
from tgtg_scanner.models.reservations import UNPAID_WARNING, Reservation
from tgtg_scanner.notifiers.telegram import TelegramNotifier


def test_reserved_text_default():
    r = Reservation("1", 1, "Bakery", status="reserved")
    assert TelegramNotifier._reservation_text(r) == "Bakery is reserved for 5 minutes"


def test_reserved_text_no_status():
    r = Reservation("1", 1, "Bakery")
    assert TelegramNotifier._reservation_text(r) == "Bakery is reserved for 5 minutes"


def test_confirmed_text():
    r = Reservation("1", 1, "Bakery", status="CONFIRMED")
    assert "purchased" in TelegramNotifier._reservation_text(r)


def test_unpaid_warning_text():
    r = Reservation("1", 1, "Bakery", status=UNPAID_WARNING)
    text = TelegramNotifier._reservation_text(r)
    assert "UNPAID" in text
    assert "app" in text


def test_terminal_text():
    r = Reservation("1", 1, "Bakery", status="CANCELED")
    assert "CANCELED" in TelegramNotifier._reservation_text(r)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest -v tests/test_telegram_text.py`
Expected: FAIL with `AttributeError: type object 'TelegramNotifier' has no attribute '_reservation_text'`

- [ ] **Step 3: Implement**

In `tgtg_scanner/notifiers/telegram.py`:

Update the import (line ~36):

```python
from tgtg_scanner.models.reservations import UNPAID_WARNING, Order, Reservation
```

Add the static method to `TelegramNotifier` (near `_send`):

```python
    @staticmethod
    def _reservation_text(item: Reservation) -> str:
        if item.status == "CONFIRMED":
            return f"{item.display_name} purchased - just pick it up"
        if item.status == UNPAID_WARNING:
            return f"{item.display_name} RESERVED BUT UNPAID - open the TGTG app now, hold expires soon"
        if item.status and item.status != "reserved":
            return f"{item.display_name} order status: {item.status}"
        return f"{item.display_name} is reserved for 5 minutes"
```

In `_send`, replace the Reservation branch (line ~226-227):

```python
        elif isinstance(item, Reservation):
            message = escape_markdown(self._reservation_text(item), version=2)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest -v tests/test_telegram_text.py tests/test_notifiers.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/notifiers/telegram.py tests/test_telegram_text.py
git commit -m "feat: status-aware telegram reservation messages"
```

---

### Task 10: Docs + final verification

**Files:**
- Modify: `config.sample.ini` (new `[RESERVATION]` section + queue field docs)
- Modify: `wiki/Configuration.md` (FastPoll row + reservations.json fields)

- [ ] **Step 1: Document FastPoll in config.sample.ini**

Add after the `[MAIN]` section (near SleepTime):

```ini
[RESERVATION]
## Enable learned fast polling: when a queued item's historical drop time is near,
## the scanner polls every ~10 seconds instead of SleepTime (recommended).
## Fast = true
FastPoll = true
```

- [ ] **Step 2: Document in wiki/Configuration.md**

Add a `FastPoll` row to the configuration table (or a new `[RESERVATION]` subsection):

```
| FastPoll       | RESERVATION_FAST_POLL | enable learned ~10s polling in predicted drop windows          | `true`       |
```

And extend the existing reservations/`reservations.json` documentation (FAQ.md or Configuration.md wherever reservations.json is described) with:

```
Optional per-entry fields in reservations.json:
- amount: number of bags to buy (default 1)
- schedule: {"days": [0-6 Monday-based], "windows": ["HH:MM-HH:MM"]} - only auto-buy inside this schedule; omit for always eligible
```

- [ ] **Step 3: Full lint + test**

Run: `make lint`
Expected: PASS (ruff + mypy clean)

Run: `make test`
Expected: PASS — all suites green, `tgtg_api` tests excluded as usual.

- [ ] **Step 4: Manual smoke (optional but recommended)**

Run: `poetry run scanner -d --base_url http://localhost:8080` with `make server` running, queue an item via editing `reservations.json`, and observe logs for skip/reserve behavior. No assertions — log check only.

- [ ] **Step 5: Commit**

```bash
git add config.sample.ini wiki/Configuration.md
git commit -m "docs: document FastPoll config and reservations.json schedule/amount"
```

---

## Self-Review Notes (already applied)

- **Spec coverage:** purchase lifecycle (Task 4+7), schedule gating (Task 1+3+4+6), learning (Task 2+6), fast polling/dynamic sleep (Task 8), config kill switch (Task 5), telegram messages (Task 9), docs (Task 10), amount honoring (Task 3+6), corrupt-file recovery (Task 2+3). All spec requirements have tasks.
- **Type consistency:** `Reservation(item_id, amount, display_name, schedule=..., status=...)` used consistently; `Order(id, item_id, amount, display_name, status=..., created_at=...)`; `process_active_orders() -> list[Order]`; `DropHistory.record(item_id, timestamp=None)`; `Schedule.parse(data) -> Schedule | None` / `.is_now(now=None) -> bool`.
- **Back-compat:** legacy array `reservations.json` loads (Task 3 test); `Reservation(**r)` positional constructions in existing code still valid; `OnlyReservations` telegram filter untouched; existing scanner tests preserved.
