import logging
import threading
import time
from typing import Any, Callable

from models.item import Item
from services.service_state import ServiceState

log = logging.getLogger("tgtg_mobile")

ANDROID_AVAILABLE = False

try:
    from android import AndroidService
    from android.runnable import run_on_ui_thread
    ANDROID_AVAILABLE = True
except ImportError:
    log.debug("Android modules not available - running in non-Android mode")


class NotificationService:
    def __init__(self, channel_id: str = "tgtg_scanner", channel_name: str = "TGTG Scanner"):
        self.channel_id = channel_id
        self.channel_name = channel_name
        self._plyer_notification = None
        
        try:
            from plyer import notification
            self._plyer_notification = notification
        except ImportError:
            log.debug("Plyer notifications not available")

    def send_notification(self, title: str, message: str, item_id: str | None = None):
        if self._plyer_notification:
            try:
                self._plyer_notification.notify(
                    title=title,
                    message=message,
                    app_name="TGTG Scanner",
                    timeout=5,
                )
            except Exception as e:
                log.error(f"Failed to send notification: {e}")
        else:
            log.info(f"Notification: {title} - {message}")


class BackgroundScanner:
    def __init__(self, poll_interval: int = 60, on_items_changed: Callable | None = None):
        self.poll_interval = poll_interval
        self.on_items_changed = on_items_changed
        self.running = False
        self.thread: threading.Thread | None = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        log.info(f"Background scanner started (interval: {self.poll_interval}s)")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        log.info("Background scanner stopped")

    def _run(self):
        while self.running:
            try:
                if self.on_items_changed:
                    self.on_items_changed()
            except Exception as e:
                log.error("Background scan error: %s", e)
            time.sleep(self.poll_interval)


class TgtgForegroundService:
    """Android foreground service that polls TGTG API for available bags."""

    def __init__(
        self,
        poll_interval: int = 60,
        notification_mgr: Any | None = None,
        state_path: str | None = None,
    ):
        self.poll_interval = poll_interval
        self.notification_mgr = notification_mgr
        self.state_file = ServiceState(state_path) if state_path else ServiceState()
        self.running = False
        self._thread: threading.Thread | None = None
        self._client: Any | None = None
        self._adaptive_interval = poll_interval
        self._no_bags_since: float | None = None
        self._last_status_update: float = 0

    def start(self):
        """Start the foreground service."""
        if self.running:
            log.warning("TgtgForegroundService already running")
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
        log.info("TgtgForegroundService started (interval: %ds)", self.poll_interval)

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

        log.info("TgtgForegroundService stopped")

    def _poll_loop(self):
        """Main polling loop running in background thread."""
        log.info("Poll loop started")

        while self.running:
            # Handle missing client (no valid tokens)
            if self._client is None:
                self._client = self._create_client()
                if self._client is None:
                    log.warning("No valid tokens, waiting before retry")
                    time.sleep(30)
                    continue

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

    def _poll_once(self, retry: bool = False):
        """Execute a single poll cycle.
        
        Args:
            retry: If True, this is a retry after 403. Don't retry again.
        """
        try:
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

            # Update service status notification (throttled to every 5 min)
            if self.notification_mgr and (now - self._last_status_update > 300):
                elapsed = int(now - state.get("last_poll", now))
                self.notification_mgr.send_service_status(
                    f"Last check: {elapsed}s ago | {len(available)} bags"
                )
                self._last_status_update = now

            log.info("Poll complete: %d available, %d new", len(available), len(new_ids))

        except Exception as e:
            error_msg = str(e).lower()
            if "401" in error_msg or "unauthorized" in error_msg:
                log.warning("Auth error during poll, pausing")
                self.state_file.set_auth_error(True)
                self._client = None  # Force client recreation on next poll
                time.sleep(60)
            elif "403" in error_msg:
                if retry:
                    log.warning("403 on retry, giving up")
                    time.sleep(15)
                else:
                    log.warning("403 during poll, retrying once")
                    try:
                        if self._client and self._client.session:
                            self._client.session.invalidate_datadome_cache()
                        time.sleep(3)
                        self._poll_once(retry=True)
                    except Exception as e:
                        log.error("403 retry failed: %s", e)
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


_foreground_service_instance: TgtgForegroundService | None = None


def get_foreground_service(
    poll_interval: int = 60,
    notification_mgr=None,
    state_path: str | None = None,
) -> TgtgForegroundService:
    """Returns singleton TgtgForegroundService instance."""
    global _foreground_service_instance
    if _foreground_service_instance is None:
        _foreground_service_instance = TgtgForegroundService(
            poll_interval=poll_interval,
            notification_mgr=notification_mgr,
            state_path=state_path,
        )
    return _foreground_service_instance


def start_foreground_service(
    poll_interval: int = 60,
    notification_mgr=None,
    state_path: str | None = None,
) -> TgtgForegroundService:
    """Start the foreground service."""
    svc = get_foreground_service(poll_interval, notification_mgr, state_path)
    svc.start()
    return svc


def stop_foreground_service() -> None:
    """Stop the foreground service."""
    global _foreground_service_instance
    if _foreground_service_instance:
        _foreground_service_instance.stop()
        _foreground_service_instance = None
