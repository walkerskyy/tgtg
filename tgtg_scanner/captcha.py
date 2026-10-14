"""DataDome captcha interstitial detection and datadome cookie recovery helpers."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from pathlib import Path  # noqa: F401

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
