"""Too Good To Go client for the scanner.

Thin subclass of the official ``tgtg`` package with only scanner-specific behavior:
browser PIN login (Docker) and paginated favorites.
"""

from __future__ import annotations

import builtins
import contextlib
import json
import logging
import re
import sys
import webbrowser
from collections.abc import Iterator
from http import HTTPStatus

import tgtg
from tgtg import BASE_URL
from tgtg import TgtgClient as _UpstreamTgtgClient
from tgtg.exceptions import TgtgAPIError

from tgtg_scanner.pin_prompt import prompt_via_browser

log = logging.getLogger("tgtg")

_DATADOME_RE = re.compile(r"datadome=([^;]+)", re.IGNORECASE)

# App-shaped supported payment methods (decompiled from APK 26.10.0, matches PaymentType enum).
DEFAULT_SUPPORTED_TYPES: list[dict] = [
    {
        "provider": "ADYEN",
        "payment_types": [
            "CREDITCARD",
            "SOFORT",
            "IDEAL",
            "PAYPAL",
            "BCMCMOBILE",
            "BCMCCARD",
            "VIPPS",
            "TWINT",
            "MBWAY",
            "BLIK",
            "CASH_APP_PAY",
            "GOOGLEPAY",
        ],
    },
    {"provider": "CHARITY", "payment_types": ["CHARITY"]},
    {"provider": "SATISPAY", "payment_types": ["SATISPAY"]},
    {"provider": "EDENRED", "payment_types": ["EDENRED"]},
]


def normalize_cookie(value: str | None) -> str | None:
    """Map config Datadome (raw token or Cookie header) to upstream cookie."""
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if "=" in value:
        return value
    return f"datadome={value}"


def extract_datadome(client: _UpstreamTgtgClient) -> str:
    """Datadome token for config.ini / token files (legacy field name)."""
    values = [c.value for c in client.session.cookies if c.name == "datadome" and c.value]
    if values:
        return values[-1]
    if client.cookie:
        match = _DATADOME_RE.search(client.cookie)
        if match:
            return match.group(1)
        if "=" not in client.cookie:
            return client.cookie
    return ""


def resolve_user_agent(user_agent: str | None = None, apk_version: str | None = None) -> str | None:
    """Optional UA override for config. None lets upstream pick APK version + UA."""
    if user_agent:
        return user_agent
    if apk_version:
        # Minimal override when only APK version is configured
        return f"TGTG/{apk_version} Dalvik/2.1.0 (Linux; U; Android 12; SM-G920V Build/MMB29K)"
    return None


@contextlib.contextmanager
def _patched_input(value: str) -> Iterator[None]:
    original = builtins.input
    builtins.input = lambda *_args, **_kwargs: value  # type: ignore[assignment]
    try:
        yield
    finally:
        builtins.input = original


