# Design: Native Notifications & Background Foreground Service

**Date:** 2026-04-01
**Status:** Draft

## Summary

Add native Android foreground service for background polling of TGTG favorite bags, with rich notifications including action buttons. Replaces current thread-based polling that only works while the app is open.

## Requirements

- Poll TGTG API for available bags when app is minimized or closed
- Send native Android notifications with "View" and "Dismiss" action buttons
- Distinct notification styles: bag alerts (sound/vibration) vs service status (silent)
- Auto-start service on device reboot if background scan was enabled
- Prevent duplicate notifications across service and app
- Handle API errors, token expiration, and permission denials gracefully
- Battery-conscious: adaptive polling interval, respect doze mode

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Kivy App (Foreground)                     │
│  ┌───────────┐  ┌────────────┐  ┌─────────────────────────┐ │
│  │Favorites   │  │Settings    │  │ItemDetail               │ │
│  │Screen      │  │Screen      │  │Screen                   │ │
│  └─────┬─────┘  └─────┬──────┘  └─────────────────────────┘ │
│        │              │                                       │
│        └──────────────┼───────────────────────────────────────┘
│                       │ reads/writes
│              ┌────────▼────────┐
│              │  ServiceState   │  ← JSON file (shared state)
│              │  (JSON store)   │
│              └────────┬────────┘
│                       │
│              ┌────────▼────────┐
│              │NotificationMgr  │  ← pyjnius → Android APIs
│              └─────────────────┘
└─────────────────────────────────────────────────────────────┘
        │
        │ independent lifecycle
        ▼
┌─────────────────────────────────────────────────────────────┐
│              ForegroundService (Background)                  │
│  ┌──────────┐  ┌────────────┐  ┌──────────────────────────┐ │
│  │Poll Loop │→ │TgtgClient  │  │ServiceState (write)      │ │
│  │          │  │            │  │NotificationMgr (alerts)  │ │
│  └──────────┘  └────────────┘  └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│              BootReceiver                                   │
│  Listens: BOOT_COMPLETED, MY_PACKAGE_REPLACED               │
│  Action: Start ForegroundService if background_scan enabled │
└─────────────────────────────────────────────────────────────┘
```

### Communication Flow

```
[Foreground Service] --polls--> [TGTG API]
       |
       |--new bag--> [NotificationManager] --> [System Notification]
       |                    |
       |              (user taps "View")
       |                    v
       |              [App opens to ItemDetail]
       |
       `--writes--> [ServiceState JSON] <--reads-- [Kivy App on resume]
```

## Component Specifications

### 1. ForegroundService

**File:** `services/android_service.py` (extend existing)

```python
class ForegroundService:
    """Wraps Android foreground service with polling logic."""
    
    def __init__(self, poll_interval: int = 60, notification_mgr: NotificationManager):
        self.poll_interval = poll_interval
        self.notification_mgr = notification_mgr
        self.state_file = ServiceState()
        self.running = False
        self._client: TgtgClient | None = None
    
    def start(self):
        """Starts the Android foreground service via pyjnius."""
        # 1. Create notification channel for service
        # 2. Build foreground notification (non-dismissable)
        # 3. Call AndroidService.startForeground()
        # 4. Start polling thread
    
    def stop(self):
        """Stops service and removes foreground notification."""
    
    def _poll_loop(self):
        """Background thread that polls TGTG API."""
        # Load tokens → Create TgtgClient
        # Loop: get_favorites() → compare with state_file
        # → new items → notification_mgr.send_bag_alert()
        # → update state_file.last_poll
```

**pyjnius imports:**
```python
from jnius import autoclass
Context = autoclass('android.content.Context')
NotificationCompat = autoclass('androidx.core.app.NotificationCompat')
PendingIntent = autoclass('android.app.PendingIntent')
Intent = autoclass('android.content.Intent')
```

### 2. NotificationManager

**File:** `services/notification_manager.py` (new)

```python
class NotificationManager:
    def __init__(self, app_context):
        self.app_context = app_context
        self._ensure_channels()
    
    def _ensure_channels(self):
        """Creates notification channels (Android 8+)."""
        # Channel 1: "bag_alerts" - IMPORTANCE_HIGH
        # Channel 2: "service_status" - IMPORTANCE_LOW
    
    def send_bag_alert(self, item: Item):
        """High-priority notification with action buttons."""
        # Title: "TGTG Available!"
        # Body: "{store_name} - {price} ({count} bags)"
        # Actions: "View" (opens item), "Dismiss"
        # Sound: default, Vibrate: yes, LED: yes
    
    def send_service_status(self, message: str, silent: bool = True):
        """Low-priority status notification."""
        # Updates persistent service notification
        # Shows: "Last check: 2 min ago | 3 bags found"
    
    def dismiss_notification(self, notification_id: int):
        """Removes a specific notification."""
```

