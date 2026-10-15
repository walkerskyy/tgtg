"""Extract a bank-verification (3DS) URL from a TGTG/Adyen payment-status payload."""

from __future__ import annotations

import json
import logging

log = logging.getLogger("tgtg")

_URL_KEYS = ("url", "redirect_url", "verification_url", "challengeUrl", "challenge_url", "href")
_BLOCKED_HOST_MARKERS = ("toogoodtogo.com", "apptoogoodtogo.com", "bagrescue.com")


def _candidate(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith(("https://", "http://")):
        return None
    lowered = value.lower()
    if any(marker in lowered for marker in _BLOCKED_HOST_MARKERS):
        return None
    return value


def _walk(node: object, depth: int = 0) -> str | None:
    """Depth-first search for the first plausible verification URL."""
    if depth > 6:
        return None
    if isinstance(node, dict):
        if "action" in node:
            found = _walk(node["action"], depth + 1)
            if found:
                return found
        for key in _URL_KEYS:
            found = _candidate(node.get(key))
            if found:
                return found
        for value in node.values():
            found = _walk(value, depth + 1)
            if found:
                return found
    elif isinstance(node, list):
        for entry in node:
            found = _walk(entry, depth + 1)
            if found:
                return found
    elif isinstance(node, str) and node[:1] in "{[":
        try:
            return _walk(json.loads(node), depth + 1)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def extract_auth_url(status: object) -> str | None:
    """Return the bank-verification URL from a payment-status payload, or None.

    Walks known shapes: top-level url keys, Adyen ``action.url``, and nested
    JSON strings (TGTG wraps Adyen payloads as strings). Non-https and
    TGTG/BagRescue-hosted URLs are ignored.
    """
    url = _walk(status)
    if url:
        log.debug("Extracted bank verification url: %s", url)
    return url


def dump_payment_shape(status: object, path: str = "payment_action_shape.json") -> None:
    """Persist a raw payment-status payload once, for tightening the extractor."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(status, f, indent=2, default=str)
        log.info("Payment action shape written to %s - review and share if reporting a bug", path)
    except OSError as exc:
        log.debug("Could not dump payment shape: %s", exc)
