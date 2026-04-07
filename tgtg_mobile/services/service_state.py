import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger("tgtg_mobile")

DEFAULT_STATE = {
    "known_items": {},
    "last_poll": 0,
    "auth_error": False,
    "service_running": False,
    "last_bag_alert": 0,
}


class ServiceState:
    """Shared JSON state file between background service and Kivy app."""

    def __init__(self, path: str | None = None):
        if path is None:
            path = str(Path(__file__).parent.parent / "service_state.json")
        self.path = path

    def read(self) -> dict[str, Any]:
        """Returns current state with defaults for missing keys."""
        if not os.path.exists(self.path):
            return dict(DEFAULT_STATE)
        try:
            with open(self.path, "r") as f:
                data = json.load(f)
            return {**DEFAULT_STATE, **data}
        except (json.JSONDecodeError, IOError) as e:
            log.error("Failed to read service state: %s", e)
            return dict(DEFAULT_STATE)

    def update(self, known_items: dict[str, float] | None = None, last_poll: float | None = None) -> None:
        """Updates state file. Only updates provided fields."""
        state = self.read()
        if known_items is not None:
            state["known_items"] = known_items
        if last_poll is not None:
            state["last_poll"] = last_poll
        self._write(state)

    def set_auth_error(self, value: bool) -> None:
        """Sets the auth error flag."""
        state = self.read()
        state["auth_error"] = value
        self._write(state)

    def set_service_running(self, value: bool) -> None:
        """Sets the service running flag."""
        state = self.read()
        state["service_running"] = value
        self._write(state)

    def update_last_bag_alert(self, timestamp: float) -> None:
        """Updates the last bag alert timestamp."""
        state = self.read()
        state["last_bag_alert"] = timestamp
        self._write(state)

    def get_new_items_since(self, timestamp: float) -> list[str]:
        """Returns item IDs first seen after the given timestamp."""
        state = self.read()
        return [
            item_id
            for item_id, seen_at in state["known_items"].items()
            if seen_at > timestamp
        ]

    def _write(self, state: dict[str, Any]) -> None:
        """Writes state to file atomically."""
        try:
            tmp_path = self.path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_path, self.path)
        except IOError as e:
            log.error("Failed to write service state: %s", e)
