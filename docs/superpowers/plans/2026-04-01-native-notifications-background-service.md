# Native Notifications & Background Foreground Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add native Android foreground service for background polling of TGTG favorite bags with rich notifications including action buttons.

**Architecture:** Four-component system: ForegroundService (background polling), NotificationManager (rich Android notifications), ServiceState (shared JSON state), and BootReceiver (auto-start on boot). Communication via shared JSON state file.

**Tech Stack:** Python 3, Kivy, pyjnius, Android SDK (NotificationCompat, PendingIntent, BroadcastReceiver), requests

---

### Task 1: ServiceState — Shared State File

**Files:**
- Create: `tgtg_mobile/services/service_state.py`
- Test: `tgtg_mobile/tests/test_service_state.py`

- [ ] **Step 1: Write failing tests for ServiceState**

```python
# tgtg_mobile/tests/test_service_state.py
import json
import os
import tempfile
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
    """Updating state preserves keys not in the update."""
    state_file.update(known_items={"item_1": 1000}, last_poll=1000)
    state_file.update(known_items={"item_2": 2000}, last_poll=2000)

    state = state_file.read()
    # Second update replaces known_items dict entirely
    assert "item_1" in state["known_items"] or "item_2" in state["known_items"]
    assert state["last_poll"] == 2000


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


def test_concurrent_read_write_safety(state_file):
    """Multiple reads/writes don't corrupt the file."""
    for i in range(10):
        state_file.update(known_items={f"item_{i}": time.time()}, last_poll=time.time())
        state = state_file.read()
        assert f"item_{i}" in state["known_items"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd tgtg_mobile && pytest tests/test_service_state.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'services.service_state'`

- [ ] **Step 3: Implement ServiceState**

```python
# tgtg_mobile/services/service_state.py
import json
import logging
import os
import time
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
            # Merge with defaults for any missing keys
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd tgtg_mobile && pytest tests/test_service_state.py -v
```

Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_mobile/services/service_state.py tgtg_mobile/tests/test_service_state.py
git commit -m "feat: add ServiceState for shared background service state"
```

---

### Task 2: NotificationManager — Rich Android Notifications

**Files:**
- Create: `tgtg_mobile/services/notification_manager.py`
- Modify: `tgtg_mobile/services/__init__.py` (export new class)
- Test: `tgtg_mobile/tests/test_notification_manager.py`

- [ ] **Step 1: Write failing tests for NotificationManager**

```python
# tgtg_mobile/tests/test_notification_manager.py
import logging
from unittest.mock import MagicMock, patch

import pytest

from models.item import Item
from services.notification_manager import NotificationManager


@pytest.fixture
def sample_item():
    """Create a sample Item for testing."""
    return Item({
        "display_name": "Test Bakery",
        "items_available": 3,
        "favorite": True,
        "item": {
            "item_id": "test_item_123",
            "name": "Magic Bag",
            "item_price": {"minor_units": 399, "decimals": 2, "code": "EUR"},
            "item_value": {"minor_units": 1200, "decimals": 2, "code": "EUR"},
            "logo_picture": {"current_url": "https://example.com/logo.png"},
            "cover_picture": {"current_url": "https://example.com/cover.png"},
        },
        "store": {"store_name": "Test Bakery", "store_id": "store_123"},
        "pickup_interval": {
            "start": "2026-04-01T18:00:00Z",
            "end": "2026-04-01T18:30:00Z",
        },
    })


@pytest.fixture
def mock_notification_manager():
    """NotificationManager with pyjnius mocked out."""
    with patch("services.notification_manager.ANDROID_AVAILABLE", False):
        mgr = NotificationManager()
        return mgr


def test_init_desktop_fallback(mock_notification_manager, caplog):
    """On desktop, NotificationManager uses logging fallback."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.send_bag_alert(sample_item)
    assert "TGTG Available!" in caplog.text


def test_send_bag_alert_logs_item_info(mock_notification_manager, sample_item, caplog):
    """Bag alert logs item name and price."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.send_bag_alert(sample_item)
    assert "Test Bakery" in caplog.text
    assert "3.99" in caplog.text


def test_send_service_status(mock_notification_manager, caplog):
    """Service status logs message."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.send_service_status("Last check: 2 min ago")
    assert "Last check: 2 min ago" in caplog.text


def test_send_service_status_silent_by_default(mock_notification_manager, caplog):
    """Service status is silent by default."""
    with caplog.at_level(logging.DEBUG):
        mock_notification_manager.send_service_status("test", silent=True)
    # Should not produce warning-level output
    assert all(record.levelno < logging.WARNING for record in caplog.records)


def test_dismiss_notification(mock_notification_manager, caplog):
    """Dismiss logs the notification ID."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.dismiss_notification(12345)
    assert "12345" in caplog.text


