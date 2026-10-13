import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models.item import Item
from tgtg_scanner.models.schedule import Schedule
from tgtg_scanner.tgtg_client import TgtgClient

log = logging.getLogger("tgtg")

UNPAID_WARNING = "UNPAID_WARNING"
PAYMENT_DEADLINE = 270  # seconds; TGTG reservation hold is ~5 minutes
SOLD_OUT_MARKERS = ("sold out", "no longer available", "not available", "out of stock")
TERMINAL_STATES = frozenset({"CANCELED", "CANCELLED", "EXPIRED", "REFUNDED", "ABORTED"})


def is_sold_out_error(err: Exception) -> bool:
    """Heuristic: does this create_order failure mean we lost the race?"""
    msg = str(err).lower()
    return any(marker in msg for marker in SOLD_OUT_MARKERS)


def _redact_secrets(payload):
    """Recursively blank values of secret-ish keys before logging."""
    if isinstance(payload, dict):
        return {
            key: "***"
            if key in ("secret", "authorization_payload", "biometrics_protected_secret", "payment_method_token")
            else _redact_secrets(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [_redact_secrets(entry) for entry in payload]
    return payload


def _choose_payment_method(entries: list) -> dict | None:
    """Prefer a saved/recurring card, then CREDITCARD, then any entry carrying an Adyen payload."""

    def has_payload(entry: dict) -> bool:
        return isinstance(entry, dict) and bool(entry.get("adyen_api_payload"))

    recurring = [entry for entry in entries if has_payload(entry) and entry.get("recurring_info")]
    cards = [entry for entry in entries if has_payload(entry) and entry.get("payment_type") == "CREDITCARD"]
    with_payload = [entry for entry in entries if has_payload(entry)]
    for candidate in (recurring, cards, with_payload):
        if candidate:
            return candidate[0]
    return None


def _authorization_body(method: dict, price: dict | None = None) -> dict:
    """Build the app-shaped pay body from a payment_methods entry (payload straight from TGTG)."""
    auth: dict = {
        "authorization_payload": {
            "payload": method.get("adyen_api_payload") or "",
            "payment_type": str(method.get("payment_type") or "CREDITCARD"),
            "save_payment_method": False,
            "type": "adyenAuthorizationPayload",
        },
        "payment_provider": str(method.get("payment_provider") or "ADYEN"),
        "return_url": "adyencheckout://com.app.tgtg.itemview",
    }
    if price is not None:
        auth["amount"] = price
    return {"authorizations": [auth]}


def _saved_card_from(response: object) -> dict | None:
    """First ACTIVE saved card from a paymentMethod/v2 response."""
    if not isinstance(response, dict):
        return None
    cards = response.get("consumer_saved_psp_payment_methods")
    if not isinstance(cards, list):
        return None
    for card in cards:
        if isinstance(card, dict) and card.get("identifier") and card.get("status") == "ACTIVE":
            return card
    return None


ADYEN_SDK_VERSION = "2.2.27"


def _saved_card_adyen_type(card: dict, entries: list) -> str:
    """Adyen method type for a saved card, from its adyenSavedPaymentMethod entry ('scheme' for cards)."""
    identifier = card.get("identifier")
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("identifier") != identifier:
            continue
        raw = entry.get("adyen_api_payload")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            if isinstance(parsed, dict) and isinstance(parsed.get("type"), str):
                return str(parsed["type"])
    return "scheme"


def _saved_card_authorization(card: dict, biometrics: dict | None, price: dict | None = None, adyen_type: str = "scheme") -> dict:
    """Pay with a saved card: AdyenCustomPayload (type + storedPaymentMethodId + 3DS SDK), no token (APK 26.10.0 lz9/mz9)."""
    secret = None
    if isinstance(biometrics, dict):
        secret = biometrics.get("biometrics_protected_secret")
    payload: dict = {
        "payload": json.dumps(
            {
                "type": adyen_type,
                "storedPaymentMethodId": card.get("identifier"),
                "threeDS2SdkVersion": ADYEN_SDK_VERSION,
            },
            separators=(",", ":"),
        ),
        "payment_type": str(card.get("payment_type") or "CREDITCARD"),
        "save_payment_method": False,
        "type": "adyenAuthorizationPayload",
    }
    if secret:
        payload["biometrics_protected_secret"] = secret
    auth: dict = {
        "authorization_payload": payload,
        "payment_provider": str(card.get("payment_provider") or "ADYEN"),
        "return_url": "adyencheckout://com.app.tgtg.itemview",
    }
    if price is not None:
        auth["amount"] = price
    return {"authorizations": [auth]}


def _first_payment_id(result: object) -> str | None:
    """Extract payment_id from a PaymentResponse ({payments: [...]}) or a flat payment dict."""
    if not isinstance(result, dict):
        return None
    direct = result.get("payment_id") or result.get("paymentId")
    if direct:
        return str(direct)
    payments = result.get("payments")
    if isinstance(payments, list) and payments and isinstance(payments[0], dict):
        pid = payments[0].get("payment_id") or payments[0].get("paymentId")
        if pid:
            return str(pid)
    return None


@dataclass
class Order:
    id: str
    item_id: str
    amount: int
    display_name: str
    status: str | None = None
    created_at: int | None = None
    price: dict | None = None


@dataclass
class Reservation:
    item_id: str
    amount: int
    display_name: str
    schedule: Schedule | None = None
    status: str | None = None


def _reservation_from_dict(data: dict) -> Reservation:
    return Reservation(
        item_id=data["item_id"],
        amount=int(data.get("amount", 1)),
        display_name=data.get("display_name", ""),
        schedule=Schedule.parse(data.get("schedule")),
        status=data.get("status"),
    )


def _extract_price(order: dict) -> dict | None:
    """Pull the Price dict from an order-shaped payload (create response or order/v8 detail)."""
    if not isinstance(order, dict):
        return None
    total = order.get("total_price")
    if isinstance(total, dict):
        return total
    order_line = order.get("order_line")
    lines = order_line if isinstance(order_line, list) else [order_line] if isinstance(order_line, dict) else []
    for line in lines:
        if isinstance(line, dict):
            total = line.get("total_price")
            if isinstance(total, dict):
                return total
    return None


def _order_from_dict(data: dict) -> Order:
    return Order(
        id=data["id"],
        item_id=data.get("item_id", ""),
        amount=int(data.get("amount", 1)),
        display_name=data.get("display_name", ""),
        status=data.get("status"),
        created_at=int(data["created_at"]) if data.get("created_at") is not None else None,
        price=data.get("price"),
    )


RESERVATIONS_FILE = "reservations.json"


class Reservations:
    def __init__(self, client: TgtgClient, persist_dir: str | None = None, pay_probe: bool = False) -> None:
        self.client = client
        self.pay_probe = pay_probe
        self.reservation_query: list[Reservation] = []
        self.active_orders: dict[str, Order] = {}
        self._probe_methods: dict[str, dict] = {}
        self._persist_path = Path(persist_dir) / RESERVATIONS_FILE if persist_dir else None
        self._load()

    def _load(self) -> None:
        if not self._persist_path or not self._persist_path.is_file():
            return
        try:
            with self._persist_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, (list, dict)):
                raise ValueError(f"expected list or object, got {type(data).__name__}")
            if isinstance(data, list):
                entries: list = data
                orders: list = []
            else:
                entries = data.get("reservation_query") or []
                orders = data.get("active_orders") or []
            self.reservation_query = [_reservation_from_dict(r) for r in entries]
            self.active_orders = {o["id"]: _order_from_dict(o) for o in orders}
            log.info(
                "Loaded %d reservations + %d orders from %s",
                len(self.reservation_query),
                len(self.active_orders),
                self._persist_path,
            )
        except (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError) as err:
            log.error("Failed to load reservations: %s", err)
            self._quarantine()

    def _quarantine(self) -> None:
        if self._persist_path and self._persist_path.is_file():
            target = self._persist_path.with_name(f"{self._persist_path.name}.corrupt-{int(time.time())}")
            try:
                self._persist_path.rename(target)
                log.warning("Quarantined corrupt reservations file to %s", target)
            except OSError as err:
                log.error("Failed to quarantine %s: %s", self._persist_path, err)
        self.reservation_query = []
        self.active_orders = {}

    def _save(self) -> None:
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._persist_path.with_name(f"{self._persist_path.name}.tmp")
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(
                    {
                        "reservation_query": [asdict(r) for r in self.reservation_query],
                        "active_orders": [asdict(o) for o in self.active_orders.values()],
                    },
                    f,
                    indent=2,
                )
            tmp_path.replace(self._persist_path)
        except OSError as err:
            log.error("Failed to save reservations: %s", err)

    def register_order(self, order_id: str, item_id: str, amount: int, display_name: str, price: dict | None = None) -> None:
        self.active_orders[order_id] = Order(
            order_id, item_id, amount, display_name, status="RESERVED", created_at=int(time.time()), price=price
        )
        self._save()
        if self.pay_probe:
            self._probe_payment(order_id, item_id, display_name)

    def reserve(self, item_id: str, display_name: str, amount: int = 1) -> None:
        """Create a new reservation.

        Args:
            item_id (str): Item ID
            display_name (str): Item display name
            amount (int, optional): Amount. Defaults to 1.

        """
        self.reservation_query.append(Reservation(item_id, amount, display_name))
        self._save()

    def remove(self, item_id: str) -> bool:
        """Remove the first reservation matching item_id. Persists the change.

        Returns:
            bool: True if a reservation was removed.
        """
        for i, r in enumerate(self.reservation_query):
            if r.item_id == item_id:
                del self.reservation_query[i]
                self._save()
                return True
        return False

    def is_queued(self, item_id: str) -> bool:
        """Check if an item_id has a pending reservation.

        Args:
            item_id (str): Item ID

        Returns:
            bool: True if the item is in the reservation queue.

        """
        return any(r.item_id == item_id for r in self.reservation_query)

    def get_reservation(self, item_id: str) -> Reservation | None:
        """Return the first queued reservation for item_id, or None."""
        for r in self.reservation_query:
            if r.item_id == item_id:
                return r
        return None

    def make_orders(
        self,
        state: dict[str, Item],
        callback: Callable[[Reservation], None],
        on_sold_out: Callable[[str], None] | None = None,
    ) -> None:
        """Create orders for all eligible reservations.

        Items already handled by _attempt_reservation this cycle
        (identified by reservation_status on the state item) are skipped.

        Args:
            state (Dict[str, Item]): Current item state
            callback (Callable[[Reservation], None]): Callback for each order
            on_sold_out: called with item_id when order creation fails with a sold-out-class error

        """
        for reservation in list(self.reservation_query):
            if reservation.schedule is not None and not reservation.schedule.is_now():
                log.debug("Reservation for %s outside schedule window - skipping", reservation.display_name)
                continue
            item = state.get(reservation.item_id)
            if item and item.items_available > 0 and item.reservation_status is None:
                log.info("Processing queued reservation for %s", reservation.display_name)
                try:
                    self._create_order(reservation)
                    item.reservation_status = "reserved"
                    item.reservation_amount = reservation.amount
                    callback(reservation)
                    self.remove(reservation.item_id)
                    log.info("Reservation completed for %s and removed from queue", reservation.display_name)
                except Exception as exc:
                    log.warning("Order failed for %s: %s", reservation.display_name, exc)
                    if on_sold_out is not None and is_sold_out_error(exc):
                        on_sold_out(reservation.item_id)

    def process_active_orders(self) -> list[Order]:
        """Check every tracked order's status and report transitions.

        - PAID (payment settled) / CONFIRMED (collected): notify once, keep in ledger;
          prune CONFIRMED when it leaves get_active().
        - PAYMENT_INITIATED: in progress, update status silently.
        - RESERVED past PAYMENT_DEADLINE: set UNPAID_WARNING and notify once.
        - Terminal state (CANCELED/CANCELLED/EXPIRED/REFUNDED/ABORTED): notify and remove.
        - Order entity gone (HTTP 410): notify as DELETED and remove.
        - Unknown non-empty state: log a warning, keep polling (never silently drop).

        Returns:
            list[Order]: orders that got an event this call (send these to notifiers).
        """
        changed = False
        events: list[Order] = []
        now = int(time.time())
        for order_id, order in list(self.active_orders.items()):
            try:
                res = self.client.get_order_status(order_id)
            except TgtgAPIError as exc:
                if exc.args and exc.args[0] == 410:
                    log.info("Order %s no longer exists (entity deleted) - removing from ledger", order_id)
                    order.status = "DELETED"
                    events.append(order)
                    self.active_orders.pop(order_id, None)
                    changed = True
                    continue
                log.warning("Failed to get status for order %s: %s", order_id, exc)
                continue
            except Exception as exc:
                log.warning("Failed to get status for order %s: %s", order_id, exc)
                continue
            state = res.get("state", "") if isinstance(res, dict) else ""
            if not isinstance(state, str) or not state:
                log.warning("Order %s: unexpected status payload %r - skipping", order_id, res)
                continue
            if state in ("CONFIRMED", "PAID"):
                if order.status != state:
                    order.status = state
                    changed = True
                    events.append(order)
                    if state == "PAID":
                        log.info("Order %s PAID for %s", order_id, order.display_name)
                    else:
                        log.info("Order %s CONFIRMED (purchased) for %s", order_id, order.display_name)
            elif state == "PAYMENT_INITIATED":
                if order.status != state:
                    order.status = state
                    changed = True
                    log.info("Order %s payment initiated for %s", order_id, order.display_name)
            elif state == "RESERVED":
                if order.status == "RESERVED" and order.created_at and now - order.created_at >= PAYMENT_DEADLINE:
                    order.status = UNPAID_WARNING
                    changed = True
                    events.append(order)
                    log.warning("Order %s still unpaid for %s - sending warning", order_id, order.display_name)
            else:
                if state in TERMINAL_STATES:
                    log.info("Order %s entered terminal state %s for %s", order_id, state, order.display_name)
                    order.status = state
                    events.append(order)
                    self.active_orders.pop(order_id, None)
                    changed = True
                else:
                    log.warning("Order %s has unknown state %r for %s - keeping in ledger", order_id, state, order.display_name)
        if any(o.status == "CONFIRMED" for o in self.active_orders.values()):
            if self._prune_collected_orders():
                changed = True
        if changed:
            self._save()
        return events

    def _prune_collected_orders(self) -> bool:
        """Drop CONFIRMED orders that no longer appear in get_active(). Returns True when changed."""
        try:
            active = self.client.get_active()
        except Exception as exc:
            log.warning("Failed to list active orders: %s", exc)
            return False
        if not isinstance(active, dict):
            log.warning("Unexpected get_active() response (%s) - skipping prune", type(active).__name__)
            return False
        orders = active.get("orders", [])
        if not isinstance(orders, list):
            log.warning("Unexpected get_active() orders shape (%s) - skipping prune", type(orders).__name__)
            return False
        active_ids = {o["id"] for o in orders if isinstance(o, dict) and isinstance(o.get("id"), str)}
        pruned = False
        for order_id in [oid for oid, o in self.active_orders.items() if o.status == "CONFIRMED"]:
            if order_id not in active_ids:
                log.info("Confirmed order %s no longer active - removing from ledger", order_id)
                del self.active_orders[order_id]
                pruned = True
        return pruned

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an unpaid active order. CONFIRMED (paid) orders cannot be aborted.

        Returns True when the order was cancelled, False when refused.
        """
        order = self.active_orders.get(order_id)
        if order is not None and order.status == "CONFIRMED":
            log.warning("Refusing to cancel confirmed (paid) order %s", order_id)
            return False
        self.client.abort_order(order_id)
        if order_id in self.active_orders:
            del self.active_orders[order_id]
            self._save()
        return True

    def cancel_all_orders(self) -> tuple[int, int]:
        """Cancel all unpaid active orders. Returns (cancelled, skipped_paid)."""
        cancelled = 0
        skipped = 0
        for order_id, order in list(self.active_orders.items()):
            if order.status == "CONFIRMED":
                skipped += 1
                log.info("Skipping confirmed (paid) order %s", order_id)
                continue
            self.cancel_order(order_id)
            cancelled += 1
        return cancelled, skipped

    def _create_order(self, reservation: Reservation) -> None:
        self.prefetch_probe_methods(reservation.item_id)
        if self.client.session:
            self.client.session.last_api_request = None
        log.info("Creating order for %s (qty=%d)", reservation.display_name, reservation.amount)
        res = self.client.create_order(reservation.item_id, reservation.amount)
        order_id = res.get("id")
        if order_id:
            price = _extract_price(res)
            if self.pay_probe and price is None:
                log.warning("PAYPROBE create_order response has no price (keys=%s)", sorted(res))
            self.register_order(order_id, reservation.item_id, reservation.amount, reservation.display_name, price=price)
            log.info("Order created for %s (order_id=%s)", reservation.display_name, order_id)

    def prefetch_probe_methods(self, item_id: str) -> None:
        """PayProbe: fetch item payment methods BEFORE the order exists (server requires a pre-order fetch)."""
        if not self.pay_probe or item_id in self._probe_methods:
            return
        try:
            methods = self.client.get_payment_methods(item_id=item_id)
            log.info("PAYPROBE pre-reserve payment_methods: %s", _redact_secrets(methods))
            if isinstance(methods, dict):
                self._probe_methods[item_id] = methods
        except Exception as exc:
            log.warning("PAYPROBE pre-reserve payment_methods failed: %s", exc)

    def _probe_payment(self, order_id: str, item_id: str, display_name: str) -> None:
        """PayProbe: walk the app's pay flow against a fresh order. Never raises."""
        log.info("PAYPROBE probing payment endpoints for order %s (%s)", order_id, display_name)
        price: dict | None = None
        order = self.active_orders.get(order_id)
        if order is not None:
            price = order.price
        if price is None:
            try:
                detail = self.client.get_order_detail(order_id)
                price = _extract_price(detail)
                if price is not None and order is not None:
                    order.price = price
                    self._save()
            except Exception as exc:
                log.warning("PAYPROBE order detail failed: %s", exc)
        if price is not None:
            log.info("PAYPROBE order price: %s", price)
        saved_card: dict | None = None
        try:
            saved = self.client.get_payment_methods()
            log.info("PAYPROBE saved_cards: %s", _redact_secrets(saved))
            saved_card = _saved_card_from(saved)
        except Exception as exc:
            log.warning("PAYPROBE saved_cards failed: %s", exc)
        entries: list = []
        cached = self._probe_methods.get(item_id)
        if cached is not None:
            log.info("PAYPROBE payment_methods (pre-reserve cache): %s", _redact_secrets(cached))
            entries = [entry for entry in cached.get("payment_methods") or [] if isinstance(entry, dict)]
        else:
            try:
                methods = self.client.get_payment_methods(item_id=item_id)
                log.info("PAYPROBE payment_methods: %s", _redact_secrets(methods))
                if isinstance(methods, dict):
                    entries = [entry for entry in methods.get("payment_methods") or [] if isinstance(entry, dict)]
            except Exception as exc:
                log.warning("PAYPROBE payment_methods failed: %s", exc)
        chosen = _choose_payment_method(entries)
        if chosen is None and saved_card is None:
            log.warning("PAYPROBE no payment method and no saved card; skipping pay")
            return
        payment_id: str | None = None
        try:
            created = self.client.create_payment(order_id)
            log.info("PAYPROBE create_payment: %s", _redact_secrets(created))
            payment_id = _first_payment_id(created)
        except Exception as exc:
            log.warning("PAYPROBE create_payment failed: %s", exc)
        biometrics: dict | None = None
        if saved_card is not None and payment_id is not None:
            try:
                biometrics = self.client.payment_biometrics(payment_id)
                log.info("PAYPROBE biometrics: %s", _redact_secrets(biometrics))
            except Exception as exc:
                log.warning("PAYPROBE biometrics failed: %s", exc)
        if saved_card is not None:
            adyen_type = _saved_card_adyen_type(saved_card, entries)
            body = _saved_card_authorization(saved_card, biometrics, price, adyen_type)
            log.info("PAYPROBE auth: saved-card (identifier=%s, type=%s)", saved_card.get("identifier"), adyen_type)
        else:
            body = _authorization_body(chosen, price)  # type: ignore[arg-type]
            log.info("PAYPROBE chosen: %s", _redact_secrets(chosen))
        if price is None:
            log.warning("PAYPROBE no order price available; authorization amount omitted")
        self._log_order_status(order_id)
        result = None
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                result = self.client.pay_order(order_id, body)
                break
            except Exception as exc:
                last_exc = exc
                log.warning("PAYPROBE pay_order attempt %d failed (%s)", attempt + 1, exc)
                self._log_order_status(order_id)
                if attempt < 2:
                    time.sleep(2 + 2 * attempt)
        if result is None:
            log.warning("PAYPROBE pay_order failed after retries: %s", last_exc)
            return
        log.info("PAYPROBE pay_order: %s", _redact_secrets(result))
        payment_id = _first_payment_id(result) or payment_id
        if payment_id:
            try:
                status = self.client.get_payment_status(payment_id)
                log.info("PAYPROBE payment_status: %s", _redact_secrets(status))
            except Exception as exc:
                log.warning("PAYPROBE payment_status failed: %s", exc)

    def _log_order_status(self, order_id: str) -> None:
        try:
            state = self.client.get_order_status(order_id)
        except Exception as exc:
            log.warning("PAYPROBE order_status failed: %s", exc)
            return
        if isinstance(state, (dict, list)):
            log.info("PAYPROBE order_status: %s", _redact_secrets(state))
