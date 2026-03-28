from threading import Thread

from kivy.clock import mainthread
from kivy.uix.screenmanager import Screen
from kivy.properties import StringProperty, BooleanProperty

from tgtg_api.client import TgtgClient
from errors import TgtgLoginError, TgtgPollingError


class LoginScreen(Screen):
    status_message = StringProperty("")
    is_loading = BooleanProperty(False)
    polling_id = StringProperty("")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.tgtg_client: TgtgClient | None = None

    def request_login(self, email):
        if not email or not email.strip():
            self.status_message = "Please enter your email"
            return

        self.is_loading = True
        self.status_message = "Sending login request..."

        def login():
            try:
                client = TgtgClient(email=email.strip())
                result = client.login()
                self._on_login_response(client, result)
            except TgtgPollingError as e:
                self._on_pin_required(client)
            except TgtgLoginError as e:
                self._on_login_error(e)
            except Exception as e:
                self._on_login_error(e)

        Thread(target=login, daemon=True).start()

    @mainthread
    def _on_login_response(self, client, result):
        pass

    @mainthread
    def _on_pin_required(self, client):
        self.tgtg_client = client
        self.is_loading = False
        self.status_message = "PIN sent! Check your email."

    @mainthread
    def _on_login_error(self, error):
        self.is_loading = False
        self.status_message = f"Error: {error}"

    @mainthread
    def _on_login_success(self):
        if self.tgtg_client:
            app = self.manager.app
            if app:
                app.tgtg_client = self.tgtg_client
                if app.token_storage:
                    app.token_storage.save(
                        access_token=self.tgtg_client.access_token,
                        refresh_token=self.tgtg_client.refresh_token,
                        datadome_cookie=self.tgtg_client.datadome_cookie,
                    )
            self.manager.current = "favorites"

    def submit_pin(self, pin):
        if not pin or not pin.strip():
            self.status_message = "Please enter the PIN"
            return

        if not self.tgtg_client:
            self.status_message = "Please request login first"
            return

        self.is_loading = True
        self.status_message = "Verifying PIN..."

        def verify():
            try:
                self.tgtg_client.login_with_pin(self.polling_id, pin.strip())
                self._on_login_success()
            except Exception as e:
                self._on_login_error(e)

        Thread(target=verify, daemon=True).start()