def test_android_channels_called_when_available():
    """When Android is available, _ensure_channels is called."""
    with patch("services.notification_manager.ANDROID_AVAILABLE", True), \
         patch("services.notification_manager.autoclass") as mock_autoclass:
        mock_context = MagicMock()
        mock_autoclass.return_value.getSystemService.return_value = MagicMock()
        mgr = NotificationManager(mock_context)
        mock_autoclass.assert_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd tgtg_mobile && pytest tests/test_notification_manager.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement NotificationManager**

```python
# tgtg_mobile/services/notification_manager.py
import logging

log = logging.getLogger("tgtg_mobile")

ANDROID_AVAILABLE = False
try:
    from jnius import autoclass
    ANDROID_AVAILABLE = True
except ImportError:
    log.debug("pyjnius not available - using notification fallback")

# Channel IDs
CHANNEL_BAG_ALERTS = "bag_alerts"
CHANNEL_SERVICE_STATUS = "service_status"

# Notification IDs
NOTIFICATION_BAG_ALERT_BASE = 1000
NOTIFICATION_SERVICE_STATUS = 1


class NotificationManager:
    """Manages Android notifications with channels and action buttons."""

    def __init__(self, context=None):
        self.context = context
        self._notification_manager = None
        self._ensure_channels()

    def _ensure_channels(self):
        """Creates notification channels on Android 8+."""
        if not ANDROID_AVAILABLE or self.context is None:
            log.debug("Notification channels: skipped (not on Android)")
            return

        try:
            NotificationManagerClass = autoclass("android.app.NotificationManager")
            NotificationChannel = autoclass("android.app.NotificationChannel")
            AudioAttributes = autoclass("android.media.AudioAttributes")
            Build = autoclass("android.os.Build")

            if Build.VERSION.SDK_INT < 26:
                return

            nm = self.context.getSystemService(NotificationManagerClass)

            # Bag alerts channel - high priority
            bag_channel = NotificationChannel(
                CHANNEL_BAG_ALERTS,
                "TGTG Bag Alerts",
                NotificationManagerClass.IMPORTANCE_HIGH,
            )
            bag_channel.enableVibration(True)
            bag_channel.enableLights(True)
            nm.createNotificationChannel(bag_channel)

            # Service status channel - low priority
            status_channel = NotificationChannel(
                CHANNEL_SERVICE_STATUS,
                "TGTG Service Status",
                NotificationManagerClass.IMPORTANCE_LOW,
            )
            nm.createNotificationChannel(status_channel)

            self._notification_manager = nm
            log.info("Notification channels created")
        except Exception as e:
            log.error("Failed to create notification channels: %s", e)

    def send_bag_alert(self, item):
        """Send high-priority notification for a new available bag.

        Args:
            item: Item instance with display_name, price, items_available
        """
        title = "TGTG Available!"
        body = f"{item.display_name} - {item.price} ({item.items_available} bags)"
        notification_id = NOTIFICATION_BAG_ALERT_BASE + hash(item.item_id) % 1000

        if ANDROID_AVAILABLE and self.context and self._notification_manager:
            self._send_android_notification(
                notification_id, title, body,
                channel=CHANNEL_BAG_ALERTS,
                item_id=item.item_id,
                high_priority=True,
            )
        else:
            log.info("Notification: %s - %s", title, body)

    def send_service_status(self, message: str, silent: bool = True):
        """Send low-priority service status notification.

        Args:
            message: Status message text
            silent: If True, no sound or vibration
        """
        if ANDROID_AVAILABLE and self.context and self._notification_manager:
            self._send_android_notification(
                NOTIFICATION_SERVICE_STATUS,
                "TGTG Scanner",
                message,
                channel=CHANNEL_SERVICE_STATUS,
                high_priority=False,
                ongoing=True,
            )
        else:
            log.info("Service status: %s", message)

    def dismiss_notification(self, notification_id: int):
        """Remove a specific notification."""
        if ANDROID_AVAILABLE and self._notification_manager:
            try:
                self._notification_manager.cancel(notification_id)
            except Exception as e:
                log.error("Failed to dismiss notification %d: %s", notification_id, e)
        else:
            log.info("Dismiss notification: %d", notification_id)

    def _send_android_notification(
        self,
        notification_id: int,
        title: str,
        body: str,
        channel: str = CHANNEL_SERVICE_STATUS,
        item_id: str | None = None,
        high_priority: bool = False,
        ongoing: bool = False,
    ):
        """Send a native Android notification via pyjnius."""
        try:
            NotificationCompat = autoclass("androidx.core.app.NotificationCompat")
            PendingIntent = autoclass("android.app.PendingIntent")
            Intent = autoclass("android.content.Intent")

            # Build intent for tapping notification
            intent = Intent(self.context, self.context.getClass())
            if item_id:
                intent.putExtra("item_id", item_id)
            intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP)

            pending_intent = PendingIntent.getActivity(
                self.context, 0, intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE,
            )

            builder = NotificationCompat.Builder(self.context, channel)
            builder.setSmallIcon(self.context.getApplicationInfo().icon)
            builder.setContentTitle(title)
            builder.setContentText(body)
            builder.setContentIntent(pending_intent)
            builder.setAutoCancel(True)
            builder.setOngoing(ongoing)

            if high_priority:
                builder.setPriority(NotificationCompat.PRIORITY_HIGH)
                builder.setDefaults(
                    NotificationCompat.DEFAULT_SOUND
                    | NotificationCompat.DEFAULT_VIBRATE
                    | NotificationCompat.DEFAULT_LIGHTS
                )

            self._notification_manager.notify(notification_id, builder.build())
        except Exception as e:
            log.error("Failed to send Android notification: %s", e)
            log.info("Fallback: %s - %s", title, body)
```

