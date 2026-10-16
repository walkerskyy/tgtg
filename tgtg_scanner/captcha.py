"""DataDome captcha interstitial detection and datadome cookie recovery helpers."""

from __future__ import annotations

import json
import logging
import re
import sys
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
        return match.group(1).strip() or None
    if value.lower().startswith("datadome="):
        return None
    return value


def prompt_datadome(challenge: CaptchaChallenge | None = None) -> str | None:
    """Interactively ask the user to paste a fresh datadome cookie value.

    Returns the normalized token, or None when prompting is not possible
    (non-TTY stdin, empty/invalid input, stdin errors). Never raises.
    """
    try:
        if not sys.stdin.isatty():
            if challenge is not None:
                log.debug("Captcha prompt skipped - stdin is not a TTY (solve: %s)", challenge.url)
            return None
        url = f":\n  {challenge.url}" if challenge is not None else ""
        sys.stdout.write(
            f"DataDome captcha challenge - solve it in your browser{url}\n"
            "Then copy the 'datadome' cookie value (DevTools -> Application -> Cookies) and paste it here:\n> "
        )
        sys.stdout.flush()
        try:
            raw = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt, OSError):
            sys.stdout.write("\n")
            return None
        value = normalize_datadome_value(raw)
        if value is None:
            if raw.strip():
                log.warning("Pasted value is not a plausible datadome cookie - ignoring")
            return None
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "�" for ch in value):
            log.warning("Pasted value does not contain a plausible datadome cookie - ignoring")
            return None
        return value
    except Exception as exc:
        log.warning("Captcha prompt failed: %s", exc)
        return None


def read_inbox_datadome(path: Path) -> str | None:
    """Read a datadome value dropped into the inbox file by the user."""
    try:
        if not path.is_file():
            return None
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as exc:
        log.warning("Could not read captcha inbox %s: %s", path, exc)
        return None
    value = normalize_datadome_value(raw)
    if value is not None and any(ord(ch) < 0x20 or ord(ch) == 0x7F or ch == "\ufffd" for ch in value):
        log.warning("Captcha inbox %s does not contain a plausible datadome cookie - ignoring", path)
        return None
    return value


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
