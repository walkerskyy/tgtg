# TGTG API client for mobile
# Based on tgtg_scanner/tgtg/tgtg_client.py

import json
import logging
import random
import re
import time
import uuid
from datetime import datetime
from http import HTTPStatus
from urllib.parse import urljoin

import certifi
import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

log = logging.getLogger("tgtg_mobile")

BASE_URL = "https://apptoogoodtogo.com/api/"
API_ITEM_ENDPOINT = "item/v8/"
FAVORITE_ITEM_ENDPOINT = "user/favorite/v1/{}/update"
AUTH_BY_EMAIL_ENDPOINT = "auth/v5/authByEmail"
AUTH_BY_REQUEST_PIN_ENDPOINT = "auth/v5/authByRequestPin"
AUTH_POLLING_ENDPOINT = "auth/v5/authByRequestPollingId"
REFRESH_ENDPOINT = "token/v1/refresh"

USER_AGENTS = [
    "TGTG/{} Dalvik/2.1.0 (Linux; U; Android 9; Nexus 5 Build/M4B30Z)",
    "TGTG/{} Dalvik/2.1.0 (Linux; U; Android 10; SM-G935F Build/NRD90M)",
    "TGTG/{} Dalvik/2.1.0 (Linux; Android 12; SM-G920V Build/MMB29K)",
]

DEFAULT_ACCESS_TOKEN_LIFETIME = 3600 * 4
DEFAULT_MAX_POLLING_TRIES = 24
DEFAULT_POLLING_WAIT_TIME = 5
DEFAULT_MIN_TIME_BETWEEN_REQUESTS = 15
DEFAULT_APK_VERSION = "24.11.0"

APK_RE_SCRIPT = re.compile(r"AF_initDataCallback\({key:\s*'ds:5'.*?data:([\s\S]*?), sideChannel:.+<\/script")


class TgtgAPIError(Exception):
    pass


class TgtgConfigurationError(Exception):
    pass


class TgtgLoginError(Exception):
    pass


class TgtgPollingError(Exception):
    pass


class TgtgSession(requests.Session):
    http_adapter = HTTPAdapter(
        max_retries=Retry(
            total=5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
            backoff_factor=1,
        )
    )

    correlation_id = str(uuid.uuid4())
    last_api_request: datetime | None = None

    _datadome_cache_cookie: str | None = None
    _datadome_cache_expires_at: float | None = None
    _datadome_cache_duration_s: int = 5 * 60

    def __init__(
        self,
        user_agent: str | None = None,
        apk_version: str | None = None,
        language: str = "en-UK",
        timeout: int | None = None,
        base_url: str = BASE_URL,
    ):
        super().__init__()
        self.mount("https://", self.http_adapter)
        self.mount("http://", self.http_adapter)
        
        self.headers = {
            "Accept-Language": language,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "gzip",
            "x-correlation-id": self.correlation_id,
        }
        if user_agent:
            self.headers["User-Agent"] = user_agent
        
        self.timeout = timeout or 30
        self.apk_version = apk_version
        self.user_agent = user_agent
        self._base_url = base_url

    def send(self, request, *args, **kwargs):
        if self.last_api_request:
            wait = max(0, DEFAULT_MIN_TIME_BETWEEN_REQUESTS - (datetime.now() - self.last_api_request).seconds)
            log.debug(f"Waiting {wait} seconds.")
            time.sleep(wait)

        response = super().send(request, *args, **kwargs)
        self.last_api_request = datetime.now()
        return response

    def post(self, *args, access_token: str | None = None, **kwargs):
        if "headers" not in kwargs:
            kwargs["headers"] = self.headers.copy()
        if access_token:
            kwargs["headers"]["authorization"] = f"Bearer {access_token}"
        return super().post(*args, **kwargs)

    def request(self, method, url, **kwargs):
        time.sleep(1)
        if "timeout" not in kwargs and self.timeout:
            kwargs["timeout"] = self.timeout
        try:
            self._ensure_datadome_cookie_for_url(url, headers=kwargs.get("headers"))
        except Exception as e:
            log.debug("DataDome auto-fetch failed (continuing without): %s", e)
        return super().request(method, url, **kwargs)

    def _ensure_datadome_cookie_for_url(self, url: str, headers: dict | None = None) -> None:
        if headers:
            ch = headers.get("Cookie") or headers.get("cookie")
            if ch and "datadome=" in ch:
                return
        if self._datadome_cache_valid() or ("datadome" in self.cookies):
            return

        cid = self._generate_datadome_cid()
        dd = self._fetch_datadome_cookie(request_url=url, cid=cid)
        if dd:
            self._set_datadome_cookie_value(dd)

    def _datadome_cache_valid(self) -> bool:
        if not self._datadome_cache_cookie or not self._datadome_cache_expires_at:
            return False
        return time.time() < self._datadome_cache_expires_at

    @staticmethod
    def _generate_datadome_cid() -> str:
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789~_"
        return "".join(random.choice(chars) for _ in range(120))

    def _set_datadome_cookie_value(self, cookie_value: str) -> None:
        domain = self._base_url.split("//")[-1].split("/")[0]
        domain = f".{'local' if domain == 'localhost' else domain}"
        self.cookies.set("datadome", cookie_value, domain=domain, path="/", secure=True)
        self._datadome_cache_cookie = cookie_value
        self._datadome_cache_expires_at = time.time() + self._datadome_cache_duration_s

    def invalidate_datadome_cache(self) -> None:
        self._datadome_cache_cookie = None
        self._datadome_cache_expires_at = None
        try:
            if "datadome" in self.cookies:
                del self.cookies["datadome"]
        except Exception:
            pass

    def _fetch_datadome_cookie(self, request_url: str, cid: str) -> str | None:
        params = {
            "camera": '{"auth":"true", "info":"{\\"front\\":\\"2000x1500\\",\\"back\\":\\"5472x3648\\"}"}',
            "cid": cid,
            "ddk": "1D42C2CA6131C526E09F294FE96F94",
            "ddv": "3.0.4",
            "ddvc": self.apk_version,
            "events": '[{"id":1,"message":"response validation","source":"sdk","date":' + str(int(time.time() * 1000)) + "}]",
            "inte": "android-java-okhttp",
            "mdl": "Pixel 7 Pro",
            "os": "Android",
            "osn": "UPSIDE_DOWN_CAKE",
            "osr": "14",
            "osv": "34",
            "request": request_url,
            "screen_d": "3.5",
            "screen_x": "1440",
            "screen_y": "3120",
            "ua": self.user_agent,
        }
        url = "https://api-sdk.datadome.co/sdk/"
        try:
            r = requests.post(
                url,
                data=params,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                    "User-Agent": self.user_agent,
                    "Accept-Encoding": "gzip, deflate, br",
                },
                timeout=10,
                verify=False,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") == 200 and data.get("cookie"):
                m = re.search(r"datadome=([^;]+)", data["cookie"])
                if m:
                    return m.group(1)
        except Exception as e:
            log.debug("Error fetching DataDome cookie: %s", e)
        return None