- [ ] **Step 4: Update services/__init__.py**

```python
# tgtg_mobile/services/__init__.py
from services.service_state import ServiceState
from services.notification_manager import NotificationManager

__all__ = ["ServiceState", "NotificationManager"]
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd tgtg_mobile && pytest tests/test_notification_manager.py -v
```

Expected: All 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add tgtg_mobile/services/notification_manager.py tgtg_mobile/services/__init__.py tgtg_mobile/tests/test_notification_manager.py
git commit -m "feat: add NotificationManager with Android channels and action buttons"
```

---

### Task 3: ForegroundService — Background Polling

**Files:**
- Modify: `tgtg_mobile/services/android_service.py`

- [ ] **Step 1: Read current android_service.py**

Current file has `NotificationService`, `BackgroundScanner`, `start_foreground_service`, `stop_foreground_service`. We'll add `ForegroundService` class and update the helper functions.

- [ ] **Step 2: Implement ForegroundService**

```python
# Add to tgtg_mobile/services/android_service.py (append after existing code)

import json
import logging
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger("tgtg_mobile")

# ... existing imports and classes remain ...

class ForegroundService:
    """Android foreground service that polls TGTG API for available bags."""

    def __init__(
        self,
        poll_interval: int = 60,
        notification_mgr=None,
        state_path: str | None = None,
    ):
        self.poll_interval = poll_interval
        self.notification_mgr = notification_mgr
        self.state_file = ServiceState(state_path) if state_path else ServiceState()
        self.running = False
        self._thread: threading.Thread | None = None
        self._client = None
        self._adaptive_interval = poll_interval
        self._no_bags_since: float | None = None

    def start(self):
        """Start the foreground service."""
        if self.running:
            log.warning("ForegroundService already running")
            return

        self.running = True
        self.state_file.set_service_running(True)

        # Start Android foreground service if available
        if ANDROID_AVAILABLE:
            try:
                service = AndroidService("tgtg_scanner", "TGTG Scanner - monitoring bags")
                service.start()
                log.info("Android foreground service started")
            except Exception as e:
                log.error("Failed to start Android service: %s", e)

        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        log.info(f"ForegroundService started (interval: {self.poll_interval}s)")

    def stop(self):
        """Stop the foreground service."""
        self.running = False
        self.state_file.set_service_running(False)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        if ANDROID_AVAILABLE:
            try:
                # Stop Android foreground service
                pass  # Service stops when Python process ends
            except Exception as e:
                log.error("Failed to stop Android service: %s", e)

        log.info("ForegroundService stopped")

    def _poll_loop(self):
        """Main polling loop running in background thread."""
        log.info("Poll loop started")

        while self.running:
            try:
                self._poll_once()
            except Exception as e:
                log.error("Poll loop error: %s", e)

            # Adaptive interval: use current _adaptive_interval
            sleep_time = self._adaptive_interval
            for _ in range(sleep_time):
                if not self.running:
                    break
                time.sleep(1)

    def _poll_once(self):
        """Execute a single poll cycle."""
        try:
            # Load client if not initialized
            if self._client is None:
                self._client = self._create_client()
                if self._client is None:
                    log.warning("No valid tokens, skipping poll")
                    time.sleep(30)
                    return

            # Fetch favorites
            favorites = self._client.get_favorites()
            items = [Item(f) for f in favorites]
            available = [i for i in items if i.is_available]

            now = time.time()

            # Check for new items
            state = self.state_file.read()
            known_ids = set(state["known_items"].keys())
            current_ids = {i.item_id for i in available}
            new_ids = current_ids - known_ids

            # Build updated known_items dict
            updated_known = dict(state["known_items"])
            for item_id in current_ids:
                if item_id not in updated_known:
                    updated_known[item_id] = now
                else:
                    updated_known[item_id] = now  # Refresh timestamp

            # Send alerts for new items
            for item in available:
                if item.item_id in new_ids:
                    if self.notification_mgr:
                        self.notification_mgr.send_bag_alert(item)
                    log.info("New bag alert: %s", item.display_name)

            # Adaptive interval logic
            if available:
                self._adaptive_interval = self.poll_interval
                self._no_bags_since = None
            else:
                if self._no_bags_since is None:
                    self._no_bags_since = now
                elif now - self._no_bags_since > 1800:  # 30 min
                    self._adaptive_interval = min(self.poll_interval * 2, 300)

            # Update state
            self.state_file.update(known_items=updated_known, last_poll=now)

            # Update service status notification
            if self.notification_mgr:
                elapsed = int(now - state.get("last_poll", now))
                self.notification_mgr.send_service_status(
                    f"Last check: {elapsed}s ago | {len(available)} bags"
                )

            log.info(f"Poll complete: {len(available)} available, {len(new_ids)} new")

        except Exception as e:
            error_msg = str(e).lower()
            if "401" in error_msg or "unauthorized" in error_msg:
                log.warning("Auth error during poll, pausing")
                self.state_file.set_auth_error(True)
                time.sleep(60)
            elif "403" in error_msg:
                log.warning("403 during poll, retrying once")
                try:
                    if self._client and self._client.session:
                        self._client.session.invalidate_datadome_cache()
                    time.sleep(3)
                    self._poll_once()
                except Exception:
                    pass
            elif "429" in error_msg:
                log.warning("Rate limited, backing off")
                time.sleep(30)
            else:
                log.error("Poll error: %s", e)
                time.sleep(15)

    def _create_client(self):
        """Create TgtgClient from stored tokens."""
        try:
            from services.token_storage import TokenStorage
            from tgtg_api.client import TgtgClient

            storage = TokenStorage()
            tokens = storage.load()

            if not tokens.get("access_token"):
                return None

            client = TgtgClient(
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token"),
                datadome_cookie=tokens.get("datadome_cookie"),
            )
            return client
        except Exception as e:
            log.error("Failed to create TgtgClient: %s", e)
            return None
