# Smart Reserve: Purchase Confirmation, Per-Item Schedules, Learned Fast Polling

**Date**: 2026-10-10
**Status**: Approved design

## Background: how BagRescue works (reverse-engineered)

BagRescue (bagrescue.com) promises to reserve+purchase TGTG bags on demand. Its public
surface is deliberately thin (only `GET /api/health` in its OpenAPI), but the mechanism is
reconstructible because it uses the same TGTG API this scanner already talks to:

1. **Account linking** — same email-PIN flow as the TGTG app; tokens stored encrypted; no
   password. Identical to this scanner's `tgtg_client` login.
2. **"Auto-purchase" is `create_order` + a saved card** — TGTG has no separate purchase
   endpoint. With a credit/debit card saved in the TGTG account, `POST order/v8/create/{id}`
   reserves and charges automatically (Apple/Google Pay can't work: interactive auth).
   This scanner already makes that call in `Scanner._attempt_reservation()`.
3. **The differentiator is timing** — 24/7 monitoring plus learned per-store drop patterns
   ("learns over the first 48 hours"): poll at high frequency inside predicted drop windows,
   back off otherwise. A fixed 60s interval misses drops that sell out in seconds.
4. **Convenience** — schedule gating (only auto-buy on pickup days), quantity up to 3.

### Payment feasibility: does the API allow purchase without the app?

Yes, with two prerequisites — verified 2026-10-10 against the upstream library and
community projects:

- **The app's fingerprint/face confirmation is an in-app local gate** (BiometricPrompt /
  Face ID), not an API requirement. Order calls authenticate with the same bearer access
  token the scanner already holds; there is no biometric parameter in the order API.
  BagRescue's business model (tokens only, never card data or biometrics) depends on this.
- **With a credit/debit card saved in the TGTG account, `create_order` reserves and
  charges server-side** (TGTG's payment backend charges the stored instrument; order goes
  `RESERVED → CONFIRMED`). Apple/Google Pay can't work via API — they need an interactive
  payment sheet. The upstream `tgtg` library intentionally has no separate "pay" endpoint;
  community payment-encryption projects exist for the no-saved-card case but are out of
  scope.
- **Without a saved card, API orders stay `RESERVED` and expire (~5 min)** — indistinguishable
  from "just reserved" in the old fire-and-forget flow. The purchase-lifecycle tracking in
  this design is the empirical check on a given account: `CONFIRMED` → auto-charge works;
  stuck `RESERVED` → the unpaid warning surfaces the missing prerequisite immediately.

### Gap analysis (motivating this design)

| BagRescue capability | Scanner before this design |
|---|---|
| Email-PIN login, DataDome handling | already present |
| `create_order` auto-purchase with saved card | already present, but "fire and forget" |
| Verify payment actually completed | **no** — `update_active_orders()` drops any non-`RESERVED` order, silently discarding successfully paid (`CONFIRMED`) orders |
| Warn when reservation goes unpaid | **no** — a RESERVED-but-unpaid hold just expires silently |
| Per-store schedule gating | **no** |
| Drop-window learning + fast polling | **no** — fixed `SleepTime` (default 60s, ±10% jitter) |
| Buy everything watched | intentionally **not** wanted — the existing reservation queue stays the trigger |

## Summary

Three capabilities, added on top of the existing reservation-queue trigger (items must
still be queued via Telegram `/reserve` or hand-edited `reservations.json` — no surprise
purchases):

1. **Purchase lifecycle** — after `create_order`, track order status: notify "purchased,
   just pick it up" when `CONFIRMED`; send an urgent "reserved but unpaid — open the app
   now" warning if still `RESERVED` near the end of the ~5 minute hold; fix the bug where
   paid orders were dropped from tracking.
2. **Per-item schedules** — optional `schedule` (days + time windows) on each queue entry;
   outside the schedule no order attempt is made (status `skipped_schedule`), the entry
   stays queued, and the availability notification still goes out.
3. **Learned fast polling** — record drop events per queued item, predict drop windows from
   historical time-of-day patterns, and poll at ~10s (jittered) only while a queued item is
   in a predicted window or an unpaid order is being watched; otherwise behavior is
   unchanged. Master switch: `RESERVATION_FAST_POLL` (default on).

Architecture: **dynamic sleep in the existing single-threaded run loop** (no threads, no
new scheduler dependency). The global cron remains a hard outer gate.

## Requirements

- Reservation queue remains the sole purchase trigger; favorites/items not in the queue are
  never bought.
- Queue entries' existing `amount` field is honored (currently hardcoded to 1); default 1.
- Old `reservations.json` formats (legacy top-level array; object without new fields) keep
  loading; entries without `schedule` are always eligible; entries without `amount` buy 1.
- Learning and schedules apply only to queued items.
- All schedule math uses local time (same basis as the existing cron support).
- Corrupt state files must never crash the loop.
- `make lint` and `make test` pass; existing tests updated only where they encode the old
  drop-non-RESERVED behavior.

## Data Model

