import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from tgtg_scanner.models.item import Item
from tgtg_scanner.models.schedule import Schedule
from tgtg_scanner.tgtg_client import TgtgClient

log = logging.getLogger("tgtg")

UNPAID_WARNING = "UNPAID_WARNING"
PAYMENT_DEADLINE = 270  # seconds; TGTG reservation hold is ~5 minutes
SOLD_OUT_MARKERS = ("sold out", "no longer available", "not available", "out of stock")


def is_sold_out_error(err: Exception) -> bool:
    """Heuristic: does this create_order failure mean we lost the race?"""
    msg = str(err).lower()
    return any(marker in msg for marker in SOLD_OUT_MARKERS)


@dataclass
class Order:
    id: str
    item_id: str
    amount: int
    display_name: str
    status: str | None = None
    created_at: int | None = None


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


def _order_from_dict(data: dict) -> Order:
    return Order(
        id=data["id"],
        item_id=data.get("item_id", ""),
        amount=int(data.get("amount", 1)),
        display_name=data.get("display_name", ""),
        status=data.get("status"),
        created_at=data.get("created_at"),
    )


RESERVATIONS_FILE = "reservations.json"


class Reservations:
    def __init__(self, client: TgtgClient, persist_dir: str | None = None) -> None:
        self.client = client
        self.reservation_query: list[Reservation] = []
        self.active_orders: dict[str, Order] = {}
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

    def register_order(self, order_id: str, item_id: str, amount: int, display_name: str) -> None:
        self.active_orders[order_id] = Order(
            order_id, item_id, amount, display_name, status="RESERVED", created_at=int(time.time())
        )
        self._save()

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

    def make_orders(self, state: dict[str, Item], callback: Callable[[Reservation], None]) -> None:
        """Create orders for reservations.

        Items already handled by _attempt_reservation this cycle
        (identified by reservation_status on the state item) are skipped.

        Args:
            state (Dict[str, Item]): Current item state
            callback (Callable[[Reservation], None]): Callback for each order

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
                    callback(reservation)
                    self.remove(reservation.item_id)
                    log.info("Reservation completed for %s and removed from queue", reservation.display_name)
                except Exception as exc:
                    log.warning("Order failed for %s: %s", reservation.display_name, exc)

    def update_active_orders(self) -> None:
        """Remove orders that are not active anymore."""
        changed = False
        for order_id in list(self.active_orders):
            res = self.client.get_order_status(order_id)
            if res.get("state") != "RESERVED":
                del self.active_orders[order_id]
                changed = True
        if changed:
            self._save()

    def cancel_order(self, order_id: str) -> None:
        """Cancel an order."""
        self.client.abort_order(order_id)
        if order_id in self.active_orders:
            del self.active_orders[order_id]
            self._save()

    def cancel_all_orders(self) -> None:
        """Cancel all active orders."""
        for order_id in list(self.active_orders):
            self.cancel_order(order_id)

    def _create_order(self, reservation: Reservation) -> None:
        if self.client.session:
            self.client.session.last_api_request = None
        log.info("Creating order for %s (qty=%d)", reservation.display_name, reservation.amount)
        res = self.client.create_order(reservation.item_id, reservation.amount)
        order_id = res.get("id")
        if order_id:
            self.active_orders[order_id] = Order(
                order_id,
                reservation.item_id,
                reservation.amount,
                reservation.display_name,
                status="RESERVED",
                created_at=int(time.time()),
            )
            self._save()
            log.info("Order created for %s (order_id=%s)", reservation.display_name, order_id)
