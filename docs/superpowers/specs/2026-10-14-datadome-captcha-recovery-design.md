# Non-blocking DataDome Captcha Recovery

**Date**: 2026-10-14
**Status**: Approved design

## Background

DataDome protects the TGTG API. The scanner has three conceptual layers of defense:

1. **SDK cookie (automatic, upstream `tgtg` 0.19.0)** — `_fetch_datadome_cookie()` POSTs to
   `api-sdk.datadome.co/sdk/` mimicking the Android app and parses the `datadome` cookie
   out of the JSON response payload. `_post()` ensures this cookie exists, and on every 403
   clears the jar, refetches an SDK cookie, and retries once. This works most of the time.
2. **Interactive challenge** — when DataDome's risk engine is not satisfied by an
   SDK-issued cookie, TGTG returns 403 with a JSON body containing a
   `geo.captcha-delivery.com` interstitial URL. No programmatic cookie satisfies this; a
   real browser must solve the challenge, after which the browser holds a fresh `datadome`
   cookie for `.toogoodtogo.com`.
3. **Manual fallback (current, problematic)** — `TgtgClient._post()` in this repo
   (`tgtg_scanner/tgtg_client.py:135-206`) opens the interstitial URL via `webbrowser.open`
   and, when stdin is a TTY, **blocks the scan loop** asking the user to paste the cookie
   from DevTools. Non-interactive sessions just log a warning and fail.

### Problems with the current fallback

- **Blocking prompt mid-scan** — the stdin prompt runs inside `_post()`, which runs inside
  the scan job; the loop stalls while the spinner is active and reservations are missed.
- **No backoff** — if the IP is effectively banned, every cycle re-opens a browser tab and
  re-prompts; repeated 403s also make upstream refetch SDK cookies each time (log spam,
  continued pressure on DataDome).
- **One-shot retry** — a wrong or stale pasted cookie fails silently.
- **Docker/headless gets nothing** — the interactive path is unavailable.

Community context: Der-Henning/tgtg #677 shows SDK fetch succeeding (200) while every
TGTG call still 403s — the interactive-challenge case is real and recurring.

## Summary

Replace the blocking stdin prompt with a **non-blocking, layered recovery**:

1. On interstitial detection, the client records a pending `CaptchaChallenge` (URL +
   timestamp), logs one warning with the URL, and returns the 403 unchanged. The scan loop
   never blocks.
2. While a challenge is pending, the scanner **skips normal scan jobs** and runs a single
   recovery probe per cooldown interval (default 600 s) instead — this is the anti-spam
   backoff.
3. Each recovery probe tries, in order:
   - **Browser cookie-jar auto-read** — read the `datadome` cookie for `toogoodtogo.com`
     straight out of the local browser's cookie store (`browser_cookie3`, optional
     dependency). The user solves the interstitial in their normal browser at leisure; the
     scanner picks the fresh cookie up automatically. No DevTools, no copy-paste.
   - **File inbox** — `<token_path>/datadome_in`, written manually by the user
     (`printf '%s' '<cookie>' > tokens/datadome_in`). Fallback when no browser read is
     possible (headless box with an SSH'd browser elsewhere, locked keyring, etc.).
4. A candidate cookie is applied to the session and verified with one cheap API call
   (`get_favorites()` — the client's paged override; one HTTP call for a typical
   favorites list). Success → pending state cleared, tokens persisted, inbox
   file consumed, scanning resumes at normal cadence. Failure → stay pending until next
   cooldown.

The SDK auto-fetch (layer 1) is unchanged and still runs first via upstream `_post()`.

## Requirements

- The scan loop must never block on captcha input.
- While a captcha challenge is pending, the scanner must not issue full scan jobs (no
  favorites/items hammering); at most one API probe per cooldown interval.
- Exactly one prominent warning (with the interstitial URL) per challenge; recovery
  progress logged at info/debug without repeating the URL spam.
- Recovery must verify a candidate cookie with a real API call before declaring success.
- Browser cookie reading is best-effort: any failure (library missing, browser absent,
  keyring locked) degrades to the next layer with at most one warning, never an exception
  that escapes the recovery path.
- A consumed inbox file must not resurrect; the scanner's own `datadome` token persistence
  must not clobber or race with the inbox file (separate filenames).
- Default behavior with no configuration changes must remain safe for Docker/headless
  users (recovery layers simply find nothing; cooldown keeps pressure off DataDome).

## Design

### New module: `tgtg_scanner/captcha.py`

- `CaptchaChallenge` — dataclass: `url: str`, `first_seen: datetime`.
- `captcha_url_from_response(response) -> str | None` — given a `requests.Response`,
  return the interstitial URL if `status_code == 403` and the JSON body contains a `url`
  with `captcha-delivery.com`; else `None`. (Logic lifted from the current `_post()`
  override.)
- `read_browser_datadome(browser: str) -> str | None` — import `browser_cookie3`
  lazily; load the named browser's cookie jar; return the `datadome` cookie value whose
  domain matches `toogoodtogo.com` (most recent by expiry if several), else `None`.
  Import failure or browser/keyring errors: return `None` after warning **at most once per
  challenge episode** with a remedy hint (install extra, unlock keyring, set
  `CaptchaBrowser = off`) — the caller passes the `CaptchaChallenge` so the module can
  suppress repeats (e.g. a `warned: bool` flag on the challenge).
- `read_inbox_datadome(path: Path) -> str | None` — if `path` exists, read it, strip
  whitespace, normalize with the same rules as today's paste handling (accept raw value,
  `datadome=xxx`, or `datadome=xxx; ...` via the shared `_DATADOME_RE`); return the bare
  value or `None`.

