import io
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from tgtg_scanner.captcha import (
    CaptchaChallenge,
    _is_plausible_datadome,
    captcha_url_from_response,
    normalize_datadome_value,
    prompt_datadome,
    read_browser_datadome,
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


def test_read_browser_missing_library_warns_and_returns_none(mocker):
    challenge = CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc))
    # None in sys.modules makes `import browser_cookie3` raise ImportError
    mocker.patch.dict("sys.modules", {"browser_cookie3": None})
    assert read_browser_datadome("chrome", challenge=challenge) is None
    assert challenge.warned is True


def test_read_browser_unsupported_name_warns(mocker):
    fake = SimpleNamespace()  # no chrome() attr
    mocker.patch.dict("sys.modules", {"browser_cookie3": fake})
    challenge = CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc))
    assert read_browser_datadome("netscape", challenge=challenge) is None
    assert challenge.warned is True


def test_read_browser_warn_once_suppresses_repeat(mocker):
    fake = SimpleNamespace()
    mocker.patch.dict("sys.modules", {"browser_cookie3": fake})
    challenge = CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc))
    warn = mocker.patch("tgtg_scanner.captcha.log.warning")
    read_browser_datadome("netscape", challenge=challenge)
    read_browser_datadome("netscape", challenge=challenge)
    assert warn.call_count == 1


def test_read_browser_extracts_newest_toogoodtogo_cookie(mocker):
    fake = SimpleNamespace(
        chrome=lambda domain_name=None: [
            SimpleNamespace(name="datadome", value="stale", domain=".toogoodtogo.com", expires=100),
            SimpleNamespace(name="datadome", value="fresh", domain=".toogoodtogo.com", expires=200),
            SimpleNamespace(name="other", value="nope", domain=".toogoodtogo.com", expires=300),
        ]
    )
    mocker.patch.dict("sys.modules", {"browser_cookie3": fake})
    assert read_browser_datadome("chrome") == "fresh"


def test_read_browser_no_candidate_returns_none(mocker):
    fake = SimpleNamespace(chrome=lambda domain_name=None: [])
    mocker.patch.dict("sys.modules", {"browser_cookie3": fake})
    assert read_browser_datadome("chrome") is None


def test_read_browser_loader_error_warns_and_returns_none(mocker):
    def boom(domain_name=None):
        raise RuntimeError("keyring locked")

    fake = SimpleNamespace(chrome=boom)
    mocker.patch.dict("sys.modules", {"browser_cookie3": fake})
    challenge = CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc))
    assert read_browser_datadome("chrome", challenge=challenge) is None
    assert challenge.warned is True


class _FakeStdin:
    def __init__(self, line: str = "", tty: bool = True, raise_exc: BaseException | None = None):
        self._line = line
        self._tty = tty
        self._raise_exc = raise_exc

    def isatty(self) -> bool:
        return self._tty

    def readline(self) -> str:
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._line


def _prompt(monkeypatch, line: str = "", tty: bool = True, raise_exc: BaseException | None = None):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(line, tty, raise_exc))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    return prompt_datadome(CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc)))


def test_prompt_datadome_bare_token(monkeypatch):
    assert _prompt(monkeypatch, line="abc123token\n") == "abc123token"


def test_prompt_datadome_cookie_header_fragment(monkeypatch):
    assert _prompt(monkeypatch, line="datadome=wrappedtoken; Path=/; Secure\n") == "wrappedtoken"


def test_prompt_datadome_empty_line(monkeypatch):
    assert _prompt(monkeypatch, line="\n") is None


def test_prompt_datadome_eof(monkeypatch):
    assert _prompt(monkeypatch, raise_exc=EOFError()) is None


def test_prompt_datadome_keyboard_interrupt(monkeypatch):
    assert _prompt(monkeypatch, raise_exc=KeyboardInterrupt()) is None


def test_prompt_datadome_os_error(monkeypatch):
    assert _prompt(monkeypatch, raise_exc=OSError("input closed")) is None


def test_prompt_datadome_non_tty(monkeypatch):
    assert _prompt(monkeypatch, line="abc123token\n", tty=False) is None


def test_prompt_datadome_rejects_control_chars(monkeypatch):
    assert _prompt(monkeypatch, line="bad\x00cookie\n") is None


def test_prompt_datadome_prints_challenge_url(monkeypatch):
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _FakeStdin("abc\n"))
    monkeypatch.setattr(sys, "stdout", stdout)

    prompt_datadome(CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc)))
    assert CAPTCHA_URL in stdout.getvalue()


def test_prompt_datadome_without_challenge(monkeypatch):
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _FakeStdin("abc123token\n"))
    monkeypatch.setattr(sys, "stdout", stdout)

    assert prompt_datadome() == "abc123token"
    text = stdout.getvalue()
    assert "solve it in your browser" in text
    assert "http" not in text


def test_prompt_datadome_never_logs_pasted_value(monkeypatch, caplog):
    with caplog.at_level(logging.DEBUG, logger="tgtg"):
        assert _prompt(monkeypatch, line="abc123token\n") == "abc123token"
        assert "abc123token" not in caplog.text
        assert _prompt(monkeypatch, line="bad\x00cookie\n") is None
        assert "bad\x00cookie" not in caplog.text


def test_is_plausible_datadome_accepts_token():
    assert _is_plausible_datadome("abc123token") is True


def test_is_plausible_datadome_rejects_control_chars():
    assert _is_plausible_datadome("bad\x00cookie") is False
    assert _is_plausible_datadome("bad\x01cookie") is False
    assert _is_plausible_datadome("bad\x7fcookie") is False


def test_is_plausible_datadome_rejects_replacement_char():
    assert _is_plausible_datadome("bad�cookie") is False