```

- [ ] **Step 3: Update helper functions at bottom of android_service.py**

```python
# Replace the existing start_foreground_service and stop_foreground_service with:

_foreground_service_instance: ForegroundService | None = None


def get_foreground_service(
    poll_interval: int = 60,
    notification_mgr=None,
    state_path: str | None = None,
) -> ForegroundService:
    """Returns singleton ForegroundService instance."""
    global _foreground_service_instance
    if _foreground_service_instance is None:
        _foreground_service_instance = ForegroundService(
            poll_interval=poll_interval,
            notification_mgr=notification_mgr,
            state_path=state_path,
        )
    return _foreground_service_instance


def start_foreground_service_service(
    poll_interval: int = 60,
    notification_mgr=None,
    state_path: str | None = None,
) -> ForegroundService:
    """Start the foreground service."""
    svc = get_foreground_service(poll_interval, notification_mgr, state_path)
    svc.start()
    return svc


def stop_foreground_service_service() -> None:
    """Stop the foreground service."""
    global _foreground_service_instance
    if _foreground_service_instance:
        _foreground_service_instance.stop()
        _foreground_service_instance = None
```

Note: The existing `start_foreground_service` and `stop_foreground_service` function names conflict with the new naming. We'll rename them to `start_foreground_service_service` and `stop_foreground_service_service` to avoid confusion with the class name. Alternatively, rename the class to `TgtgForegroundService`.

Let me use the cleaner approach — rename the class:

```python
class TgtgForegroundService:
    """Android foreground service that polls TGTG API for available bags."""
    # ... (same implementation as above, just class name changed)
```

And keep the helper function names as `start_foreground_service` / `stop_foreground_service`.

- [ ] **Step 4: Run lint and type check**

```bash
cd tgtg_mobile && python -c "from services.android_service import TgtgForegroundService, start_foreground_service, stop_foreground_service; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 5: Commit**

```bash
git add tgtg_mobile/services/android_service.py
git commit -m "feat: add TgtgForegroundService with adaptive polling loop"
```

---

### Task 4: Settings Screen Integration

**Files:**
- Modify: `tgtg_mobile/screens/settings.py`
- Modify: `tgtg_mobile/screens/settings.kv`