### `reservations.json` (extended, backward compatible)

```json
{
  "reservation_query": [
    {
      "item_id": "12345",
      "amount": 1,
      "display_name": "Bakery X",
      "schedule": {
        "days": [0, 1, 2, 3, 4],
        "windows": ["16:00-20:00"]
      }
    }
  ],
  "active_orders": [
    {
      "id": "abc",
      "item_id": "12345",
      "amount": 1,
      "display_name": "Bakery X",
      "status": "RESERVED",
      "created_at": 1760000000
    }
  ]
}
```

- `schedule` optional; `days` are Python `weekday()` numbers (0=Mon … 6=Sun), omitted =
  any day; `windows` are local `"HH:MM-HH:MM"` ranges (wrap-around allowed, e.g.
  `"22:00-02:00"`), omitted = all day.
- `active_orders` entries gain `status` (TGTG status string, or the scanner's own
  `UNPAID_WARNING` marker after the unpaid warning is sent) and `created_at` (epoch).
- `Reservation` and `Order` dataclasses gain `status: str | None = None` and
  `created_at: int | None = None` (defaulted — existing constructors keep working).

### `drop_history.json` (new)

Persisted next to `reservations.json`. A dumb timestamp log:

```json
{ "12345": [1760000000, 1760086400, 1760172800] }
```

- One list of epoch timestamps per queued `item_id`, capped at the last 100 entries,
  trimmed on save.
- No secrets (item IDs and timestamps only).
- Loaded at startup; saved on each new event; corrupt file → rename to
  `*.corrupt-<ts>`, log error, start empty.

## Detailed Design

### 1. Schedule gating

Before any order attempt, `Scanner._attempt_reservation()` checks the queue entry's
`schedule` against `datetime.now()`:

- Outside schedule → no `create_order`; `item.reservation_status = "skipped_schedule"`;
  normal availability notification still sent; entry stays queued.
- Malformed schedule (bad day number, unparseable window) → log a warning, treat as
  "no schedule" (always eligible). Never raise out of the scan cycle.

### 2. Drop learning

Events are recorded when:

- a queued item transitions 0 → available (observed during any cycle), or
- `create_order` for a queued item fails with a **sold-out-class error** — identified by a
  small case-insensitive substring matcher on the error message ("sold out", "no longer
  available", "not available", …). Transient errors (network, DataDome, auth) are not
  drop signals and are never recorded.

Prediction (computed in memory from raw timestamps; no derived state persisted):

- Bucket event timestamps by `(weekday, 15-min slot)` — 96 slots per weekday.
- An item is "in a predicted drop window" when the current `(weekday, slot)` or an
  immediately adjacent slot (slot−1 / slot+1, wrapping 0↔95 within the same weekday
  bucket) has ≥ 2 recorded events. A single observation never triggers fast mode.

### 3. Adaptive polling (dynamic sleep)

After each cycle the loop computes:

```
fast_mode = any queued item matches a predicted window
            OR any active order is still RESERVED or UNPAID_WARNING (payment watch)
effective_sleep = uniform(8, 12) seconds   # fast_mode
                = config.sleep_time ± 10%  # otherwise
```

**Fast cycle** (reduced job):

1. One `get_favorites()` call → observe + attempt reservation for queued favorites.
2. `get_item()` for queued non-favorites **round-robin, one per cycle** — request rate stays
   ~1 per 10s regardless of queue size.
3. `make_orders()` fallback (buys queued items that are already available — in-memory,
   zero API cost unless an order is eligible; sold-out races are recorded for learning).
4. Check status of all `RESERVED` orders (see §4).

**Normal cycle**: unchanged `_job()` (favorites + configured item_ids + `make_orders()`
fallback), plus the order-status check.

The existing global cron gate stays a hard outer gate — no polling (fast or slow) outside
scheduled hours. Fast cycles are spaced ~8–12s apart (dynamic sleep) and normal cycles
~SleepTime ±10%, capping request heat at the cycle level; within a cycle, requests are
issued back-to-back. The existing DataDome captcha fallback in `TgtgClient._post()` applies
to every request unchanged.

### 4. Purchase lifecycle

1. `create_order` succeeds → register in `active_orders` with `status="RESERVED"` and
   `created_at=now`; `item.reservation_status = "reserved"` (kept for template
   back-compat).
2. Every subsequent cycle (fast or normal), pending orders are checked with
   `get_order_status()`:
   - `CONFIRMED` → payment done: send **purchased** notification
     (`Reservation` with `status="CONFIRMED"`); order stays in the ledger as `CONFIRMED`
     (collected/terminal cleanup happens later, see below). Note: the `Item` availability
     notification already went out at drop time — purchase confirmation is always a
     separate, later message.
   - Still `RESERVED` and `now - created_at > ~4.5 min` → send **unpaid warning once**
     (`Reservation` with `status="UNPAID_WARNING"`): "reserved but unpaid — open the TGTG
     app now, hold expires ~5 min". The order entry's persisted `status` is set to
     `UNPAID_WARNING`, so the warning survives restarts and is never repeated.
   - `UNPAID_WARNING` orders keep being polled: if payment lands late (`CONFIRMED`), the
     purchased notification is still sent; if the order goes terminal, the terminal
     handling below applies.
   - Terminal states (`CANCELED`, `EXPIRED`, `REFUNDED`) → notification carrying that
     status; order removed from `active_orders`. Any other unknown state is logged at
     warning level and kept in the ledger (payment watch continues; never silently drop).
3. **Bug fix**: `Reservations.update_active_orders()` (replaced by
   `process_active_orders()`) no longer drops every non-`RESERVED`
   order (which silently discarded paid orders). It becomes the status-tracking method
   described above. Orders in `CONFIRMED` that later leave `get_active()` are dropped with
   a log line (no unbounded ledger growth).

### 5. Notifications

| Moment | Payload | Template vars |
|---|---|---|
| Drop observed (as today) | `Item` | `reservation_status` ∈ `reserved` / `failed` / `skipped_schedule` (new), plus `reservation_error`, `reservation_amount` |
| Payment confirmed | `Reservation`, `status=CONFIRMED` | Telegram default: "purchased — pick it up"; `status` available to all notifiers |
| Unpaid warning (once) | `Reservation`, `status=UNPAID_WARNING` | "act now, hold expiring" |

Telegram's `OnlyReservations` filter continues to treat reservation-message kinds
(including purchase-confirmed and unpaid-warning) as reservation messages.

### 6. Config

One new key, following existing INI + env conventions:

| INI | Env | Default | Meaning |
|---|---|---|---|
| `[RESERVATION] FastPoll` | `RESERVATION_FAST_POLL` | `true` | Master switch for learned fast-polling cadence. Order-status checks still run on normal cycles when off. |

Schedules and drop history are per-item state and stay out of `config.ini`.

### 7. Error handling

- Corrupt `reservations.json` / `drop_history.json` → log error, rename to
  `*.corrupt-<ts>`, continue with empty state.
- DataDome / captcha → existing `_post()` fallback; no new logic. Kill switch
  (`RESERVATION_FAST_POLL=false`) reduces request heat when desired.
- Reauth mid-run → existing `_login_or_reauth()` path, unchanged.
- Nothing raised inside schedule parsing, bucket computation, or status handling may
  escape the scan cycle.

## Files Changed (expected)

| File | Change |
|---|---|
| `tgtg_scanner/models/reservations.py` | `Reservation`/`Order` gain `status`, `created_at`; schedule parsing/validation; `update_active_orders` → purchase-lifecycle tracking; corrupt-file recovery |
| `tgtg_scanner/models/drop_history.py` (new) | timestamp log: record, trim, load/save, corrupt recovery |
| `tgtg_scanner/models/schedule.py` (new or inside reservations) | `Schedule` value type: parse, `is_now` check, malformed-tolerant |
| `tgtg_scanner/models/config.py` | `reservation.fast_poll` (or top-level) config field + INI/env reading |
| `tgtg_scanner/scanner.py` | honor `amount`; schedule gate in `_attempt_reservation`; record drop events; fast-mode decision + reduced fast cycle + round-robin; per-cycle order-status check; dynamic sleep |
| `tgtg_scanner/notifiers/telegram.py` | render `CONFIRMED` / `UNPAID_WARNING` message texts |
| `config.sample.ini`, `wiki/Configuration.md` | document `FastPoll`, schedule + amount fields |
| `tests/` | new coverage per test plan; update tests encoding old `update_active_orders` behavior |

## Test Plan

All tests mocked (no real API), following existing `responses` + `pytest-mock` style.

| Area | Tests |
|---|---|
| Schedule | valid parse; malformed → warning + always-eligible; day gating; window gating incl. wrap-around; `skipped_schedule` status and no `create_order` call |
| Learning | transition recorded; lost race recorded; trim to 100; corrupt file recovery; prediction: ≥2 events in slot/adjacent → in window, 1 event → not |
| Adaptive sleep | fast when predicted window matches; fast when unpaid order pending; normal otherwise; cron still gates everything |
| Purchase lifecycle | RESERVED→CONFIRMED sends purchased notification and keeps order in ledger; unpaid warning fires once near deadline; terminal status notifies and removes order; regression: CONFIRMED orders are not silently dropped |
| Back-compat | legacy array `reservations.json` loads; entries without `schedule` always eligible; entries without `amount` buy 1; existing tests pass |
| Round-robin | two queued non-favorite items alternate `get_item` calls across fast cycles |
| Config | `RESERVATION_FAST_POLL` env + INI parsing, default true |

## Out of Scope (YAGNI)

- Buying everything watched (explicitly rejected — queue stays the trigger).
- Telegram commands for editing schedules (hand-edit `reservations.json` in v1).
- Multi-user fairness rotation, dashboards, billing (multi-tenant concerns).
- Threads / scheduler frameworks (architecture A chosen deliberately).
- Auto-abort of unpaid orders (single user gains nothing from freeing the hold).
