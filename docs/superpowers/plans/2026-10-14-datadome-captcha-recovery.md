# Non-blocking DataDome Captcha Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the blocking stdin captcha prompt with non-blocking recovery: record a pending challenge, back off to cooldown probes, and auto-read the fresh `datadome` cookie from the local browser cookie store (file inbox as fallback).

**Architecture:** New `tgtg_scanner/captcha.py` module holds interstitial detection + cookie-recovery helpers. `TgtgClient._post()` stops prompting and only tracks `self.captcha: CaptchaChallenge | None`. The scanner run loop swaps normal jobs for `_recover_captcha()` while pending, and `_next_sleep_time()` uses a cooldown. Recovery order: browser cookie-jar (`browser_cookie3`, optional dep) → `<token_path>/datadome_in` file → verify with one `get_favorites()` probe.

**Tech Stack:** Python 3.10+, pytest + pytest-mock + responses, `browser_cookie3` (optional extra), existing `tgtg` 0.19 upstream client.

**Spec:** `docs/superpowers/specs/2026-10-14-datadome-captcha-recovery-design.md`

**Conventions:** Commit messages are plain imperative sentences (no conventional-commit prefixes), matching `git log`. Lint: `make lint` (ruff E,F,B,I,UP + mypy via pre-commit). Tests: `poetry run pytest …` or `make test` for the full non-API suite. Line length 130.

---

### Task 1: Captcha module — challenge state, interstitial detection, normalization

**Files:**
- Create: `tgtg_scanner/captcha.py`
- Test: `tests/test_captcha.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_captcha.py`:

```python
import json
from datetime import datetime, timezone

from tgtg_scanner.captcha import (
    CaptchaChallenge,
    captcha_url_from_response,
    normalize_datadome_value,
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


def test_challenge_defaults_warned_false():
    challenge = CaptchaChallenge(url=CAPTCHA_URL, first_seen=datetime.now(timezone.utc))
    assert challenge.warned is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_captcha.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tgtg_scanner.captcha'`

- [ ] **Step 3: Write the implementation**

Create `tgtg_scanner/captcha.py`:

```python
"""DataDome captcha interstitial detection and datadome cookie recovery helpers."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from pathlib import Path

log = logging.getLogger("tgtg")

DATADOME_RE = re.compile(r"datadome=([^;]+)", re.IGNORECASE)
CAPTCHA_DOMAIN = "captcha-delivery.com"
COOKIE_DOMAIN = "toogoodtogo.com"


@dataclass
class CaptchaChallenge:
    """A pending DataDome interactive challenge."""

    url: str
    first_seen: datetime
    warned: bool = False


def captcha_url_from_response(response) -> str | None:
    """Return the interstitial URL when the response is a DataDome captcha challenge."""
    if response.status_code != HTTPStatus.FORBIDDEN:
        return None
    try:
        body = response.json()
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    url = body.get("url", "")
    if isinstance(url, str) and CAPTCHA_DOMAIN in url:
        return url
    return None


def normalize_datadome_value(value: str) -> str | None:
    """Map a raw value or Cookie header fragment to a bare datadome token."""
    value = value.strip()
    if not value:
        return None
    match = DATADOME_RE.search(value)
    if match:
        return match.group(1).strip()
    return value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_captcha.py -v`
Expected: all PASS (NotImplementedError function has no tests yet)

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/captcha.py tests/test_captcha.py
git commit -m "Add captcha module: interstitial detection and datadome normalization"
```

---

### Task 2: Inbox file reader

**Files:**
- Modify: `tgtg_scanner/captcha.py`
- Test: `tests/test_captcha.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_captcha.py`:

```python
from pathlib import Path

from tgtg_scanner.captcha import read_inbox_datadome


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


def test_read_inbox_garbage_bytes_still_returns_string(tmp_path: Path):
    inbox = tmp_path / "datadome_in"
    inbox.write_bytes(b"\x00\x01abc")
    assert read_inbox_datadome(inbox) == "\x00\x01abc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_captcha.py -v -k inbox`