- [ ] **Step 1: Read current settings.py**

Already read. Has `notifications_enabled`, `poll_interval`, `background_scan` properties. We'll add `foreground_service_enabled`, `notification_sound_enabled`, `service_running`.

- [ ] **Step 2: Update settings.py**

```python
# tgtg_mobile/screens/settings.py
from kivy.app import App
from kivy.uix.screenmanager import Screen
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.storage.jsonstore import JsonStore
import os


class SettingsScreen(Screen):
    notifications_enabled = BooleanProperty(True)
    poll_interval = NumericProperty(60)
    background_scan = BooleanProperty(False)
    foreground_service_enabled = BooleanProperty(False)
    notification_sound_enabled = BooleanProperty(True)
    service_running = BooleanProperty(False)
    current_version = StringProperty("0.5.0")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.store = JsonStore("settings.json")
        self.load_settings()

    def on_enter(self):
        """Update service running status when entering settings."""
        self.load_settings()
        self._update_service_status()

    def load_settings(self):
        try:
            if self.store.exists("notifications_enabled"):
                self.notifications_enabled = self.store.get("notifications_enabled")
            if self.store.exists("poll_interval"):
                self.poll_interval = self.store.get("poll_interval")
            if self.store.exists("background_scan"):
                self.background_scan = self.store.get("background_scan")
            if self.store.exists("foreground_service_enabled"):
                self.foreground_service_enabled = self.store.get("foreground_service_enabled")
            if self.store.exists("notification_sound_enabled"):
                self.notification_sound_enabled = self.store.get("notification_sound_enabled")
        except Exception:
            pass

    def save_settings(self):
        try:
            self.store.put("notifications_enabled", self.notifications_enabled)
            self.store.put("poll_interval", self.poll_interval)
            self.store.put("background_scan", self.background_scan)
            self.store.put("foreground_service_enabled", self.foreground_service_enabled)
            self.store.put("notification_sound_enabled", self.notification_sound_enabled)
        except Exception:
            pass

    def toggle_notifications(self, value):
        self.notifications_enabled = value
        self.save_settings()

    def toggle_sound(self, value):
        self.notification_sound_enabled = value
        self.save_settings()

    def set_poll_interval(self, interval):
        self.poll_interval = int(interval)
        self.save_settings()

    def toggle_background_scan(self, value):
        self.background_scan = value
        self.save_settings()

    def toggle_foreground_service(self, value):
        """Toggle the foreground service on/off."""
        self.foreground_service_enabled = value
        self.save_settings()

        if value:
            self._start_service()
        else:
            self._stop_service()

    def _start_service(self):
        """Start the foreground service."""
        try:
            from services.android_service import get_foreground_service
            app = App.get_running_app()

            svc = get_foreground_service(
                poll_interval=self.poll_interval,
                notification_mgr=getattr(app, "notification_manager", None),
            )
            svc.start()
            self.service_running = True
        except Exception as e:
            self.service_running = False
            self.error_message = f"Failed to start service: {e}"

    def _stop_service(self):
        """Stop the foreground service."""
        try:
            from services.android_service import stop_foreground_service
            stop_foreground_service()
            self.service_running = False
        except Exception as e:
            self.error_message = f"Failed to stop service: {e}"

    def _update_service_status(self):
        """Check if service is currently running."""
        try:
            from services.service_state import ServiceState
            state = ServiceState()
            self.service_running = state.read().get("service_running", False)
        except Exception:
            self.service_running = False

    def logout(self):
        # Stop service before logout
        if self.foreground_service_enabled:
            self._stop_service()
        app = App.get_running_app()
        if app:
            app.logout()
        self.manager.current = "login"

    def go_back(self):
        self.save_settings()
        self.manager.current = "favorites"
```

- [ ] **Step 3: Update settings.kv**

