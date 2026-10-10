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