### 3. ServiceState

**File:** `services/service_state.py` (new)

```python
class ServiceState:
    """Shared JSON state file between background service and Kivy app."""
    
    def __init__(self, path: str = "service_state.json"):
        self.path = path
    
    def read(self) -> dict:
        """Returns current state: {last_poll, known_items, notifications}"""
    
    def update(self, known_items: set, last_poll: float):
        """Updates state file with new data."""
    
    def get_new_items_since(self, timestamp: float) -> list:
        """Returns items discovered after given timestamp."""
```

### 4. Settings Integration

**File:** `screens/settings.py` (extend existing)

New properties:
- `foreground_service_enabled` (BooleanProperty)
- `notification_sound_enabled` (BooleanProperty)
- `service_running` (BooleanProperty) — read-only status indicator

UI additions:
- Service status indicator (green dot if running)
- Toggle starts/stops the foreground service
- Poll interval slider updates service config via state file

### 5. BootReceiver

**File:** `android/BootReceiver.java` (new)

```java
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        // Check if background_scan was enabled in settings
        // If yes, start ForegroundService
    }
}
```

Registered for:
- `android.intent.action.BOOT_COMPLETED`
- `android.intent.action.MY_PACKAGE_REPLACED`

## Buildozer Configuration

**File:** `buildozer.spec`

Requirements addition:
```
requirements = python3,kivy==2.3.0,requests,urllib3,certifi,plyer,pyjnius
```

Android manifest customization:
```
android.add_src = android/
android.permissions = INTERNET,ACCESS_NETWORK_STATE,POST_NOTIFICATIONS,FOREGROUND_SERVICE,RECEIVE_BOOT_COMPLETED,WAKE_LOCK,VIBRATE
```

## Error Handling

### API Errors

| Error | Action |
|-------|--------|
| 403/Captcha | Invalidate DataDome, retry once, skip poll cycle |
| 401/Unauthorized | Write `auth_error` to state, pause polling. User re-logs in via app. |
| Network timeout | Retry once with 5s backoff, then wait for next cycle |
| 429 Rate limit | Exponential backoff (5s → 15s → 60s), resume after cooldown |

### Permission Handling

- Android 13+ requires runtime `POST_NOTIFICATIONS` permission
- Check on first launch via pyjnius
- If denied: show explanation, redirect to settings
- Service checks before sending; silently skips if denied

### Service Lifecycle

| Scenario | Behavior |
|----------|----------|
| App killed by OS | Service continues independently |
| User swipes notification | Service restarts foreground notification |
| Device reboot | BootReceiver starts service if enabled |
| App update | `MY_PACKAGE_REPLACED` receiver restarts service |
| Token expires | Service detects 401, writes error state, pauses |

### Duplicate Notification Prevention

Three layers:
1. In-memory `known_item_ids` set in current session
2. `known_items` with timestamps written to state file after each poll
3. 5-minute time window: only alert on items first seen within last 5 minutes

```python
# ServiceState stores: {"known_items": {"item_id_123": 1712000000, ...}}
current_ids = {item.item_id for item in items}
now = time.time()
new_ids = current_ids - state.known_items.keys()
for item_id in new_ids:
    if now - state.known_items.get(item_id, 0) < 300:  # 5 min window
        notification_mgr.send_bag_alert(item)
state.update({**state.known_items, **{id: now for id in current_ids}}, now)
```

## Battery Optimization

- **Adaptive polling**: Double interval (up to 5 min) if no bags found for 30 min
- **Resume normal**: When bags detected or app comes to foreground
- **Doze mode**: `startForeground()` provides partial exemption; no unnecessary wake locks
- **WAKE_LOCK**: Only during active API call, released immediately after

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `services/android_service.py` | Modify | Add ForegroundService class, extend existing |
| `services/notification_manager.py` | New | Rich notification management with channels |
| `services/service_state.py` | New | Shared JSON state file |
| `screens/settings.py` | Modify | Add service toggle, status indicator |
| `screens/settings.kv` | Modify | UI for new settings |
| `screens/favorites.py` | Modify | Use NotificationManager, read service state |
| `main.py` | Modify | Init NotificationManager, service lifecycle |
| `buildozer.spec` | Modify | Add pyjnius, BootReceiver config |
| `android/BootReceiver.java` | New | Boot/restart receiver |
| `models/item.py` | No changes needed | Deduplication timestamps tracked in ServiceState |

## Testing Approach

1. **Unit tests**: ServiceState read/write, deduplication logic
2. **Desktop testing**: NotificationManager falls back to logging
3. **Device testing**: Build APK, test on Android device
   - Notification delivery with app in foreground/background/closed
   - Action button behavior
   - Service survives app kill
   - Boot receiver triggers correctly
   - Permission denial handling