Expected: FAIL — `ImportError: cannot import name 'read_inbox_datadome' from 'tgtg_scanner.captcha'`

- [ ] **Step 3: Write the implementation**

Append to `tgtg_scanner/captcha.py` (after `normalize_datadome_value`):

```python
def read_inbox_datadome(path: Path) -> str | None:
    """Read a datadome value dropped into the inbox file by the user."""
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning("Could not read captcha inbox %s: %s", path, exc)
        return None
    return normalize_datadome_value(raw)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_captcha.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/captcha.py tests/test_captcha.py
git commit -m "Read datadome inbox file with normalization and soft failures"
```

---

### Task 3: Browser cookie-jar reader (optional browser_cookie3)

**Files:**
- Modify: `tgtg_scanner/captcha.py`
- Modify: `pyproject.toml`, `poetry.lock` (via poetry)
- Test: `tests/test_captcha.py`

- [ ] **Step 1: Add the optional dependency**

Run: `poetry add --optional "browser_cookie3>=0.20.0,<1.0.0"`

Expected: `pyproject.toml` gains `[project.optional-dependencies] captcha = [...]`, lock updated.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_captcha.py`:

```python
from types import SimpleNamespace

from tgtg_scanner.captcha import read_browser_datadome


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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `poetry run pytest tests/test_captcha.py -v -k browser`
Expected: FAIL — `ImportError: cannot import name 'read_browser_datadome'`

- [ ] **Step 4: Write the implementation**

Append to `tgtg_scanner/captcha.py` (after `read_inbox_datadome`):

```python
def read_browser_datadome(browser: str, *, challenge: CaptchaChallenge | None = None) -> str | None:
    """Read the datadome cookie for toogoodtogo.com from the local browser cookie store."""
    try:
        import browser_cookie3
    except ImportError:
        _warn_once(
            challenge,
            "browser_cookie3 not installed - browser cookie auto-read disabled "
            "(pip install 'tgtg-scanner[captcha]'). Falling back to file inbox.",
        )
        return None
    loader = getattr(browser_cookie3, browser.lower(), None)
    if not callable(loader):
        _warn_once(challenge, f"Unsupported CaptchaBrowser '{browser}' - falling back to file inbox.")
        return None
    try:
        jar = loader(domain_name=COOKIE_DOMAIN)
    except Exception as exc:
        _warn_once(challenge, f"Could not read {browser} cookies ({exc}) - falling back to file inbox.")
        return None
    candidates = [c for c in jar if c.name == "datadome" and c.value and COOKIE_DOMAIN in (c.domain or "")]
    if not candidates:
        return None
    candidates.sort(key=lambda c: c.expires or 0)
    return str(candidates[-1].value)


def _warn_once(challenge: CaptchaChallenge | None, message: str) -> None:
    if challenge is not None:
        if challenge.warned:
            return
        challenge.warned = True
    log.warning(message)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_captcha.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add tgtg_scanner/captcha.py pyproject.toml poetry.lock tests/test_captcha.py
git commit -m "Auto-read datadome from browser cookie store with warn-once fallback"
```

---

### Task 4: Client — track challenge instead of blocking, add apply_datadome

**Files:**
- Modify: `tgtg_scanner/tgtg_client.py` (class `TgtgClient`: `__init__`, `_post`, new `apply_datadome`; module imports)
- Test: `tests/test_tgtg.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tgtg.py` (ensure imports: `from datetime import datetime, timezone`, `from tgtg_scanner.captcha import CaptchaChallenge`; existing imports already include `TgtgClient`, `normalize_cookie`, `responses`, `pytest`, `urljoin`, `tgtg`, `BASE_URL`, `TgtgAPIError` — add whatever is missing):

