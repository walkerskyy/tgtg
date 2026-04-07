import logging

from kivy.app import App
from kivy.uix.screenmanager import Screen
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.storage.jsonstore import JsonStore

log = logging.getLogger("tgtg_mobile")


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
        except Exception as e:
            log.warning("Failed to load settings: %s", e)

    def save_settings(self):
        try:
            self.store.put("notifications_enabled", self.notifications_enabled)
            self.store.put("poll_interval", self.poll_interval)
            self.store.put("background_scan", self.background_scan)
            self.store.put("foreground_service_enabled", self.foreground_service_enabled)
            self.store.put("notification_sound_enabled", self.notification_sound_enabled)
        except Exception as e:
            log.warning("Failed to save settings: %s", e)

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
            # Defer status check to _update_service_status to avoid race condition
            self._update_service_status()
        except Exception as e:
            log.error("Failed to start foreground service: %s", e)
            self.foreground_service_enabled = False
            self.service_running = False
            self.save_settings()

    def _stop_service(self):
        """Stop the foreground service."""
        try:
            from services.android_service import stop_foreground_service
            stop_foreground_service()
            self.service_running = False
        except Exception as e:
            log.error("Failed to stop foreground service: %s", e)
            self.service_running = False

    def _update_service_status(self):
        """Check if service is currently running."""
        try:
            from services.service_state import ServiceState
            state = ServiceState()
            self.service_running = state.read().get("service_running", False)
        except Exception:
            log.debug("Failed to read service status")
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
