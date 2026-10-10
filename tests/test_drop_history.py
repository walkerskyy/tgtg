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
    assert h.in_predicted_window("123", datetime(2026, 10, 5, 0, 5))


def test_persistence_roundtrip(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    h.record("123", 1000)
    h.record("123", 2000)
    h2 = DropHistory(str(tmp_path))
    assert h2._events["123"] == [1000, 2000]
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


def test_corrupt_shapes_quarantined_not_crash(tmp_path: Path):
    for content in ('{"123": [null]}', '{"123": [[1]]}', '{"123": [1e999]}'):
        sub = tmp_path / f"case_{abs(hash(content))}"
        sub.mkdir()
        (sub / DROP_HISTORY_FILE).write_text(content)
        h = DropHistory(str(sub))  # must not raise
        assert h._events == {}
        assert list(sub.glob(f"{DROP_HISTORY_FILE}.corrupt-*"))


def test_save_leaves_no_tmp_file(tmp_path: Path):
    h = DropHistory(str(tmp_path))
    h.record("123", 1000)
    assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / DROP_HISTORY_FILE).is_file()


def test_no_persist_dir(tmp_path: Path):
    h = DropHistory(None)
    h.record("123", 1000)  # must not raise
    assert not h.in_predicted_window("999")
