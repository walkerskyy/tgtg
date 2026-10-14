import json
from datetime import datetime, timezone
from pathlib import Path

from tgtg_scanner.captcha import (
    CaptchaChallenge,
    captcha_url_from_response,
    normalize_datadome_value,
    read_inbox_datadome,
)


class _Resp:
    def __init__(self, status_code: int, body=None, *, json_error: bool = False):
        self.status_code = status_code
        self._body = body
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise json.JSONDecodeError("expecting value", "", 0)
        return self._body


CAPTCHA_URL = "https://geo.captcha-delivery.com/interstitial/abc"


def test_captcha_url_from_interstitial_403():
    assert captcha_url_from_response(_Resp(403, {"url": CAPTCHA_URL})) == CAPTCHA_URL


def test_captcha_url_ignores_non_captcha_403():
    assert captcha_url_from_response(_Resp(403, {"url": "https://example.com/x"})) is None


def test_captcha_url_ignores_200():
    assert captcha_url_from_response(_Resp(200, {"url": CAPTCHA_URL})) is None


def test_captcha_url_ignores_non_json_body():
    assert captcha_url_from_response(_Resp(403, json_error=True)) is None


def test_captcha_url_ignores_non_dict_body():
    assert captcha_url_from_response(_Resp(403, ["not", "a", "dict"])) is None


def test_normalize_bare_value():
    assert normalize_datadome_value("abc123") == "abc123"


def test_normalize_cookie_header_fragment():
    assert normalize_datadome_value("datadome=xyz; Path=/") == "xyz"


def test_normalize_full_cookie_string():
    assert normalize_datadome_value("datadome=xyz") == "xyz"


def test_normalize_empty_returns_none():
    assert normalize_datadome_value("   ") is None


def test_normalize_datadome_without_value_returns_none():
    assert normalize_datadome_value("datadome=") is None


def test_normalize_datadome_blank_value_returns_none():
    assert normalize_datadome_value("datadome= ;") is None


def test_challenge_defaults_warned_false():
    challenge = CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc))
    assert challenge.warned is False


def test_read_inbox_missing_file(tmp_path: Path):
    assert read_inbox_datadome(tmp_path / "datadome_in") is None


def test_read_inbox_raw_value(tmp_path: Path):
    inbox = tmp_path / "datadome_in"
    inbox.write_text("freshcookie\n", encoding="utf-8")
    assert read_inbox_datadome(inbox) == "freshcookie"


def test_read_inbox_cookie_header_form(tmp_path: Path):
    inbox = tmp_path / "datadome_in"
    inbox.write_text("datadome=freshcookie; Path=/\n", encoding="utf-8")
    assert read_inbox_datadome(inbox) == "freshcookie"


def test_read_inbox_empty_file(tmp_path: Path):
    inbox = tmp_path / "datadome_in"
    inbox.write_text("   \n", encoding="utf-8")
    assert read_inbox_datadome(inbox) is None


def test_read_inbox_garbage_bytes_rejected(tmp_path: Path):
    inbox = tmp_path / "datadome_in"
    inbox.write_bytes(b"\x00\x01abc")
    assert read_inbox_datadome(inbox) is None


def test_read_inbox_strips_utf8_bom(tmp_path: Path):
    inbox = tmp_path / "datadome_in"
    inbox.write_bytes(b"\xef\xbb\xbffreshcookie")
    assert read_inbox_datadome(inbox) == "freshcookie"
