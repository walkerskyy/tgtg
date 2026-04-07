import logging
from typing import Any

from models.item import Item

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


def _stable_item_hash(item_id: str) -> int:
    """Returns a stable hash for an item ID (unlike Python's built-in hash())."""
    h = 0
    for char in item_id:
        h = (h * 31 + ord(char)) & 0x7FFFFFFF
    return h % 1000


class NotificationManager:
    """Manages Android notifications with channels and action buttons."""

    def __init__(self, context: Any | None = None) -> None:
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

    def send_bag_alert(self, item: Item) -> None:
        """Send high-priority notification for a new available bag.

        Args:
            item: Item instance with display_name, price, items_available
        """
        title = "TGTG Available!"
        body = f"{item.display_name} - {item.price} ({item.items_available} bags)"
        notification_id = NOTIFICATION_BAG_ALERT_BASE + _stable_item_hash(item.item_id)

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
                silent=silent,
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
        silent: bool = False,
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
                if not silent:
                    builder.setDefaults(
                        NotificationCompat.DEFAULT_SOUND
                        | NotificationCompat.DEFAULT_VIBRATE
                        | NotificationCompat.DEFAULT_LIGHTS
                    )

                # Add action buttons for bag alerts
                if item_id:
                    # View action
                    view_intent = Intent(self.context, self.context.getClass())
                    view_intent.putExtra("item_id", item_id)
                    view_intent.putExtra("action", "view")
                    view_intent.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP)
                    view_pending = PendingIntent.getActivity(
                        self.context, 1, view_intent,
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE,
                    )
                    builder.addAction(0, "View", view_pending)

                    # Dismiss action
                    dismiss_intent = Intent(self.context, self.context.getClass())
                    dismiss_intent.putExtra("notification_id", notification_id)
                    dismiss_intent.putExtra("action", "dismiss")
                    dismiss_pending = PendingIntent.getBroadcast(
                        self.context, 2, dismiss_intent,
                        PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE,
                    )
                    builder.addAction(0, "Dismiss", dismiss_pending)

            self._notification_manager.notify(notification_id, builder.build())
        except Exception as e:
            log.error("Failed to send Android notification: %s", e)
            log.info("Fallback: %s - %s", title, body)
