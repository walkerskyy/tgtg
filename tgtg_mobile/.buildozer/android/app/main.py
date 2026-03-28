import logging
import os
import sys
from threading import Thread

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger("tgtg_mobile")

try:
    from kivy import require
    from kivy.app import App
    from kivy.uix.screenmanager import ScreenManager, Screen
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.label import Label
    from kivy.uix.textinput import TextInput
    from kivy.uix.button import Button
    from kivy.clock import Clock
    require("2.3.0")
except ImportError as e:
    log.error(f"Kivy import failed: {e}")
    raise


class SplashScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()

    def build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=50)
        title = Label(text="TGTG Scanner", font_size="32sp", bold=True, color=(1, 1, 1, 1))
        self.status = Label(text="Starting...", font_size="14sp", color=(0.7, 0.7, 0.7, 1))
        layout.add_widget(title)
        layout.add_widget(self.status)
        self.add_widget(layout)


class LoginScreen(Screen):
    polling_id = ""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()

    def build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=50, spacing=20)
        
        title = Label(text="TGTG Scanner", font_size="28sp", bold=True, color=(1, 1, 1, 1))
        
        self.email_input = TextInput(
            hint_text="Your TGTG email",
            multiline=False,
            input_type="email",
            size_hint_y=0.1,
            height=50,
            font_size="16sp"
        )
        
        self.status_label = Label(text="", font_size="14sp", color=(1, 0.5, 0.5, 1))
        
        login_btn = Button(
            text="Send PIN",
            size_hint_y=0.1,
            height=50,
            background_color=(0.29, 0.76, 0.26, 1),
            font_size="16sp",
            bold=True
        )
        login_btn.bind(on_press=self.do_send_pin)
        
        layout.add_widget(title)
        layout.add_widget(self.email_input)
        layout.add_widget(login_btn)
        layout.add_widget(self.status_label)
        self.add_widget(layout)

    def do_send_pin(self, instance):
        email = self.email_input.text.strip()
        if not email:
            self.status_label.text = "Please enter email"
            return
        
        self.status_label.text = "Sending PIN..."
        self.email_input.disabled = True
        
        def send():
            try:
                from tgtg_api.client import TgtgClient
                client = TgtgClient(email=email)
                polling_id = client.request_pin()
                
                if polling_id:
                    app = App.get_running_app()
                    app.tgtg_client = client
                    app.pending_email = email
                    self.polling_id = polling_id
                    self.on_pin_requested(client, polling_id)
                else:
                    self.on_error("Failed to send PIN")
            except Exception as e:
                log.error(f"Send PIN error: {e}")
                self.on_error(str(e))
        
        Thread(target=send, daemon=True).start()

    def on_pin_requested(self, client, polling_id):
        Clock.schedule_once(lambda dt: self._show_pin_screen(polling_id), 0)

    def _show_pin_screen(self, polling_id):
        self.email_input.disabled = False
        self.manager.get_screen("pin").setup(client=None, polling_id=polling_id)
        self.manager.current = "pin"


class PinScreen(Screen):
    polling_id = ""
    client = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()

    def build_ui(self):
        self.layout = BoxLayout(orientation="vertical", padding=50, spacing=20)
        
        title = Label(text="Enter PIN", font_size="28sp", bold=True, color=(1, 1, 1, 1))
        self.subtitle = Label(text="Check your email for the 6-digit code", font_size="14sp", color=(0.7, 0.7, 0.7, 1))
        
        self.pin_input = TextInput(
            hint_text="6-digit PIN",
            multiline=False,
            input_type="number",
            password=True,
            size_hint_y=0.1,
            height=50,
            font_size="24sp"
        )
        
        self.status_label = Label(text="", font_size="14sp", color=(1, 0.5, 0.5, 1))
        
        verify_btn = Button(
            text="Verify",
            size_hint_y=0.1,
            height=50,
            background_color=(0.29, 0.76, 0.26, 1),
            font_size="16sp",
            bold=True
        )
        verify_btn.bind(on_press=self.do_verify)
        
        back_btn = Button(
            text="Back to Login",
            size_hint_y=0.1,
            height=50,
            background_color=(0.4, 0.4, 0.4, 1),
            font_size="14sp"
        )
        back_btn.bind(on_press=self.go_back)
        
        self.layout.add_widget(title)
        self.layout.add_widget(self.subtitle)
        self.layout.add_widget(self.pin_input)
        self.layout.add_widget(verify_btn)
        self.layout.add_widget(back_btn)
        self.layout.add_widget(self.status_label)
        self.add_widget(self.layout)

    def setup(self, client, polling_id):
        self.client = client
        self.polling_id = polling_id
        self.pin_input.text = ""
        self.status_label.text = ""

    def do_verify(self, instance):
        pin = self.pin_input.text.strip()
        if not pin or len(pin) != 6:
            self.status_label.text = "Please enter 6-digit PIN"
            return
        
        if not self.client or not self.polling_id:
            self.status_label.text = "Session expired. Please login again."
            return
        
        self.status_label.text = "Verifying..."
        self.pin_input.disabled = True
        
        def verify():
            try:
                success = self.client.verify_pin(self.polling_id, pin)
                if success:
                    self.on_success()
                else:
                    self.on_error("Invalid PIN")
            except Exception as e:
                log.error(f"Verify error: {e}")
                self.on_error(str(e))
        
        Thread(target=verify, daemon=True).start()

    def on_success(self):
        Clock.schedule_once(lambda dt: self._do_success(), 0)

    def _do_success(self):
        app = App.get_running_app()
        if app and self.client:
            app.tgtg_client = self.client
            if hasattr(app, 'token_storage') and app.token_storage:
                app.token_storage.save(
                    access_token=self.client.access_token,
                    refresh_token=self.client.refresh_token,
                    datadome_cookie=self.client.datadome_cookie,
                )
        self.status_label.text = "Login successful!"
        self.manager.current = "favorites"

    def on_error(self, error):
        Clock.schedule_once(lambda dt: self._show_error(error), 0)

    def _show_error(self, error):
        self.status_label.text = error
        self.pin_input.disabled = False

    def go_back(self, instance):
        self.pin_input.disabled = False
        self.manager.current = "login"


class FavoritesScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()

    def build_ui(self):
        layout = BoxLayout(orientation="vertical")
        
        header = BoxLayout(size_hint_y=None, height=56, padding=[15, 0])
        header.add_widget(Label(text="My Favorites", font_size="20sp", bold=True, color=(1, 1, 1, 1)))
        
        back_btn = Button(text="Logout", size_hint_x=None, width=80)
        back_btn.bind(on_press=lambda x: setattr(self.manager, 'current', 'settings'))
        header.add_widget(back_btn)
        
        layout.add_widget(header)
        
        self.content = Label(text="Loading...", color=(0.7, 0.7, 0.7, 1))
        layout.add_widget(self.content)
        self.add_widget(layout)

    def on_enter(self):
        self.load_favorites()

    def load_favorites(self):
        app = App.get_running_app()
        if not app or not hasattr(app, 'tgtg_client') or not app.tgtg_client:
            self.content.text = "Not logged in"
            return
        
        self.content.text = "Fetching favorites..."
        
        def fetch():
            try:
                favorites = app.tgtg_client.get_favorites()
                self.on_favorites(favorites)
            except Exception as e:
                log.error(f"Fetch error: {e}")
                self.on_error(str(e))
        
        Thread(target=fetch, daemon=True).start()

    def on_favorites(self, favorites):
        Clock.schedule_once(lambda dt: self._update_favorites(favorites), 0)

    def _update_favorites(self, favorites):
        if not favorites:
            self.content.text = "No favorites found"
            return
        
        text = f"Found {len(favorites)} favorites:\n\n"
        for item_data in favorites[:10]:
            try:
                from models.item import Item
                item = Item(item_data)
                text += f"• {item.display_name} - {item.price}\n"
            except Exception as e:
                log.error(f"Item error: {e}")
                text += f"• (error parsing item)\n"
        
        self.content.text = text

    def on_error(self, error):
        Clock.schedule_once(lambda dt: self._update_error(error), 0)

    def _update_error(self, error):
        self.content.text = f"Error: {error}"


class SettingsScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()

    def build_ui(self):
        layout = BoxLayout(orientation="vertical", padding=20, spacing=20)
        
        header = BoxLayout(size_hint_y=None, height=56)
        back_btn = Button(text="Back", size_hint_x=None, width=80)
        back_btn.bind(on_press=lambda x: setattr(self.manager, 'current', 'favorites'))
        title = Label(text="Settings", font_size="18sp", bold=True, color=(1, 1, 1, 1), size_hint_x=1)
        header.add_widget(back_btn)
        header.add_widget(title)
        
        logout_btn = Button(
            text="Logout",
            size_hint_y=0.1,
            height=50,
            background_color=(0.9, 0.3, 0.3, 1),
            font_size="16sp"
        )
        logout_btn.bind(on_press=self.logout)
        
        version = Label(text="Version 0.5.0", color=(0.5, 0.5, 0.5, 1))
        
        layout.add_widget(header)
        layout.add_widget(Label())
        layout.add_widget(logout_btn)
        layout.add_widget(version)
        self.add_widget(layout)

    def logout(self, instance):
        app = App.get_running_app()
        if app:
            if hasattr(app, 'token_storage') and app.token_storage:
                app.token_storage.clear()
            app.tgtg_client = None
        self.manager.current = "login"


class TgtgMobileApp(App):
    token_storage = None
    tgtg_client = None
    pending_email = ""

    def build(self):
        log.info("Building app...")
        sm = ScreenManager()
        
        sm.add_widget(SplashScreen(name="splash"))
        sm.add_widget(LoginScreen(name="login"))
        sm.add_widget(PinScreen(name="pin"))
        sm.add_widget(FavoritesScreen(name="favorites"))
        sm.add_widget(SettingsScreen(name="settings"))
        
        Clock.schedule_once(lambda dt: self.init_app(sm), 0.5)
        
        return sm

    def init_app(self, sm):
        log.info("Initializing app...")
        try:
            from services.token_storage import TokenStorage
            self.token_storage = TokenStorage()
            tokens = self.token_storage.load()
            log.info(f"Tokens loaded: {bool(tokens)}")
            
            if tokens:
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
            
            sm.current = "favorites" if self.tgtg_client else "login"
        except Exception as e:
            log.error(f"Init error: {e}")
            sm.current = "login"


if __name__ == "__main__":
    TgtgMobileApp().run()
