# Design: Telegram DataDome Cookie Exchange

**Date:** 2026-10-24
**Status:** Approved (inline design review with user)
**Extends:** `2026-10-14-datadome-captcha-recovery-design.md`, `2026-10-16-captcha-paste-prompt-design.md`

## Problem

The user runs the scanner on Termux (mobile). `browser_cookie3` cannot read browser
cookies there, the TTY paste prompt (`CaptchaPrompt`) does not fit their setup, and the
`datadome_in` inbox file is clunky. The scanner sits in the captcha cooldown loop with no
usable recovery path. They already live in Telegram and want the recovery flow there:
the scanner sends the captcha link via Telegram, they solve it in their phone browser,
paste the `datadome` cookie value as a reply, and the scanner picks it up.

## Goals

- When a DataDome challenge is pending and the Telegram notifier is enabled, the scanner
  sends the challenge URL to all configured chat IDs with an "Open captcha" inline
  button.
- A reply to that message containing a `datadome` cookie value is validated, queued, and
  consumed by the scanner within ~1 second (no `CaptchaCooldown` wait after pasting).
- The existing probe-before-trust flow is reused unchanged: freshness check, apply,
  `get_favorites` probe, rollback on 403, `save_tokens` on success.
- The user gets Telegram acks for the outcome: recovered, rejected (with the button
  re-attached), invalid paste, stale paste.
- Additional path only: TTY prompt, browser-cookie, and inbox-file sources are untouched.

## Non-Goals

- Replacing or reordering the existing recovery mechanisms beyond prepending the
  Telegram queue as the first candidate.
- Sending captcha alerts through non-Telegram notifiers (no Reservation-shaped
  `DATADOME_REQUIRED` status).
- An HTTP-form alternative (pin-prompt style) for the paste step.
- Accepting replies that are not replies to a tracked bot message.
- New configuration keys.

## Configuration

No new keys. Implicit requirements, documented in the wiki:

- `[TELEGRAM] Enabled = true` with `ChatIDs` set → alerts are sent and replies accepted.
- `[TELEGRAM] DisableCommands = false` (default) → inbound polling runs; with `true` the
  reply path is dead (alerts are still sent, but replies cannot be received). A startup
  warning is logged when a challenge is pending and commands are disabled.
- Recommended Termux setup: `CaptchaBrowser = off`, `CaptchaPrompt = false` — Telegram
  becomes the effective recovery flow; the other sources stay configured but inert.

## Behavior

1. Challenge detection is unchanged: `TgtgClient._post` records a `CaptchaChallenge` on a
   403 interstitial; the run loop calls `_recover_captcha` each cycle while pending.
2. `Scanner` owns a `CaptchaExchange` (thread-safe). After `Notifiers(...)` is built in
   `run()`, the scanner attaches it to the Telegram instance (when present) via a public
   accessor on `Notifiers`.
3. Candidate selection order in `_recover_captcha` becomes:
   **telegram queue (non-blocking) → prompt → browser cookie → inbox file**.
4. While a challenge is pending and no alert was sent for its URL yet
   (`exchange.should_alert(url)`, one alert per challenge URL), the scanner calls
   `telegram.send_captcha_alert(url)`. The Telegram queue consumer sends a MarkdownV2
   message — instructions to open the link, solve the captcha, copy the `datadome`
   cookie value (DevTools → Application → Cookies), and reply to the message — plus an
   `InlineKeyboardButton("Open captcha", url=...)`, to all chat IDs, and records the
   sent message IDs in the exchange.
5. A new inbound handler (`MessageHandler(filters.REPLY & filters.TEXT)`, registered
   after the command and share-URL handlers, decorated `@_private`) processes replies:
   - Reply-to must be the bot (`reply_to_message.from_user.id == context.bot.id`) AND
     its `message_id` must be in `exchange.message_ids` (the original alert and any
     tracked rejected-ack). Otherwise: ignored silently.
   - No active alert in the exchange: ignored.
   - Value normalized via `normalize_datadome_value` and checked with
     `_is_plausible_datadome`:
     - Valid → `exchange.offer_cookie(token)`, reply *"Checking the datadome cookie…"*.
     - Invalid → reply *"That doesn't look like a datadome cookie value - copy the
       'datadome' cookie from the solved captcha page and reply again."* Nothing queued.
6. The main loop's chunked sleep breaks early when `exchange.has_pending_cookie()`, so
   the next job cycle (within ~1 s) runs `_recover_captcha`, which consumes one queued
   cookie. Leftover cookies re-trigger the early wake on the following cycle.
