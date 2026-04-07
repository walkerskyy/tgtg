import logging
import time

import pytest

from services.service_state import ServiceState


@pytest.fixture
def state_file(tmp_path):
    path = tmp_path / "test_service_state.json"
    return ServiceState(str(path))


def test_read_empty_state(state_file):
    """Reading a non-existent state file returns defaults."""
    state = state_file.read()
    assert state["known_items"] == {}
    assert state["last_poll"] == 0
    assert state["auth_error"] is False
    assert state["service_running"] is False


def test_update_and_read_state(state_file):
    """Writing state and reading it back returns the same data."""
    known_items = {"item_123": 1712000000, "item_456": 1712000100}
    state_file.update(known_items=known_items, last_poll=1712000200)

    state = state_file.read()
    assert state["known_items"] == known_items
    assert state["last_poll"] == 1712000200
    assert state["auth_error"] is False


def test_update_preserves_existing_keys(state_file):
    """Updating state replaces known_items dict but preserves other fields."""
    state_file.update(known_items={"item_1": 1000}, last_poll=1000)
    state_file.set_auth_error(True)
    state_file.update(known_items={"item_2": 2000}, last_poll=2000)

    state = state_file.read()
    # known_items replaced, but auth_error preserved
    assert "item_1" not in state["known_items"]
    assert "item_2" in state["known_items"]
    assert state["last_poll"] == 2000
    assert state["auth_error"] is True


def test_set_auth_error(state_file):
    """Setting auth error flag persists."""
    state_file.set_auth_error(True)
    state = state_file.read()
    assert state["auth_error"] is True

    state_file.set_auth_error(False)
    state = state_file.read()
    assert state["auth_error"] is False


def test_set_service_running(state_file):
    """Setting service running flag persists."""
    state_file.set_service_running(True)
    state = state_file.read()
    assert state["service_running"] is True


def test_get_new_items_since(state_file):
    """Returns items first seen after the given timestamp."""
    now = time.time()
    known_items = {
        "old_item": now - 600,  # 10 min ago
        "new_item": now - 60,   # 1 min ago
    }
    state_file.update(known_items=known_items, last_poll=now)

    # Items seen in last 5 minutes (300 seconds)
    new_items = state_file.get_new_items_since(now - 300)
    assert "new_item" in new_items
    assert "old_item" not in new_items


def test_get_new_items_since_empty(state_file):
    """Returns empty list when no items match."""
    now = time.time()
    state_file.update(known_items={"old": now - 600}, last_poll=now)

    new_items = state_file.get_new_items_since(now - 100)
    assert new_items == []


def test_rapid_read_write_safety(state_file):
    """Multiple rapid reads/writes don't corrupt the file."""
    for i in range(10):
        state_file.update(known_items={f"item_{i}": time.time()}, last_poll=time.time())
        state = state_file.read()
        assert f"item_{i}" in state["known_items"]


def test_read_corrupted_json(state_file, caplog):
    """Reading corrupted JSON returns defaults and logs error."""
    with open(state_file.path, "w") as f:
        f.write("{invalid json!!!")

    with caplog.at_level(logging.ERROR):
        state = state_file.read()
    assert state["known_items"] == {}
    assert state["last_poll"] == 0
    assert "Failed to read service state" in caplog.text


def test_update_last_bag_alert(state_file):
    """update_last_bag_alert persists the timestamp."""
    ts = time.time()
    state_file.update_last_bag_alert(ts)
    state = state_file.read()
    assert state["last_bag_alert"] == ts
