import logging
import threading
from typing import Callable

log = logging.getLogger("tgtg_mobile")

ANDROID_AVAILABLE = False

try:
    from android import AndroidService
    from android.runnable import run_on_ui_thread
    from jnius import autoclass, cast, JavaClass, MetaJavaClass

    ANDROID_AVAILABLE = True
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Context = autoclass("android.content.Context")
    NotificationManager = autoclass("android.app.NotificationManager")
    NotificationCompat = autoclass("androidx.core.app.NotificationCompat")
    PendingIntent = autoclass("android.app.PendingIntent")
    Intent = autoclass("android.content.Intent")

except ImportError:
    log.debug("Android modules not available - running in non-Android mode")


class NotificationService:
    def __init__(self, channel_id: str = "tgtg_scanner", channel_name: str = "TGTG Scanner"):
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.notification_manager: NotificationManager | None = None
        self.service = None
        if ANDROID_AVAILABLE:
            self._init_notification_channel()

    def _init_notification_channel(self):
        try:
            CharSequence = autoclass("java.lang.CharSequence")
            PythonActivity.mActivity.getSystemService(Context.NOTIFICATION_SERVICE)
            self.notification_manager = cast(
                "android.app.NotificationManager",
                PythonActivity.mActivity.getSystemService(Context.NOTIFICATION_SERVICE),
            )
        except Exception as e:
            log.error(f"Failed to initialize notification channel: {e}")

    def send_notification(self, title: str, message: str, item_id: str | None = None):
        if not ANDROID_AVAILABLE:
            log.info(f"Notification (mock): {title} - {message}")
            return

        try:
            intent = Intent(PythonActivity.mActivity, PythonActivity.mActivity.getClass())
            intent.setFlags(0x10000000)
            if item_id:
                intent.putExtra("item_id", item_id)

            pending_intent = PendingIntent.getActivity(
                PythonActivity.mActivity,
                0,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE,
            )

            builder = NotificationCompat.Builder(PythonActivity.mActivity, self.channel_id)
            builder.setContentTitle(title)
            builder.setContentText(message)
            builder.setSmallIcon(17301589)
            builder.setContentIntent(pending_intent)
            builder.setAutoCancel(True)

            notification = builder.build()
            if self.notification_manager:
                self.notification_manager.notify(hash(item_id or title), notification)

        except Exception as e:
            log.error(f"Failed to send notification: {e}")


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