7. Probe outcomes (existing logic, unchanged):
   - Success → if an alert was active, enqueue the ack *"Datadome recovered - scanning
     resumes"*, then `exchange.clear()` (drains queue, resets alerted URL and message
     IDs), persist tokens, clear the challenge.
   - 403 → existing rollback; enqueue *"Cookie rejected - open the link again, solve it,
     and reply with a fresh value."* with the challenge button re-attached and the ack
     message ID tracked (so the user may reply to the alert or the ack).
   - Transient probe failure → existing log-and-retry only; no Telegram ack.
   - Candidate equals the current cookie → existing early return; when the candidate
     came from Telegram, enqueue *"Pasted cookie matches the current one - solve the
     captcha for a fresh value."*
8. A new challenge with a different URL re-arms `should_alert` and triggers a fresh
   alert (bot restart included: the exchange is in-memory, but the re-detected challenge
   re-alerts automatically).
9. `mute` and `only_reservations` do not suppress captcha alerts or acks — they are
   operational, not item notifications.

## Components

| File | Change |
|------|--------|
| `tgtg_scanner/captcha.py` | New `CaptchaExchange` (offer/take/has_pending_cookie/should_alert/has_active_alert/clear; message-ID registry; never logs values). New payload dataclasses `CaptchaChallengeAlert(url)` and `CaptchaStatusAck(text, button_url=None)`. Sent message IDs are recorded in the exchange for challenge alerts and for status acks that carry a `button_url` (the rejected ack) — exactly the messages the user may reply to. The recovered and stale acks are not tracked. |
| `tgtg_scanner/notifiers/telegram.py` | Public `attach_captcha_exchange(exchange)` and `send_captcha_alert(url)` / `send_captcha_status_ack(text, button_url=None)` (queue puts). `_run` consumer handles `CaptchaChallengeAlert` / `CaptchaStatusAck` before `_send`; alert/ack sending records message IDs into the exchange. New `_captcha_reply_handler` + registration (after command/URL handlers). |
| `tgtg_scanner/notifiers/notifiers.py` | Public accessor to look up a notifier instance by class (e.g. `get(cls) -> Notifier \| None`). |
| `tgtg_scanner/scanner.py` | Own `CaptchaExchange`; attach to Telegram instance in `run()`. `_recover_captcha`: telegram candidate first, acks + `clear()` on outcomes. `run()` sleep loop: break when a cookie is pending. Startup warning when a challenge is pending and `DisableCommands = true`. |
| `wiki/Configuration.md` | Document the Telegram captcha flow under the Telegram and captcha settings. |

## Error Handling

- Telegram send failures use the existing `_send_message` retry/error handling; a failed
  alert is logged and the challenge stays recoverable via the other sources. A new
  challenge URL re-arms the alert.
- Exceptions in the reply handler are caught by the existing PTB error handler; the
  exchange is never corrupted (queue operations are atomic).
- `attach_captcha_exchange` is a no-op when Telegram is disabled or absent; the scanner
  guards all exchange use so a missing Telegram notifier is always safe.
- The scanner's early-wake check is exception-safe (plain `queue.Empty` semantics).

## Testing

- `CaptchaExchange` unit tests: offer/take roundtrip; take on empty → `None`;
  `has_pending_cookie`; `should_alert` true once per URL, false for the same URL, true
  again for a new URL and after `clear()`; message-ID registry add/contains/clear.
- Telegram handler tests (mocked `Update`): valid reply to tracked message → value
  queued + "Checking…" reply; bare token vs `datadome=...` wrapped form both accepted;
  invalid value → rejection reply, nothing queued; reply to untracked message → ignored;
  unauthorized chat → ignored with warning; no active alert → ignored.
- Scanner tests: telegram cookie wins over an enabled prompt (non-blocking, no stdin
  read); success path clears the exchange and enqueues the recovered ack; 403 path
  rolls back and enqueues the rejected ack with button; stale-value ack; sleep loop
  breaks early when a cookie is pending; alert sent once per challenge URL.
- Regression: existing captcha recovery, prompt, inbox, and telegram notifier tests
  keep passing; `make lint && make test` green.

## Security

- Replies are accepted only from configured chat IDs (`@_private`) and only when they
  reply to a bot message ID the scanner itself recorded.
- The `datadome` cookie value is a session credential: it is never logged (scanner,
  handler, or acks) — matching the existing prompt behavior and its never-logs test.
- Persistence goes only through the existing `save_tokens` path (config.ini and/or
  `TGTG_TOKEN_PATH` files), same as every other recovery source.