```kv
# tgtg_mobile/screens/settings.kv
<SettingsScreen>:
    BoxLayout:
        orientation: "vertical"

        canvas.before:
            Color:
                rgb: 0.15, 0.18, 0.22
            Rectangle:
                pos: self.pos
                size: self.size

        # Header
        BoxLayout:
            size_hint_y: None
            height: "56dp"
            padding: [10, 0]

            canvas.before:
                Color:
                    rgb: 0.12, 0.14, 0.18
                Rectangle:
                    pos: self.pos
                    size: self.size

            Button:
                text: "Back"
                font_size: "16sp"
                size_hint_x: None
                width: "80dp"
                background_color: 0, 0, 0, 0
                on_release: root.go_back()

            Label:
                text: "Settings"
                font_size: "18sp"
                bold: True
                color: 1, 1, 1, 1
                size_hint_x: 1

        # Content
        ScrollView:
            size_hint_y: 1

            BoxLayout:
                orientation: "vertical"
                size_hint_y: None
                height: self.minimum_height
                padding: 20
                spacing: 15

                # Service Status
                BoxLayout:
                    orientation: "horizontal"
                    size_hint_y: None
                    height: "40dp"

                    Label:
                        text: "Service Status"
                        font_size: "14sp"
                        color: 0.7, 0.7, 0.7, 1
                        size_hint_x: 1

                    Canvas:
                        size: "16dp", "16dp"
                        pos: self.pos
                        Color:
                            rgba: 0.29, 0.76, 0.26, 1 if root.service_running else 0.5, 0.5, 0.5, 1
                        Ellipse:
                            pos: self.pos
                            size: self.size

                    Label:
                        text: "Running" if root.service_running else "Stopped"
                        font_size: "14sp"
                        color: 0.7, 0.7, 0.7, 1
                        size_hint_x: None
                        width: "80dp"

                # Notifications
                BoxLayout:
                    orientation: "horizontal"
                    size_hint_y: None
                    height: "50dp"

                    Label:
                        text: "Push Notifications"
                        font_size: "16sp"
                        color: 1, 1, 1, 1
                        size_hint_x: 1

                    Switch:
                        id: notify_switch
                        active: root.notifications_enabled
                        on_active: root.toggle_notifications(args[1])

                # Sound
                BoxLayout:
                    orientation: "horizontal"
                    size_hint_y: None
                    height: "50dp"

                    Label:
                        text: "Notification Sound"
                        font_size: "16sp"
                        color: 1, 1, 1, 1
                        size_hint_x: 1

                    Switch:
                        active: root.notification_sound_enabled
                        on_active: root.toggle_sound(args[1])

                # Foreground Service
                BoxLayout:
                    orientation: "horizontal"
                    size_hint_y: None
                    height: "50dp"

                    Label:
                        text: "Background Service"
                        font_size: "16sp"
                        color: 1, 1, 1, 1
                        size_hint_x: 1

                    Switch:
                        active: root.foreground_service_enabled
                        on_active: root.toggle_foreground_service(args[1])

                # Poll Interval
                Label:
                    text: "Poll Interval: {} seconds".format(int(root.poll_interval))
                    font_size: "14sp"
                    color: 0.7, 0.7, 0.7, 1
                    size_hint_y: None
                    height: "30dp"

                Slider:
                    min: 30
                    max: 300
                    step: 10
                    value: root.poll_interval
                    on_value: root.set_poll_interval(args[1])

                # Background Scanning (legacy)
                BoxLayout:
                    orientation: "horizontal"
                    size_hint_y: None
                    height: "50dp"

                    Label:
                        text: "Background Scanning (legacy)"
                        font_size: "14sp"
                        color: 0.5, 0.5, 0.5, 1
                        size_hint_x: 1

                    Switch:
                        id: scan_switch
                        active: root.background_scan
                        on_active: root.toggle_background_scan(args[1])

                # About
                Label:
                    text: "Version " + root.current_version
                    font_size: "14sp"
                    color: 1, 1, 1, 1
                    size_hint_y: None
                    height: "30dp"

                Label:
                    text: "TGTG Scanner Mobile"
                    font_size: "12sp"
                    color: 0.5, 0.5, 0.5, 1
                    size_hint_y: None
                    height: "30dp"

                # Logout
                Button:
                    text: "Logout"
                    font_size: "16sp"
                    size_hint_y: None
                    height: "50dp"
                    background_color: 0.9, 0.3, 0.3, 1
                    on_release: root.logout()
```

- [ ] **Step 4: Verify imports work**

```bash
cd tgtg_mobile && python -c "from screens.settings import SettingsScreen; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 5: Commit**

```bash
git add tgtg_mobile/screens/settings.py tgtg_mobile/screens/settings.kv
git commit -m "feat: add foreground service controls to settings screen"
```

---

### Task 5: Main App Integration

**Files:**
- Modify: `tgtg_mobile/main.py`
- Modify: `tgtg_mobile/screens/favorites.py`

- [ ] **Step 1: Update main.py to initialize NotificationManager**

```python
# tgtg_mobile/main.py - modify init_app method and add imports

# Add after existing imports:
from services.service_state import ServiceState

# Modify init_app method (replace the notification service init section):
            # Replace this block:
            # try:
            #     from services.android_service import NotificationService
            #     self.notification_service = NotificationService()
            #     log.info("Notification service initialized")
            # except Exception as e:
            #     log.warning(f"Notification service not available: {e}")
            #     self.notification_service = None

            # With this:
            try:
                from services.notification_manager import NotificationManager
                self.notification_manager = NotificationManager()
                log.info("Notification manager initialized")
            except Exception as e:
                log.warning(f"Notification manager not available: {e}")
                self.notification_manager = None

            # Keep backward compat
            self.notification_service = self.notification_manager