```python
@responses.activate
def test_post_interstitial_sets_captcha_state_once():
    _datadome_sdk()
    url = urljoin(BASE_URL, tgtg.API_BUCKET_ENDPOINT)
    for _ in range(2):
        responses.add(
            responses.POST,
            url,
            status=403,
            body='{"url":"https://geo.captcha-delivery.com/interstitial/abc"}',
        )
    client = TgtgClient(
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]

    with pytest.raises(TgtgAPIError):
        client.get_favorites()
    assert client.captcha is not None
    assert "captcha-delivery.com" in client.captcha.url
    first_seen = client.captcha.first_seen

    with pytest.raises(TgtgAPIError):
        client.get_favorites()
    assert client.captcha.first_seen == first_seen  # same episode - not reset


@responses.activate
def test_post_success_clears_captcha_state():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, tgtg.API_BUCKET_ENDPOINT),
        json={"mobile_bucket": {"items": []}},
        status=200,
    )
    client = TgtgClient(
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    client.captcha = CaptchaChallenge(url="https://geo.captcha-delivery.com/x", first_seen=datetime.now(timezone.utc))

    assert client.get_favorites() == []
    assert client.captcha is None


def test_apply_datadome_sets_session_cookie():
    client = TgtgClient(
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.apply_datadome("datadome=newtok; Path=/")
    assert client.cookie == "datadome=newtok"
    assert any(c.name == "datadome" and c.value == "newtok" for c in client.session.cookies)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_tgtg.py -v -k "captcha_state or apply_datadome"`
Expected: FAIL — `AttributeError: 'TgtgClient' object has no attribute 'captcha'` / `'apply_datadome'`

- [ ] **Step 3: Rewrite imports and `_post` in `tgtg_scanner/tgtg_client.py`**

Module-level changes:

1. Remove imports: `json`, `sys`, `webbrowser`, `re` (all used only by the old `_post`
   prompt/detection or the deleted `_DATADOME_RE`).
2. Add import: `from datetime import datetime, timezone`.
3. Delete `_DATADOME_RE = re.compile(...)` (line 28) and add:

```python
from tgtg_scanner.captcha import DATADOME_RE, CaptchaChallenge, captcha_url_from_response, normalize_datadome_value
```

(import placed with the other `tgtg_scanner` import, `from tgtg_scanner.pin_prompt import prompt_via_browser`).

4. In `extract_datadome`, replace `_DATADOME_RE.search(...)` with `DATADOME_RE.search(...)`.

5. In `__init__`, after `self.pin_port = pin_port` add:

```python
        self.captcha: CaptchaChallenge | None = None
```

6. Replace the entire `_post` method body with:

```python
    def _post(self, url, **kwargs):
        """POST via upstream (SDK cookie + one retry), tracking DataDome captcha challenges.

        On an interstitial this records ``self.captcha`` (once per episode) and returns
        the 403 unchanged - recovery is the scanner's job and the scan loop never blocks.
        """
        response = super()._post(url, **kwargs)
        captcha_url = captcha_url_from_response(response)
        if captcha_url is None:
            self.captcha = None
            return response
        if self.captcha is None:
            self.captcha = CaptchaChallenge(url=captcha_url, first_seen=datetime.now(timezone.utc))
            log.warning(
                "DataDome captcha challenge - solve it in your browser; the scanner will pick up the fresh cookie automatically:\n  %s",
                captcha_url,
            )
        return response
```

7. Add `apply_datadome` right after `_post`:

```python
    def apply_datadome(self, value: str) -> None:
        """Set a fresh datadome cookie on the session (bare token or Cookie-header form)."""
        bare = normalize_datadome_value(value)
        if not bare:
            return
        domain = tgtg.urlsplit(self.base_url).hostname
        self.session.cookies.set(
            "datadome",
            bare,
            domain=f".{domain}",
            path="/",
            secure=True,
        )
        self.cookie = f"datadome={bare}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_tgtg.py -v`
Expected: all PASS (including the pre-existing `test_login_raises_when_refresh_fails_without_email`)

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/tgtg_client.py tests/test_tgtg.py
git commit -m "Track DataDome captcha challenges in client instead of blocking on stdin"
```

---

### Task 5: Config — CaptchaBrowser / CaptchaCooldown + docs rows

**Files:**
- Modify: `tgtg_scanner/models/config.py` (`TgtgConfig`)
- Modify: `config.sample.ini`
- Modify: `wiki/Configuration.md`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py` (existing imports include `Config`; add `import os` if missing):