### Client changes: `tgtg_client.py`

- **Remove** the `webbrowser`/stdin prompt block in `_post()` (current lines ~157-206).
- After `super()._post(...)`, run `captcha_url_from_response()`:
  - If an interstitial URL is found: if `self.captcha` is already set, keep it unchanged
    (same challenge episode — no new log); otherwise set
    `self.captcha = CaptchaChallenge(url, now)` and log exactly one `WARNING` with the URL
    and the recovery instructions ("solve it in your browser; the scanner will pick up the
    cookie automatically"). Return the 403 response either way.
  - Otherwise, clear `self.captcha = None` only on a successful (2xx) non-interstitial
    response, and return the response. A failed (4xx/5xx) non-interstitial response (e.g.
    429/500/plain-403) keeps the pending challenge so the episode is not reset and the
    warning is not re-fired.
- `self.captcha: CaptchaChallenge | None = None` initialized in `__init__`.
- New method `apply_datadome(value: str) -> None` — factored from the current lines
  189-197: normalize, set the session cookie for `.{base_url host}` with
  `secure=True, path=/`, and set `self.cookie = f"datadome={value}"`.

### Scanner changes: `scanner.py`

- `Scanner._captcha_pending` helper: `self.tgtg_client.captcha is not None`.
- In the run loop's job selection (`_job`/`_fast_job` call sites): if a challenge is
  pending, call `self._recover_captcha()` instead of the normal job. Normal jobs (and
  their many API calls) are skipped entirely while pending.
- `_recover_captcha()`:
  1. Determine candidate: `read_browser_datadome(config.tgtg.captcha_browser)` when the
     browser is not `off`; else (or on `None`) `read_inbox_datadome(token_path /
     "datadome_in")` when `token_path` is configured.
  2. If no candidate, or candidate equals the cookie already in the session → `log.debug`
     "still waiting for captcha solve" and return.
  3. `apply_datadome(candidate)`; probe with `self.tgtg_client.get_favorites()`.
     - Probe succeeds (no `TgtgAPIError`): `self.tgtg_client.captcha = None`,
       `self._save_tokens()`, delete the inbox file if it was the source, `log.info`
       "Datadome captcha recovered — resuming normal scanning".
     - Probe raises `TgtgAPIError`: log warning "candidate datadome cookie rejected";
       leave pending (the failed probe's 403 may refresh `self.captcha` via `_post`).
  4. Any unexpected exception inside recovery: `log.warning`, stay pending. Recovery must
     not take down the run loop.
- `_next_sleep_time()`: when a challenge is pending, return
  `config.tgtg.captcha_cooldown * (0.9 + 0.2 * random())` (same jitter style as normal
  sleep). This is the single lever that throttles recovery probes.

### Config: `models/config.py`

Two new fields on the `TgtgConfig` dataclass (following existing ini/env patterns):

| INI | Env | Type | Default | Meaning |
|---|---|---|---|---|
| `[TGTG] CaptchaBrowser` | `TGTG_CAPTCHA_BROWSER` | str | `chrome` | Browser whose cookie store to read on captcha (`chrome`, `firefox`, `edge`, `brave`, … as supported by browser_cookie3; `off` disables the layer) |
| `[TGTG] CaptchaCooldown` | `TGTG_CAPTCHA_COOLDOWN` | int seconds | `600` | Delay between recovery probes while a challenge is pending |

`config.sample.ini` and `wiki/Configuration.md` gain the two rows.

### Dependency

- `browser_cookie3` added as an **optional extra**: `pip install tgtg-scanner[captcha]`
  (`[project.optional-dependencies] captcha = ["browser_cookie3>=0.20.0,<1.0.0"]`).
- The import is lazy and guarded; without the extra the scanner logs one warning per
  challenge and uses the file inbox only. Docker images and PyInstaller binaries are
  unaffected (the layer simply never activates there).

### Data flow

```
POST … 403 {"url":"https://geo.captcha-delivery.com/interstitial/…"}
        │
        ▼
TgtgClient._post: super() already did SDK refetch+retry
        │ captcha_url_from_response → URL
        ▼
self.captcha = CaptchaChallenge(url)   ← log WARNING once (URL + instructions)
        │ return 403 (no blocking)
        ▼
Scanner run loop: challenge pending?
        ├─ no  → normal _job / _fast_job
        └─ yes → sleep(cooldown) … _recover_captcha():
                  1. browser_cookie3 jar  → datadome value?
                  2. else token_path/datadome_in
                  3. apply_datadome + get_favorites() probe
                     ├─ ok  → clear pending, save_tokens, consume inbox, resume
                     └─ fail → stay pending, next cooldown probe
```

## Error handling

| Failure | Behavior |
|---|---|
| `browser_cookie3` not installed | one warning with install hint; fall to inbox layer |
| Browser not installed / profile missing | one warning; fall to inbox layer |
| Keyring locked / no Secret Service (e.g. cron/systemd session) | one warning with remedy; fall to inbox layer |
| Inbox file absent or empty | debug log; stay pending |
| Candidate cookie rejected by probe | warning; stay pending; the 403 may refresh the challenge |
| Inbox malformed (binary garbage) | warning; treat as no candidate; stay pending |
| Any exception in `_recover_captcha` | caught, warned, stay pending — run loop survives |
| Captcha during `_login_or_reauth` at startup | same client state mechanism; recovery replaces jobs once the loop is running |

## Testing

- `tests/test_captcha.py` (new): interstitial parsing (403 JSON with/without captcha URL,
  non-JSON body, non-403); inbox normalization (raw value, `datadome=x`, `datadome=x;
  Path=/`, empty/missing file); browser read via a faked `browser_cookie3` module
  (monkeypatched at the `captcha.py` boundary — no real browser in CI).
- `tests/test_tgtg.py`: update/extend — 403 interstitial sets `client.captcha`, does not
  touch stdin; subsequent 200 response clears it; `apply_datadome` sets session cookie and
  `client.cookie`.
- `tests/test_scanner.py`: pending challenge ⇒ `_next_sleep_time` uses cooldown and the run
  loop calls `_recover_captcha` instead of `_job`; successful inbox recovery (mocker on
  `get_favorites`) clears pending, persists tokens, deletes inbox; rejected candidate stays
  pending; recovery exception is swallowed.
- Existing `test_login_raises_when_refresh_fails_without_email` remains valid (the
  non-interactive path now simply returns the 403 as before).

## Out of scope

- Sending captcha alerts through configured notifiers (Reservation-shaped status or
  otherwise) — log-only for now; can be layered later.
- Playwright-managed browser window and CDP capture.
- Paid solver APIs (CapSolver/Anti-Captcha DataDome tasks) — require proxy-bound solves;
  poor fit for a home CLI.
- Headless/Docker solve UX (HTTP form on `PORT`) — the file inbox covers the manual path.
- Changing upstream SDK-cookie behavior.

## Alternatives considered

- **Keep stdin prompt + add cooldown** — minimal change but keeps the blocking UX and
  DevTools copy-paste friction; rejected per requirements.
- **Localhost solve form (pin_prompt pattern)** — nicer submission UX but still requires
  manual DevTools copy; adds server lifecycle during the scan loop; rejected as primary,
  compatible later.
- **File inbox only (no browser read)** — no new dependency, but the whole point of the
  chosen design is zero-touch recovery after the human solves the puzzle.
- **Playwright-guided solve** — guided window with automatic CDP extraction; heavy
  dependency (~50 MB browser), little gain over cookie-jar reads for a local CLI.