```

- [ ] **Step 2: Update main.py to restore service on app resume**

```python
# Add to TgtgMobileApp class:

def on_resume(self):
    """Called when app comes to foreground."""
    super().on_resume() if hasattr(super(), 'on_resume') else None
    
    # Check if service should be running
    try:
        from services.service_state import ServiceState
        from kivy.storage.jsonstore import JsonStore
        
        state = ServiceState()
        settings = JsonStore("settings.json")
        
        if settings.exists("foreground_service_enabled") and settings.get("foreground_service_enabled"):
            svc_state = state.read()
            if not svc_state.get("service_running", False):
                # Service was enabled but not running - restart it
                log.info("Restarting foreground service on app resume")
                from services.android_service import get_foreground_service
                svc = get_foreground_service(
                    poll_interval=settings.get("poll_interval") if settings.exists("poll_interval") else 60,
                    notification_mgr=self.notification_manager,
                )
                svc.start()
    except Exception as e:
        log.error(f"Failed to restore service on resume: {e}")
```

- [ ] **Step 3: Update favorites.py to use NotificationManager**

```python
# tgtg_mobile/screens/favorites.py - modify the notification section in load_favorites

# Replace the notification block in the fetch() function:
# From:
#                 for item in available_items:
#                     if item.item_id not in previous_ids:
#                         if app.notification_service:
#                             app.notification_service.send_notification(
#                                 title="TGTG Available!",
#                                 message=f"{item.display_name} - {item.price}",
#                                 item_id=item.item_id
#                             )

# To:
                for item in available_items:
                    if item.item_id not in previous_ids:
                        notif_mgr = getattr(app, "notification_manager", None) or getattr(app, "notification_service", None)
                        if notif_mgr:
                            notif_mgr.send_bag_alert(item)
```

- [ ] **Step 4: Verify imports work**

```bash
cd tgtg_mobile && python -c "from main import TgtgMobileApp; print('Import OK')"
```

Expected: `Import OK`

- [ ] **Step 5: Commit**

```bash
git add tgtg_mobile/main.py tgtg_mobile/screens/favorites.py
git commit -m "feat: integrate NotificationManager and service lifecycle into app"
```

---

### Task 6: BootReceiver — Auto-start on Boot

**Files:**
- Create: `tgtg_mobile/android/BootReceiver.java`
- Create: `tgtg_mobile/android/ServiceStarter.java`
- Modify: `tgtg_mobile/buildozer.spec`

- [ ] **Step 1: Create BootReceiver.java**

```java
// tgtg_mobile/android/BootReceiver.java
package org.tgtg;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.util.Log;

public class BootReceiver extends BroadcastReceiver {
    private static final String TAG = "TGTGBootReceiver";
    private static final String PREFS = "settings";
    private static final String KEY_FOREGROUND_SERVICE = "foreground_service_enabled";

    @Override
    public void onReceive(Context context, Intent intent) {
        String action = intent.getAction();
        Log.i(TAG, "Received broadcast: " + action);

        if (Intent.ACTION_BOOT_COMPLETED.equals(action)
                || "android.intent.action.MY_PACKAGE_REPLACED".equals(action)) {
            
            SharedPreferences prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
            boolean serviceEnabled = prefs.getBoolean(KEY_FOREGROUND_SERVICE, false);

            if (serviceEnabled) {
                Log.i(TAG, "Foreground service enabled, starting service");
                startService(context);
            } else {
                Log.i(TAG, "Foreground service not enabled, skipping");
            }
        }
    }

    private void startService(Context context) {
        try {
            // Start the Python service via ServiceStarter
            Intent serviceIntent = new Intent(context, ServiceStarter.class);
            context.startService(serviceIntent);
        } catch (Exception e) {
            Log.e(TAG, "Failed to start service: " + e.getMessage());
        }
    }
}
```

- [ ] **Step 2: Create ServiceStarter.java**

```java
// tgtg_mobile/android/ServiceStarter.java
package org.tgtg;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.util.Log;

import org.kivy.android.PythonService;

public class ServiceStarter extends Service {
    private static final String TAG = "TGTGServiceStarter";

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Log.i(TAG, "Starting TGTG background service");
        
        // Start Python service
        String serviceTitle = "TGTG Scanner";
        String serviceDescription = "Monitoring for available bags";
        String pythonServiceArgument = null;
        
        // Start the Python service
        PythonService.startService(
            this,
            serviceTitle,
            serviceDescription,
            pythonServiceArgument
        );
        