class TgtgClient(_UpstreamTgtgClient):
    """Official tgtg client + browser PIN login + full favorites pagination."""

    def __init__(
        self,
        *args,
        pin_port: int = 0,
        max_polling_tries: int | None = None,
        polling_wait_time: int | None = None,
        **kwargs,
    ) -> None:
        self.pin_port = pin_port
        if max_polling_tries is not None:
            tgtg.MAX_POLLING_TRIES = max_polling_tries
        if polling_wait_time is not None:
            tgtg.POLLING_WAIT_TIME = polling_wait_time
        if kwargs.get("url") and kwargs["url"] != BASE_URL:
            log.warning("Using custom tgtg base url: %s", kwargs["url"])
        super().__init__(*args, **kwargs)

    def login(self) -> None:
        try:
            super().login()
        except TypeError as err:
            from tgtg_scanner.errors import TgtgConfigurationError

            raise TgtgConfigurationError(str(err)) from err

    def start_polling(self, polling_id: str) -> None:
        """Use browser PIN form, then defer to upstream (PIN auth or email-link poll)."""
        pin = prompt_via_browser(email=self.email, port=self.pin_port) or ""
        with _patched_input(pin):
            super().start_polling(polling_id)

    def _post(self, url, **kwargs):
        """POST with DataDome captcha fallback.

        First try upstream's SDK cookie approach. If that still returns 403
        with a DataDome interstitial, open the captcha in the browser and
        prompt the user to paste the fresh datadome cookie.
        """
        # Upstream _post: ensure SDK cookie, POST, on 403 clear + retry once
        response = super()._post(url, **kwargs)
        if response.status_code != 403:
            return response

        # Check if it's a DataDome captcha interstitial
        try:
            body = response.json()
            captcha_url = body.get("url", "")
        except (json.JSONDecodeError, AttributeError):
            return response

        if "captcha-delivery.com" not in captcha_url:
            return response

        # DataDome captcha detected — try to open in browser
        log.warning("DataDome captcha challenge received.")
        try:
            webbrowser.open(captcha_url)
            log.info("Opened captcha in browser. If it didn't open, visit:\n  %s", captcha_url)
        except Exception:
            log.warning("Could not open browser. Please visit this URL manually:\n  %s", captcha_url)

        # Prompt user for the datadome cookie
        if not sys.stdin.isatty():
            log.warning("Non-interactive session — cannot prompt for datadome cookie.")
            return response

        sys.stdout.write(
            "Solve the captcha, then copy the 'datadome' cookie value "
            "from your browser (e.g. via DevTools → Application → Cookies) "
            "and paste it here:\n> "
        )
        sys.stdout.flush()
        try:
            fresh_cookie = sys.stdin.readline().strip()
        except (EOFError, KeyboardInterrupt):
            return response

        if not fresh_cookie:
            return response

        # Strip wrapping if user pasted full "datadome=xxx" or "datadome=xxx; ..."
        match = _DATADOME_RE.search(fresh_cookie)
        if match:
            fresh_cookie = match.group(1)

        domain = tgtg.urlsplit(self.base_url).hostname
        self.session.cookies.set(
            "datadome",
            fresh_cookie,
            domain=f".{domain}",
            path="/",
            secure=True,
        )
        self.cookie = f"datadome={fresh_cookie}"

        # Retry the request with the fresh cookie
        return self.session.post(
            url,
            headers=self._headers,
            proxies=self.proxies,
            timeout=self.timeout,
            **kwargs,
        )

    def get_favorites(self, **kwargs) -> list[dict]:
        """Fetch all favorites via upstream paging (page_size 100)."""
        items: list[dict] = []
        page = 0
        page_size = 100
        while True:
            batch = super().get_favorites(page=page, page_size=page_size, **kwargs)
            items.extend(batch)
            if len(batch) < page_size:
                break
            page += 1
        return items

    def get_credentials(self) -> dict:
        """Credentials in the shape the scanner CLI / config persistence expect."""
        self.login()
        return {
            "email": self.email,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "datadome_cookie": extract_datadome(self),
        }

    def get_payment_methods(self, item_id: str | None = None, supported_types: list[dict] | None = None) -> dict:
        """List saved payment methods (global) or payable methods for an item (APK 26.10.0: paymentMethod/v2)."""
        self.login()
        if item_id:
            path = f"paymentMethod/v2/item/{item_id}"
            body: dict | None = {"supported_types": supported_types if supported_types is not None else DEFAULT_SUPPORTED_TYPES}
        else:
            path = "paymentMethod/v2"
            body = None
        response = self._post(self._get_url(path), json=body)
        if response.status_code != HTTPStatus.OK:
            raise TgtgAPIError(response.status_code, response.content)
        return response.json()

    def get_vouchers(self) -> dict:
        """List active vouchers (APK 26.10.0: voucher/v4/active)."""
        self.login()
        response = self._post(self._get_url("voucher/v4/active"))
        if response.status_code != HTTPStatus.OK:
            raise TgtgAPIError(response.status_code, response.content)
        return response.json()

    def create_payment(self, order_id: str) -> dict:
        """Initialize the payment session for an order (APK 26.10.0: payment/v4/order/{orderId})."""
        self.login()
        response = self._post(self._get_url(f"payment/v4/order/{order_id}"))
        if response.status_code != HTTPStatus.OK:
            raise TgtgAPIError(response.status_code, response.content)
        return response.json()

    def payment_biometrics(self, payment_id: str) -> dict:
        """Fetch saved-card auth material for a payment session (APK 26.10.0: payment/v4/{paymentId}/biometrics)."""
        self.login()
        response = self._post(self._get_url(f"payment/v4/{payment_id}/biometrics"))
        if response.status_code != HTTPStatus.OK:
            raise TgtgAPIError(response.status_code, response.content)
        return response.json()

    def pay_order(self, order_id: str, payment: dict) -> dict:
        """Post a payment authorization for an order (v8 endpoint, v7 fallback)."""
        self.login()
        last_error: TgtgAPIError | None = None
        for endpoint in ("order/v8/{}/pay", "order/v7/{}/pay"):
            response = self._post(self._get_url(endpoint.format(order_id)), json=payment)
            if response.status_code == HTTPStatus.NOT_FOUND:
                last_error = TgtgAPIError(response.status_code, response.content)
                continue
            if response.status_code == HTTPStatus.OK:
                return response.json()
            raise TgtgAPIError(response.status_code, response.content)
        raise last_error if last_error is not None else TgtgAPIError(404, b"no pay endpoint")

    def get_payment_status(self, payment_id: str) -> dict:
        """Poll payment state for a payment started via pay_order (APK 26.10.0: payment/v4)."""
        self.login()
        response = self._post(self._get_url(f"payment/v4/{payment_id}"))
        if response.status_code != HTTPStatus.OK:
            raise TgtgAPIError(response.status_code, response.content)
        return response.json()

    def get_order_detail(self, order_id: str) -> dict:
        """Fetch the full order (APK 26.10.0 md9.t: order/v8/{orderId} -> {order: ...})."""
        self.login()
        response = self._post(self._get_url(f"order/v8/{order_id}"))
        if response.status_code != HTTPStatus.OK:
            raise TgtgAPIError(response.status_code, response.content)
        data = response.json()
        order = data.get("order") if isinstance(data, dict) else None
        return order if isinstance(order, dict) else data