```python
def test_captcha_config_defaults():
    config = Config()
    assert config.tgtg.captcha_browser == "chrome"
    assert config.tgtg.captcha_cooldown == 600


def test_captcha_config_env_overrides(mocker):
    mocker.patch.dict(os.environ, {"TGTG_CAPTCHA_BROWSER": "firefox", "TGTG_CAPTCHA_COOLDOWN": "120"})
    config = Config()
    assert config.tgtg.captcha_browser == "firefox"
    assert config.tgtg.captcha_cooldown == 120
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_config.py -v -k captcha`
Expected: FAIL with `AttributeError` (no `captcha_browser`)

- [ ] **Step 3: Implement config fields**

In `tgtg_scanner/models/config.py`, class `TgtgConfig`:

After `user_agent: str | None = None` / before `base_url`, add:

```python
    captcha_browser: str = "chrome"
    captcha_cooldown: int = 600
```

In `_read_ini`, after the `UserAgent` line:

```python
        self._ini_get(parser, "TGTG", "CaptchaBrowser", "captcha_browser")
        self._ini_get_int(parser, "TGTG", "CaptchaCooldown", "captcha_cooldown")
```

In `_read_env`, after the `TGTG_USER_AGENT` line:

```python
        self._env_get("TGTG_CAPTCHA_BROWSER", "captcha_browser")
        self._env_get_int("TGTG_CAPTCHA_COOLDOWN", "captcha_cooldown")
```

- [ ] **Step 4: Update sample config and wiki**

In `config.sample.ini`, inside `[TGTG]` after the `; Datadome =` line, add:

```ini
## Browser whose cookie store is read automatically after a DataDome captcha
## challenge (chrome, firefox, edge, ... - or 'off' to disable).
## Requires the optional extra: pip install 'tgtg-scanner[captcha]'
CaptchaBrowser = chrome
## Seconds between captcha recovery probes while a challenge is pending
CaptchaCooldown = 600
```

In `wiki/Configuration.md`, in the TGTG table directly after the `Datadome` row, add two rows (keep column alignment):

```
| CaptchaBrowser      | TGTG_CAPTCHA_BROWSER       | Browser cookie auto-read on captcha      | chrome  |          |
| CaptchaCooldown     | TGTG_CAPTCHA_COOLDOWN      | Seconds between captcha recovery probes  | 600     |          |
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `poetry run pytest tests/test_config.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add tgtg_scanner/models/config.py tests/test_config.py config.sample.ini wiki/Configuration.md
git commit -m "Add CaptchaBrowser and CaptchaCooldown config with docs"
```

---

### Task 6: Scanner — route pending challenges to recovery, cooldown sleep

**Files:**
- Modify: `tgtg_scanner/scanner.py` (`_next_sleep_time`, new `_run_job`, `run` loop call site)
- Test: `tests/test_scanner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scanner.py` (add imports: `from datetime import datetime, timezone`, `from tgtg_scanner.captcha import CaptchaChallenge`):

```python
def _pending_captcha() -> CaptchaChallenge:
    return CaptchaChallenge(url="https://geo.captcha-delivery.com/interstitial/x", first_seen=datetime.now(timezone.utc))


