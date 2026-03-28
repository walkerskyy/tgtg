import logging
import threading
from typing import Callable

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
                log.error(f"Background scan error: {e}")
            import time
            time.sleep(self.poll_interval)


def start_foreground_service():
    if not ANDROID_AVAILABLE:
        log.info("Foreground service not available (non-Android mode)")
        return None

    try:
        service = AndroidService("tgtg_scanner", "TGTG Scanner running")
        service.start()
        return service
    except Exception as e:
        log.error(f"Failed to start foreground service: {e}")
        return None


def stop_foreground_service(service):
    if service and ANDROID_AVAILABLE:
        try:
            service.stop()
        except Exception as e:
            log.error(f"Failed to stop foreground service: {e}")
