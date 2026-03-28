import logging
import os

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("tgtg_mobile")

try:
    from kivy import require
    from kivy.app import App
    from kivy.uix.screenmanager import ScreenManager, Screen
    from kivy.clock import Clock
    require("2.3.0")
except ImportError as e:
    log.error(f"Kivy import failed: {e}")
    raise


class SplashScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def on_enter(self):
        Clock.schedule_once(self.check_auth, 1.5)

    def check_auth(self, dt):
        app = App.get_running_app()
        if app and app.tgtg_client:
            self.manager.current = "favorites"
        else:
            self.manager.current = "login"


class TgtgMobileApp(App):
    token_storage = None
    tgtg_client = None
    notification_service = None

    def build(self):
        log.info("Building TGTG Mobile app...")
        
        sm = ScreenManager()
        
        sm.add_widget(SplashScreen(name="splash"))
        sm.add_widget(self._create_login_screen())
        sm.add_widget(FavoritesScreen(name="favorites"))
        sm.add_widget(ItemDetailScreen(name="item_detail"))
        sm.add_widget(SettingsScreen(name="settings"))
        
        Clock.schedule_once(lambda dt: self.init_app(sm), 0)
        
        return sm

    def _create_login_screen(self):
        from screens.login import LoginScreen
        return LoginScreen(name="login")

    def init_app(self, sm):
        log.info("Initializing app...")
        try:
            from services.token_storage import TokenStorage
            self.token_storage = TokenStorage()
            tokens = self.token_storage.load()
            log.info(f"Tokens loaded: {bool(tokens)}")
            
            if tokens and tokens.get("access_token"):
                try:
                    from tgtg_api.client import TgtgClient
                    self.tgtg_client = TgtgClient(
                        access_token=tokens.get("access_token"),
                        refresh_token=tokens.get("refresh_token"),
                        datadome_cookie=tokens.get("datadome_cookie"),
                    )
                    log.info("Client created from tokens")
                except Exception as e:
                    log.error(f"Client init error: {e}")
                    self.token_storage.clear()
            
            try:
                from services.android_service import NotificationService
                self.notification_service = NotificationService()
                log.info("Notification service initialized")
            except Exception as e:
                log.warning(f"Notification service not available: {e}")
                self.notification_service = None
            
            sm.current = "favorites" if self.tgtg_client else "login"
        except Exception as e:
            log.error(f"Init error: {e}")
            sm.current = "login"

    def logout(self):
        if self.token_storage:
            self.token_storage.clear()
        self.tgtg_client = None


from screens.favorites import FavoritesScreen
from screens.item_detail import ItemDetailScreen
from screens.settings import SettingsScreen


if __name__ == "__main__":
    TgtgMobileApp().run()