def test_next_sleep_uses_captcha_cooldown(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = _pending_captcha()
    scanner.config.tgtg.captcha_cooldown = 600
    mocker.patch("tgtg_scanner.scanner.random", return_value=0.5)
    assert scanner._next_sleep_time() == 600.0


def test_run_job_routes_to_recovery_when_captcha_pending(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_recover = mocker.patch.object(scanner, "_recover_captcha")
    mock_job = mocker.patch.object(scanner, "_job")

    scanner._run_job()

    mock_recover.assert_called_once()
    mock_job.assert_not_called()


def test_run_job_runs_normal_job_when_no_captcha(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_recover_captcha")
    mock_job = mocker.patch.object(scanner, "_job")

    scanner._run_job()

    mock_job.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_scanner.py -v -k "cooldown or routes_to_recovery or normal_job"`
Expected: FAIL — `_run_job` missing; cooldown test fails on normal sleep (60s default ≠ 600)

- [ ] **Step 3: Implement**

In `tgtg_scanner/scanner.py`:

1. Add import next to the existing captcha-adjacent import (line 33 area):

```python
from tgtg_scanner.captcha import read_browser_datadome, read_inbox_datadome
```

2. Replace `_next_sleep_time` with:

```python
    def _next_sleep_time(self) -> float:
        if self.tgtg_client.captcha is not None:
            return self.config.tgtg.captcha_cooldown * (0.9 + 0.2 * random())
        if self.config.reservation.fast_poll and self._fast_mode_active():
            return 8.0 + 4.0 * random()
        return self.config.sleep_time * (0.9 + 0.2 * random())
```

3. Add `_run_job` directly after `_next_sleep_time`:

```python
    def _run_job(self) -> None:
        """Run one cycle: captcha recovery while blocked, otherwise fast or normal job."""
        if self.tgtg_client.captcha is not None:
            self._recover_captcha()
            return
        try:
            fast = self.config.reservation.fast_poll and self._fast_mode_active()
        except Exception:
            log.warning("Failed to evaluate fast mode - using normal cycle")
            fast = False
        if fast:
            self._fast_job()
        else:
            self._job()
```

(`_recover_captcha` arrives in Task 7; for this task add a temporary stub so the suite is green:)

```python
    def _recover_captcha(self) -> None:
        """Placeholder - implemented in Task 7."""
        raise NotImplementedError
```

4. In `run()`, replace the inner job-selection block:

```python
                try:
                    self._run_job()
                except Exception:
                    log.error("Job Error! - %s", sys.exc_info())
```

(was: the nested `try: fast = ...` / `if fast: _fast_job else _job` block — everything inside the outer `try:` stays otherwise unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_scanner.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/scanner.py tests/test_scanner.py
git commit -m "Route scan cycles to captcha recovery with cooldown backoff"
```

---

### Task 7: Scanner — `_recover_captcha` implementation

**Files:**
- Modify: `tgtg_scanner/scanner.py` (replace `_recover_captcha` stub; add `import contextlib`)
- Test: `tests/test_scanner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_scanner.py` (add `from pathlib import Path` to imports):

```python
def test_recover_captcha_applies_inbox_cookie(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    (tmp_path / "datadome_in").write_text("datadome=freshcookie; Path=/\n", encoding="utf-8")
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value=None)
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    assert scanner.tgtg_client.captcha is None
    assert scanner.tgtg_client.cookie == "datadome=freshcookie"
    mock_favorites.assert_called_once()
    mock_save.assert_called_once()
    assert not (tmp_path / "datadome_in").exists()


def test_recover_captcha_prefers_browser_cookie(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    (tmp_path / "datadome_in").write_text("inboxcookie", encoding="utf-8")
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="browsercookie")
    mock_inbox = mocker.patch("tgtg_scanner.scanner.read_inbox_datadome")
    mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    assert scanner.tgtg_client.cookie == "datadome=browsercookie"
    mock_inbox.assert_not_called()


def test_recover_captcha_rejected_candidate_stays_pending(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    (tmp_path / "datadome_in").write_text("badcookie", encoding="utf-8")
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value=None)
    mocker.patch.object(scanner.tgtg_client, "get_favorites", side_effect=TgtgAPIError(403, b"captcha challenge"))
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    assert scanner.tgtg_client.captcha is not None
    mock_save.assert_not_called()
    assert (tmp_path / "datadome_in").exists()


def test_recover_captcha_no_candidate_stays_pending(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value=None)
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites")

    scanner._recover_captcha()

    assert scanner.tgtg_client.captcha is not None
    mock_favorites.assert_not_called()


def test_recover_captcha_swallows_unexpected_exceptions(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", side_effect=RuntimeError("boom"))

    scanner._recover_captcha()  # must not raise

    assert scanner.tgtg_client.captcha is not None


def test_recover_captcha_noop_without_challenge(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = None
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites")

    scanner._recover_captcha()

    mock_favorites.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `poetry run pytest tests/test_scanner.py -v -k recover`
Expected: FAIL — stub raises `NotImplementedError`

- [ ] **Step 3: Implement**

In `tgtg_scanner/scanner.py`:

1. Add `import contextlib` to the stdlib imports at the top.
2. Replace the `_recover_captcha` stub with:

```python
    def _recover_captcha(self) -> None:
        """Attempt datadome recovery for a pending captcha challenge (never raises)."""
        challenge = self.tgtg_client.captcha
        if challenge is None:
            return
        try:
            candidate: str | None = None
            from_inbox = False
            browser = self.config.tgtg.captcha_browser
            if browser and browser.lower() != "off":
                candidate = read_browser_datadome(browser, challenge=challenge)
            inbox_path: Path | None = None
            if self.config.token_path:
                inbox_path = Path(self.config.token_path, "datadome_in")
            if candidate is None and inbox_path is not None:
                candidate = read_inbox_datadome(inbox_path)
                from_inbox = candidate is not None
            current = extract_datadome(self.tgtg_client)
            if not candidate or candidate == current:
                log.debug("Captcha pending - no fresh datadome cookie yet (solve: %s)", challenge.url)
                return
            self.tgtg_client.apply_datadome(candidate)
            try:
                self.tgtg_client.get_favorites()
            except TgtgAPIError as err:
                log.warning("Candidate datadome cookie rejected: %s", err)
                return
            self.tgtg_client.captcha = None
            self._save_tokens()
            if from_inbox and inbox_path is not None:
                with contextlib.suppress(OSError):
                    inbox_path.unlink()
            log.info("Datadome captcha recovered - resuming normal scanning")
        except Exception as exc:
            log.warning("Captcha recovery attempt failed: %s", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `poetry run pytest tests/test_scanner.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tgtg_scanner/scanner.py tests/test_scanner.py
git commit -m "Recover datadome via browser cookie store or inbox file with verify probe"
```

---

### Task 8: Full verification and cleanup

**Files:**
- Verify: whole repo (no new files)

- [ ] **Step 1: Run the full lint suite**

Run: `make lint`
Expected: pre-commit hooks all pass (ruff, mypy). Fix any unused-import leftovers in `tgtg_client.py` (e.g. `re` if now unused) and any mypy complaints, then re-run.

- [ ] **Step 2: Run the full test suite**

Run: `make test`
Expected: all tests pass (API-marked tests excluded).

- [ ] **Step 3: Sanity-check the CLI against the local proxy (manual smoke, optional)**

Run: `make server` in one terminal; in another: `poetry run scanner -d --base_url http://localhost:8080`
Expected: scanner starts, logs in via the dev proxy, no captcha path triggered (dev proxy sets its own datadome cookie). Ctrl-C to stop.

- [ ] **Step 4: Commit any lint fixes (if Step 1 required changes)**

```bash
git add -u
git commit -m "Fix lint issues after captcha recovery changes"
```

(No-op commit step if lint was already clean — skip the commit, do not create empty commits.)

---

## Self-review notes (plan author)

- **Spec coverage:** interstitial detection + once-per-episode warning (Task 4); cooldown backoff (Task 6); browser primary + inbox fallback + verify probe + consume inbox (Task 7); config + docs (Task 5); optional extra + degradation (Tasks 3, 7); never-raise recovery (Task 7). Stdin prompt removal is inherent to the Task 4 `_post` rewrite. Out-of-scope items deliberately absent.
- **Type consistency:** `CaptchaChallenge(url, first_seen, warned)` used identically in Tasks 1/4/6/7; `read_browser_datadome(browser, *, challenge=...)` matches Tasks 3/7; `read_inbox_datadome(path)` matches Tasks 2/7; `apply_datadome(value)` matches Tasks 4/7; `DATADOME_RE` renamed from private `_DATADOME_RE` consistently in Task 4.
- **Known edges:** `get_favorites()` on the scanner client is the paged override (hardcoded page size 100) — one HTTP call for typical lists; a rejected candidate cookie stays in the session until the next probe or upstream 403 refetch replaces it (harmless; Task 7 test pins the pending-state behavior).