class TgtgClient:
    def __init__(
        self,
        base_url: str = BASE_URL,
        email: str | None = None,
        access_token: str | None = None,
        refresh_token: str | None = None,
        datadome_cookie: str | None = None,
        apk_version: str | None = None,
        user_agent: str | None = None,
        language: str = "en-GB",
        timeout: int | None = None,
        access_token_lifetime: int = DEFAULT_ACCESS_TOKEN_LIFETIME,
        max_polling_tries: int = DEFAULT_MAX_POLLING_TRIES,
        polling_wait_time: int = DEFAULT_POLLING_WAIT_TIME,
        device_type: str = "ANDROID",
        max_captcha_retries: int = 3,
    ):
        self.base_url = base_url
        self.email = email
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.datadome_cookie = datadome_cookie
        self.access_token_lifetime = access_token_lifetime
        self.max_polling_tries = max_polling_tries
        self.polling_wait_time = polling_wait_time
        self.device_type = device_type
        self.last_time_token_refreshed: datetime | None = None
        self.max_captcha_retries = max_captcha_retries

        self.fixed_user_agent = user_agent
        self.user_agent = user_agent
        self.apk_version = apk_version
        self.language = language
        self.timeout = timeout
        self.session: TgtgSession | None = None
        self.captcha_error_count = 0

    def _get_user_agent(self) -> str:
        if self.fixed_user_agent:
            return self.fixed_user_agent
        version = DEFAULT_APK_VERSION
        if self.apk_version is None:
            try:
                version = self.get_latest_apk_version()
            except Exception:
                log.warning("Failed to get latest APK version!")
        log.debug("Using APK version %s.", version)
        return random.choice(USER_AGENTS).format(version)

    @staticmethod
    def get_latest_apk_version() -> str:
        try:
            r = requests.get(
                "https://play.google.com/store/apps/details?id=com.app.tgtg&hl=en&gl=US",
                timeout=30,
                verify=certifi.where(),
            )
            text = r.text
            match = APK_RE_SCRIPT.search(text)
            if not match:
                raise TgtgAPIError("Failed to get latest APK version")
            data = json.loads(match.group(1))
            return data[1][2][140][0][0][0]
        except Exception as e:
            log.warning(f"Failed to fetch APK version: {e}, using default")
            return DEFAULT_APK_VERSION

    def _create_session(self) -> TgtgSession:
        if not self.user_agent:
            self.user_agent = self._get_user_agent()
        return TgtgSession(
            user_agent=self.user_agent,
            apk_version=self.apk_version,
            language=self.language,
            timeout=self.timeout,
            base_url=self.base_url,
        )

    @property
    def _already_logged(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    def verify_tokens(self) -> bool:
        log.info("Verifying stored tokens...")
        try:
            self._refresh_token()
            return True
        except TgtgAuthError as e:
            log.warning("Token verification failed (auth error): %s", e)
            return False
        except TgtgAPIError as e:
            log.warning("Token verification failed: %s", e)
            return False
        except Exception as e:
            log.error("Unexpected error during token verification: %s", e)
            return False

    def _refresh_token(self) -> None:
        if (
            self.last_time_token_refreshed
            and (datetime.now() - self.last_time_token_refreshed).seconds <= self.access_token_lifetime
        ):
            return
        
        try:
            response = self._post(REFRESH_ENDPOINT, json={"refresh_token": self.refresh_token})
            data = response.json()
            
            if not data.get("access_token"):
                raise TgtgAuthError("Token refresh failed: no access token returned")
            
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token", self.refresh_token)
            self.last_time_token_refreshed = datetime.now()
            log.info("Tokens refreshed successfully")
        except TgtgAPIError:
            raise
        except Exception as e:
            log.error("Token refresh error: %s", e)
            raise TgtgAuthError(f"Failed to refresh tokens: {e}")

    def _post(self, path: str, **kwargs) -> requests.Response:
        if not self.session:
            self.session = self._create_session()
        
        url = urljoin(self.base_url, path)
        response = self.session.post(url, access_token=self.access_token, **kwargs)
        self.datadome_cookie = self.session.cookies.get("datadome")
        
        if response.status_code in (HTTPStatus.OK, HTTPStatus.ACCEPTED):
            self.captcha_error_count = 0
            return response
        
        if response.status_code == HTTPStatus.FORBIDDEN:
            log.warning("Captcha Error 403!")
            self.captcha_error_count += 1
            if self.captcha_error_count >= self.max_captcha_retries:
                log.error("Max captcha retries reached, giving up")
                raise TgtgAPIError("403", "Captcha blocked after max retries")
            
            if self.session:
                self.session.invalidate_datadome_cache()
                time.sleep(0.5)
            
            if self.captcha_error_count == 1:
                self.user_agent = self._get_user_agent()
            elif self.captcha_error_count == 2:
                self.session = self._create_session()
            elif self.captcha_error_count >= 3:
                self.datadome_cookie = None
                self.session = self._create_session()
            
            time.sleep(3)
            return self._post(path, **kwargs)
        
        raise TgtgAPIError(response.status_code, response.content)

    def request_pin(self) -> str | None:
        if not self.email:
            raise TgtgConfigurationError("Email is required")
        
        log.info("Requesting PIN...")
        response = self._post(
            AUTH_BY_EMAIL_ENDPOINT,
            json={
                "device_type": self.device_type,
                "email": self.email,
            },
        )
        
        data = response.json()
        if data.get("state") == "TERMS":
            raise TgtgPollingError(
                f"This email {self.email} is not linked to a tgtg account. Please signup first."
            )
        
        if data.get("state") == "WAIT":
            polling_id = data.get("polling_id")
            log.info(f"PIN requested, polling_id: {polling_id}")
            return polling_id
        
        log.error(f"Unexpected login state: {data.get('state')}")
        return None

    def verify_pin(self, polling_id: str, pin: str) -> bool:
        log.info("Verifying PIN...")
        response = self._post(
            AUTH_BY_REQUEST_PIN_ENDPOINT,
            json={
                "device_type": self.device_type,
                "email": self.email,
                "request_pin": pin,
                "request_polling_id": polling_id,
            },
        )
        
        data = response.json()
        if data.get("access_token"):
            self.access_token = data.get("access_token")
            self.refresh_token = data.get("refresh_token")
            self.last_time_token_refreshed = datetime.now()
            log.info("Login successful!")
            return True
        
        log.error("PIN verification failed")
        return False

    def get_items(
        self,
        latitude: float = 0.0,
        longitude: float = 0.0,
        radius: int = 21,
        page_size: int = 20,
        page: int = 1,
        favorites_only: bool = True,
        with_stock_only: bool = False,
    ) -> list[dict]:
        if self._already_logged:
            self._refresh_token()
        
        data = {
            "origin": {"latitude": latitude, "longitude": longitude},
            "radius": radius,
            "page_size": page_size,
            "page": page,
            "discover": False,
            "favorites_only": favorites_only,
            "item_categories": [],
            "diet_categories": [],
            "pickup_earliest": None,
            "pickup_latest": None,
            "search_phrase": None,
            "with_stock_only": with_stock_only,
            "hidden_only": False,
            "we_care_only": False,
        }
        
        response = self._post(API_ITEM_ENDPOINT, json=data)
        return response.json().get("items", [])

    def get_item(self, item_id: str) -> dict:
        if self._already_logged:
            self._refresh_token()
        response = self._post(
            f"{API_ITEM_ENDPOINT}/{item_id}",
            json={"origin": None},
        )
        return response.json()

    def get_favorites(self) -> list[dict]:
        items = []
        page = 1
        page_size = 100
        while True:
            new_items = self.get_items(favorites_only=True, page_size=page_size, page=page)
            items += new_items
            if len(new_items) < page_size:
                break
            page += 1
        return items

    def set_favorite(self, item_id: str, is_favorite: bool) -> None:
        if self._already_logged:
            self._refresh_token()
        self._post(
            FAVORITE_ITEM_ENDPOINT.format(item_id),
            json={"is_favorite": is_favorite},
        )
