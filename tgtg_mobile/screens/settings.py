from kivy.uix.screenmanager import Screen
from kivy.properties import BooleanProperty, NumericProperty, StringProperty
from kivy.storage.jsonstore import JsonStore
import os


class SettingsScreen(Screen):
    notifications_enabled = BooleanProperty(True)
    poll_interval = NumericProperty(60)
    background_scan = BooleanProperty(False)
    current_version = StringProperty("0.5.0")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.store = JsonStore("settings.json")
        self.load_settings()

    def load_settings(self):
        try:
            if self.store.exists("notifications_enabled"):
                self.notifications_enabled = self.store.get("notifications_enabled")
            if self.store.exists("poll_interval"):
                self.poll_interval = self.store.get("poll_interval")
            if self.store.exists("background_scan"):
                self.background_scan = self.store.get("background_scan")
        except Exception:
            pass

    def save_settings(self):
        try:
            self.store.put("notifications_enabled", self.notifications_enabled)
            self.store.put("poll_interval", self.poll_interval)
            self.store.put("background_scan", self.background_scan)
        except Exception:
            pass

    def toggle_notifications(self, value):
        self.notifications_enabled = value
        self.save_settings()

    def set_poll_interval(self, interval):
        self.poll_interval = int(interval)
        self.save_settings()

    def toggle_background_scan(self, value):
        self.background_scan = value
        self.save_settings()

    def logout(self):
        app = self.manager.app
        if app:
            app.logout()
        self.manager.current = "login"

    def go_back(self):
        self.save_settings()
        self.manager.current = "favorites"
