# Design: Interactive DataDome Cookie Paste Prompt

**Date:** 2026-10-16
**Status:** Approved (inline design review with user)
**Supersedes nothing; extends:** `2026-10-14-datadome-captcha-recovery-design.md`

## Problem

The DataDome captcha recovery flow added in October 2026 recovers challenges via
`browser_cookie3` auto-read or a `datadome_in` inbox file. The user runs the scanner on a
mobile device where `browser_cookie3` cannot read browser cookies, and the inbox file is
clunky. The pre-`293d2cb` workflow — an interactive terminal prompt to paste the `datadome`
cookie value — worked on mobile and is requested back, without losing the safety
improvements built since (no-crash-on-startup login deferral, probe-before-trust of new
cookies, token persistence).

## Goals

- When a captcha challenge is pending, an opted-in user can paste the `datadome` cookie
  value at a terminal prompt and have the scanner verify, apply, persist, and resume —
  within the same recovery cycle (no `CaptchaCooldown` wait after a successful paste).
- Opt-in only: default behavior (browser auto-read + inbox) is unchanged.
- Mobile-friendly: no `browser_cookie3` dependency for this path.

## Non-Goals

- Reverting `_post` to blocking mid-request (would resurrect the startup-crash bug by
  dropping captcha state tracking).
- Removing the browser-cookie or inbox recovery paths.
- Prompting when stdin is not a TTY (docker/systemd); behavior there stays as today.

## Configuration

- New key: `[TGTG] CaptchaPrompt` (env `TGTG_CAPTCHA_PROMPT`), boolean, default `false`.
- Recommended mobile setup: `CaptchaPrompt = true` and `CaptchaBrowser = off`.

## Behavior

1. `_recover_captcha` runs on each scan cycle while `client.captcha` is pending (unchanged).
2. Candidate selection order becomes: **prompt → browser cookie → inbox file**; first
   non-`None` candidate wins.
3. The prompt (only when `CaptchaPrompt` is enabled **and** `sys.stdin.isatty()`):
   - Re-prints the challenge URL and instructions to solve the captcha and copy the
     `datadome` cookie value (browser DevTools → Application → Cookies).
   - Reads one line from stdin.
   - Normalizes via the existing `normalize_datadome_value` (accepts a bare token, a full
     `datadome=...` Cookie-header fragment, or `datadome=...; other=...`).
   - Empty line, `EOFError`, `KeyboardInterrupt`, non-TTY, or unnormalizable input:
     returns `None` and falls through to the browser/inbox candidates. The prompt
     re-offers on each recovery cycle while the challenge is pending.
4. Everything downstream of candidate selection is unchanged: freshness comparison against
   the current cookie, `apply_datadome`, probe via `get_favorites`, rollback on probe
   failure, `config.save_tokens` on success, challenge cleared, scanning resumes.
5. The pasted value is never logged.

## Components

| File | Change |
|------|--------|
| `tgtg_scanner/models/config.py` | `TgtgConfig.captcha_prompt: bool = False`; `_read_ini` + `_read_env` readers. |
| `tgtg_scanner/captcha.py` | New `prompt_datadome(challenge: CaptchaChallenge \| None) -> str \| None`: TTY check, prompt text with challenge URL, one-line read, normalize/reject, never raises. |
| `tgtg_scanner/scanner.py` | `_recover_captcha`: try prompt first (when enabled), then existing browser/inbox candidates. |
| `config.sample.ini` | Document `CaptchaPrompt` in the `[TGTG]` captcha notes. |

## Error Handling

- Any unexpected exception inside the prompt: caught, logged at warning, treated as `None`
  (falls through). The scan loop never crashes on prompting.
- Probe failure after a pasted cookie: existing rollback path (restore previous cookie or
  clear) — unchanged.
- `CaptchaPrompt = true` under non-TTY: silent fall-through (`log.debug`), no prompt text.

## Testing

- `prompt_datadome` unit tests: bare token; `datadome=...` wrapped form; Cookie-header
  fragment with extra attributes; empty line; `EOFError`; non-TTY; control-character
  garbage rejected; never raises on stdin exceptions.
- `Scanner._recover_captcha` tests: prompt enabled and returns a value → used as candidate
  (probe success path applies it); prompt returns `None` → browser/inbox still tried;
  prompt disabled → `prompt_datadome` not called.
- Config test: `CaptchaPrompt` parsed from INI and env.

## Security

- The `datadome` cookie is a session credential: it must not appear in logs or exceptions.
  The prompt helper logs instructions only; validation failures log a generic warning.
