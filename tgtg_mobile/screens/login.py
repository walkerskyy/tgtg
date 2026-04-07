from threading import Thread

from kivy.app import App
from kivy.clock import mainthread
from kivy.uix.screenmanager import Screen
from kivy.properties import StringProperty, BooleanProperty

from tgtg_api.client import TgtgClient
from errors import TgtgLoginError, TgtgPollingError, TgtgConfigurationError


class LoginScreen(Screen):
    status_message = StringProperty("")
    is_loading = BooleanProperty(False)
    polling_id = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tgtg_client: TgtgClient | None = None

    def login_with_tokens(self, access_token: str, refresh_token: str):
        if not access_token or not access_token.strip():
            self.status_message = "Please enter access token"
            return
        if not refresh_token or not refresh_token.strip():
            self.status_message = "Please enter refresh token"
            return

        self.is_loading = True
        self.status_message = "Logging in with tokens..."

        def login():
            try:
                self.tgtg_client = TgtgClient(
                    access_token=access_token.strip(),
                    refresh_token=refresh_token.strip(),
                )
                if self.tgtg_client.verify_tokens():
                    self._on_login_success()
                else:
                    self._on_login_error("Invalid or expired tokens")
            except Exception as e:
                self._on_login_error(str(e))

        Thread(target=login, daemon=True).start()

    def request_login(self, email):
        if not email or not email.strip():
            self.status_message = "Please enter your email"
            return

        self.is_loading = True
        self.status_message = "Sending login request..."

        def login():
            try:
                self.tgtg_client = TgtgClient(email=email.strip())
                polling_id = self.tgtg_client.request_pin()
                if polling_id:
                    self.polling_id = polling_id
                    self._on_pin_required()
                else:
                    self._on_login_error("Failed to send PIN")
            except TgtgPollingError as e:
                self._on_login_error(str(e))
            except Exception as e:
                self._on_login_error(str(e))

        Thread(target=login, daemon=True).start()

    def submit_pin(self, pin):
        if not pin or not pin.strip():
            self.status_message = "Please enter the PIN"
            return

        if not self.tgtg_client or not self.polling_id:
            self.status_message = "Session expired. Please request new PIN"
            return

        self.is_loading = True
        self.status_message = "Verifying PIN..."

        def verify():
            try:
                success = self.tgtg_client.verify_pin(self.polling_id, pin.strip())
                if success:
                    self._on_login_success()
                else:
                    self._on_login_error("Invalid PIN")
            except Exception as e:
                self._on_login_error(str(e))

        Thread(target=verify, daemon=True).start()

    @mainthread
    def _on_pin_required(self):
        self.is_loading = False
        self.status_message = "PIN sent! Check your email and enter it below."

    @mainthread
    def _on_login_success(self):
        self.is_loading = False
        self.status_message = ""
        
        app = App.get_running_app()
        if app and self.tgtg_client:
            app.tgtg_client = self.tgtg_client
            if app.token_storage:
                app.token_storage.save(
                    access_token=self.tgtg_client.access_token,
                    refresh_token=self.tgtg_client.refresh_token,
                    datadome_cookie=self.tgtg_client.datadome_cookie,
                )
        self.manager.current = "favorites"

    @mainthread
    def _on_login_error(self, error):
        self.is_loading = False
        self.status_message = f"Error: {error}"
