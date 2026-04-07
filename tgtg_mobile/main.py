import logging
import os
import sys

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("tgtg_mobile")
log.info(f"Python: {sys.version}")

# Load KV files helper
def load_kv(path):
    from kivy.lang import Builder
    for base in [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]:
        full_path = os.path.join(base, path)
        if os.path.exists(full_path):
            try:
                Builder.load_file(full_path)
                log.info(f"Loaded KV: {full_path}")
                return True
            except Exception as e:
                log.error(f"Failed to load {full_path}: {e}")
    return False

try:
    from kivy import require
    from kivy.app import App
    from kivy.uix.screenmanager import ScreenManager, Screen
    from kivy.clock import Clock
    require("2.3.0")
    
    # Load KV files after Kivy is available
    load_kv("screens/splash.kv")
    load_kv("screens/login.kv")
    load_kv("screens/favorites.kv")
    load_kv("screens/item_detail.kv")
    load_kv("screens/settings.kv")
    
    # Import screen classes after KV files are loaded
    from screens.login import LoginScreen
    from screens.favorites import FavoritesScreen
    from screens.item_detail import ItemDetailScreen
    from screens.settings import SettingsScreen
    
except Exception as e:
    log.error(f"Kivy import failed: {e}")
    import traceback
    traceback.print_exc()
    raise


class SplashScreen(Screen):
    def __init__(self, **kwargs):
        log.info("SplashScreen init")
        super().__init__(**kwargs)

    def on_enter(self):
        log.info("SplashScreen on_enter")
        Clock.schedule_once(self.check_auth, 1.5)

    def check_auth(self, dt):
        log.info("SplashScreen check_auth")
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
        sm.add_widget(LoginScreen(name="login"))
        sm.add_widget(FavoritesScreen(name="favorites"))
        sm.add_widget(ItemDetailScreen(name="item_detail"))
        sm.add_widget(SettingsScreen(name="settings"))
        
        Clock.schedule_once(lambda dt: self.init_app(sm), 0)
        
        return sm

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
                from services.notification_manager import NotificationManager
                self.notification_manager = NotificationManager()
                log.info("Notification manager initialized")
            except Exception as e:
                log.warning("Notification manager not available: %s", e)
                self.notification_manager = None

            # Keep backward compat
            self.notification_service = self.notification_manager
            
            sm.current = "favorites" if self.tgtg_client else "login"
        except Exception as e:
            log.error(f"Init error: {e}")
            import traceback
            traceback.print_exc()
            sm.current = "login"

    def logout(self):
        if self.token_storage:
            self.token_storage.clear()
        self.tgtg_client = None

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
            log.error("Failed to restore service on resume: %s", e)


if __name__ == "__main__":
    TgtgMobileApp().run()
