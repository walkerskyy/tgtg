# Simplified TGTG API client for mobile
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
import urllib3
from urllib3.util import Retry

log = logging.getLogger("tgtg_mobile")


class TgtgAPIError(Exception):
    pass


class TgtgConfigurationError(Exception):
    pass


class TgtgLoginError(Exception):
    pass


class TgtgPollingError(Exception):
    pass

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


class TgtgSession:
    http_pool = None

    def __init__(
        self,
        user_agent: str | None = None,
        apk_version: str | None = None,
        language: str = "en-UK",
        timeout: int | None = None,
        base_url: str = BASE_URL,
    ):
        self.headers = {
            "Accept-Language": language,
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Accept-Encoding": "gzip",
            "x-correlation-id": str(uuid.uuid4()),
        }
        self.user_agent = user_agent or self._get_user_agent(apk_version)
        self.headers["User-Agent"] = self.user_agent
        self.timeout = timeout or 30
        self.base_url = base_url
        self.last_api_request: datetime | None = None
        self.cert_paths = certifi.where()

        self._datadome_cache_cookie: str | None = None
        self._datadome_cache_expires_at: float | None = None
        self._datadome_cache_duration_s = 5 * 60

        self._init_pool()

    def _init_pool(self):
        if TgtgSession.http_pool is None:
            TgtgSession.http_pool = urllib3.PoolManager(
                cert_reqs="CERT_REQUIRED",
                ca_certs=self.cert_paths,
                maxsize=10,
                retries=Retry(
                    total=5,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["GET", "POST"],
                    backoff_factor=1,
                ),
            )

    def _get_user_agent(self, apk_version: str | None = None) -> str:
        version = apk_version or DEFAULT_APK_VERSION
        return random.choice(USER_AGENTS).format(version)

    def _wait_between_requests(self) -> None:
        if self.last_api_request:
            wait = max(0, DEFAULT_MIN_TIME_BETWEEN_REQUESTS - (datetime.now() - self.last_api_request).seconds)
            if wait > 0:
                log.debug(f"Waiting {wait} seconds between requests")
                time.sleep(wait)

    def _ensure_datadome_cookie(self, url: str) -> None:
        if self._datadome_cache_valid() or self._datadome_cookie_present():
            return
        cid = self._generate_datadome_cid()
        cookie = self._fetch_datadome_cookie(request_url=url, cid=cid)
        if cookie:
            self._set_datadome_cookie(cookie)

    def _datadome_cache_valid(self) -> bool:
        if not self._datadome_cache_cookie or not self._datadome_cache_expires_at:
            return False
        return time.time() < self._datadome_cache_expires_at

    def _datadome_cookie_present(self) -> bool:
        return self._datadome_cache_cookie is not None

    @staticmethod
    def _generate_datadome_cid() -> str:
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789~_"
        return "".join(random.choice(chars) for _ in range(120))

    def _set_datadome_cookie(self, cookie_value: str) -> None:
        self._datadome_cache_cookie = cookie_value
        self._datadome_cache_expires_at = time.time() + self._datadome_cache_duration_s

    def _fetch_datadome_cookie(self, request_url: str, cid: str) -> str | None:
        params = {
            "camera": '{"auth":"true", "info":"{\\"front\\":\\"2000x1500\\",\\"back\\":\\"5472x3648\\"}"}',
            "cid": cid,
            "ddk": "1D42C2CA6131C526E09F294FE96F94",
            "ddv": "3.0.4",
            "ddvc": DEFAULT_APK_VERSION,
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
            r = urllib3.PoolManager().request(
                "POST",
                url,
                fields=params,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "*/*",
                    "User-Agent": self.user_agent,
                },
                timeout=10,
            )
            if r.status == 200:
                data = json.loads(r.data)
                if data.get("status") == 200 and data.get("cookie"):
                    m = re.search(r"datadome=([^;]+)", data["cookie"])
                    if m:
                        return m.group(1)
        except Exception as e:
            log.debug("Error fetching DataDome cookie: %s", e)
        return None

    def get(self, path: str, access_token: str | None = None) -> dict:
        return self._request("GET", path, access_token=access_token)

    def post(self, path: str, json_data: dict | None = None, access_token: str | None = None) -> dict:
        return self._request("POST", path, json_data=json_data, access_token=access_token)

    def _request(
        self,
        method: str,
        path: str,
        json_data: dict | None = None,
        access_token: str | None = None,
    ) -> dict:
        self._wait_between_requests()

        url = urljoin(self.base_url, path)
        self._ensure_datadome_cookie(url)

        headers = self.headers.copy()
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        if self._datadome_cache_cookie:
            headers["Cookie"] = f"datadome={self._datadome_cache_cookie}"

        body = json.dumps(json_data) if json_data else None

        try:
            response = self.http_pool.request(
                method,
                url,
                body=body,
                headers=headers,
                timeout=self.timeout,
            )
            self.last_api_request = datetime.now()

            if response.status == HTTPStatus.OK:
                return json.loads(response.data)
            elif response.status == HTTPStatus.ACCEPTED:
                return json.loads(response.data)
            elif response.status == HTTPStatus.FORBIDDEN:
                log.warning("Received 403 - may need new DataDome cookie")
                self._datadome_cache_cookie = None
                raise TgtgAPIError(response.status, response.data)
            else:
                raise TgtgAPIError(response.status, response.data)

        except urllib3.exceptions.HTTPError as e:
            log.error(f"HTTP error: {e}")
            raise TgtgAPIError from e


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

        self.session = TgtgSession(
            user_agent=user_agent,
            apk_version=apk_version,
            language=language,
            timeout=timeout,
            base_url=base_url,
        )

    @property
    def _already_logged(self) -> bool:
        return bool(self.access_token and self.refresh_token)

    def _refresh_token(self) -> None:
        if (
            self.last_time_token_refreshed
            and (datetime.now() - self.last_time_token_refreshed).seconds <= self.access_token_lifetime
        ):
            return
        response = self.session.post(
            REFRESH_ENDPOINT,
            json_data={"refresh_token": self.refresh_token},
        )
        self.access_token = response.get("access_token")
        self.refresh_token = response.get("refresh_token")
        self.last_time_token_refreshed = datetime.now()

    def request_pin(self) -> str | None:
        """Request a PIN to be sent to email. Returns polling_id if successful."""
        if not self.email:
            raise TgtgConfigurationError("Email is required")
        
        log.info("Requesting PIN...")
        response = self.session.post(
            AUTH_BY_EMAIL_ENDPOINT,
            json_data={
                "device_type": self.device_type,
                "email": self.email,
            },
        )
        
        if response.get("state") == "TERMS":
            raise TgtgPollingError(
                f"This email {self.email} is not linked to a tgtg account. Please signup first."
            )
        
        if response.get("state") == "WAIT":
            polling_id = response.get("polling_id")
            log.info(f"PIN requested, polling_id: {polling_id}")
            return polling_id
        
        log.error(f"Unexpected login state: {response.get('state')}")
        return None

    def verify_pin(self, polling_id: str, pin: str) -> bool:
        """Verify the PIN. Returns True if successful."""
        log.info("Verifying PIN...")
        response = self.session.post(
            AUTH_BY_REQUEST_PIN_ENDPOINT,
            json_data={
                "device_type": self.device_type,
                "email": self.email,
                "request_pin": pin,
                "request_polling_id": polling_id,
            },
        )
        
        if response.get("access_token"):
            self.access_token = response.get("access_token")
            self.refresh_token = response.get("refresh_token")
            self.last_time_token_refreshed = datetime.now()
            log.info("Login successful!")
            return True
        
        log.error("PIN verification failed")
        return False

    def login(self) -> None:
        """Legacy login method - use request_pin + verify_pin instead."""
        polling_id = self.request_pin()
        if not polling_id:
            raise TgtgLoginError("Failed to request PIN")
        raise TgtgLoginError("PIN required - use verify_pin instead")

    def login_with_pin(self, polling_id: str, pin: str) -> None:
        """Legacy login method - use verify_pin instead."""
        if not self.verify_pin(polling_id, pin):
            raise TgtgLoginError("Failed to login with PIN")

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
        response = self.session.post(API_ITEM_ENDPOINT, json_data=data, access_token=self.access_token)
        return response.get("items", [])

    def get_item(self, item_id: str) -> dict:
        if self._already_logged:
            self._refresh_token()
        response = self.session.post(
            f"{API_ITEM_ENDPOINT}/{item_id}",
            json_data={"origin": None},
            access_token=self.access_token,
        )
        return response

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
        self.session.post(
            FAVORITE_ITEM_ENDPOINT.format(item_id),
            json_data={"is_favorite": is_favorite},
            access_token=self.access_token,
        )