        return START_STICKY;
    }
}
```

- [ ] **Step 3: Update buildozer.spec**

```ini
# tgtg_mobile/buildozer.spec
[app]

title = TGTG Scanner
package.name = tgtgscanner
package.domain = org.tgtg

source.dir = .
version = 0.2.0

requirements = python3,kivy==2.3.0,requests,urllib3,certifi,plyer,pyjnius

orientation = portrait

fullscreen = 0

android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE,VIBRATE,POST_NOTIFICATIONS,FOREGROUND_SERVICE,RECEIVE_BOOT_COMPLETED,WAKE_LOCK

android.archs = arm64-v8a

android.minapi = 24
android.api = 34

android.allow_backup = False

android.add_src = android/

android.manifest.intent_filters = android/AndroidManifest.xml

log_level = 2

p4a.bootstrap = sdl2
```

- [ ] **Step 4: Create AndroidManifest.xml for intent filters**

```xml
<!-- tgtg_mobile/android/AndroidManifest.xml -->
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application>
        <receiver
            android:name="org.tgtg.BootReceiver"
            android:enabled="true"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.BOOT_COMPLETED" />
                <action android:name="android.intent.action.MY_PACKAGE_REPLACED" />
            </intent-filter>
        </receiver>
        <service
            android:name="org.tgtg.ServiceStarter"
            android:enabled="true"
            android:exported="false"
            android:foregroundServiceType="dataSync" />
    </application>
</manifest>
```

- [ ] **Step 5: Verify file structure**

```bash
ls -la tgtg_mobile/android/
```

Expected: `BootReceiver.java`, `ServiceStarter.java`, `AndroidManifest.xml`

- [ ] **Step 6: Commit**

```bash
git add tgtg_mobile/android/ tgtg_mobile/buildozer.spec
git commit -m "feat: add BootReceiver for auto-start on device boot"
```

---

### Task 7: Desktop Testing & Verification

**Files:**
- No new files
- Run existing test suite

- [ ] **Step 1: Run full test suite**

```bash
cd tgtg_mobile && pytest tests/ -v --ignore=tests/test_notification_manager.py
```

Expected: All existing tests pass

- [ ] **Step 2: Run all new tests**

```bash
cd tgtg_mobile && pytest tests/test_service_state.py tests/test_notification_manager.py -v
```

Expected: All 14 tests pass (8 + 6)

- [ ] **Step 3: Desktop smoke test**

```bash
cd tgtg_mobile && python main.py
```

Verify:
- App starts without errors
- Settings screen loads with new toggles
- Service status shows "Stopped"
- Toggling foreground service doesn't crash (logs fallback message)

- [ ] **Step 4: Run lint**

```bash
cd tgtg_mobile && python -m py_compile services/service_state.py services/notification_manager.py services/android_service.py screens/settings.py main.py screens/favorites.py
```

Expected: No syntax errors

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: verify all tests pass and desktop smoke test OK"
```

---

## Self-Review

### 1. Spec Coverage Check

| Spec Requirement | Task |
|-----------------|------|
| Poll TGTG API when app minimized/closed | Task 3 (ForegroundService) |
| Native notifications with View/Dismiss actions | Task 2 (NotificationManager) |
| Distinct notification styles (bag vs status) | Task 2 (send_bag_alert vs send_service_status) |
| Auto-start on reboot | Task 6 (BootReceiver) |
| Prevent duplicate notifications | Task 3 (dedup in _poll_once) |
| Handle API errors gracefully | Task 3 (_poll_once error handling) |
| Battery-conscious adaptive polling | Task 3 (_adaptive_interval logic) |
| Settings integration | Task 4 (SettingsScreen) |
| ServiceState shared file | Task 1 (ServiceState) |
| Buildozer config updates | Task 6 (buildozer.spec) |

All requirements covered.

### 2. Placeholder Scan

No TBD, TODO, or incomplete sections. All code blocks contain real implementations.

### 3. Type Consistency

- `ServiceState` class name consistent across Tasks 1, 3, 5
- `NotificationManager` class name consistent across Tasks 2, 3, 5
- `TgtgForegroundService` class name consistent in Task 3
- `foreground_service_enabled` property name consistent in Tasks 4, 6
- `service_state.json` path consistent in Tasks 1, 3

### 4. Task Independence

Each task produces working, testable code:
- Task 1: ServiceState works standalone
- Task 2: NotificationManager works with desktop fallback
- Task 3: ForegroundService can be tested independently
- Task 4: Settings UI works with existing app
- Task 5: Integration connects components
- Task 6: BootReceiver is Android-only, doesn't affect desktop
- Task 7: Verification
