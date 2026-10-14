import logging
import random
import sys
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Any, NoReturn

from progress.spinner import Spinner

from tgtg_scanner.captcha import read_browser_datadome, read_inbox_datadome
from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models import (
    Config,
    Cron,
    Favorites,
    Item,
    Location,
    Metrics,
    Reservations,
)
from tgtg_scanner.models.drop_history import DropHistory
from tgtg_scanner.models.reservations import (
    PAYMENT_ACTION_REQUIRED,
    PAYMENT_FAILED,
    PAYMENT_RETRY_PENDING,
    UNPAID_WARNING,
    Order,
    is_sold_out_error,
)
from tgtg_scanner.models.reservations import Reservation as ReservationObj
from tgtg_scanner.models.stock_monitor import StockMonitor
from tgtg_scanner.notifiers import Notifiers
from tgtg_scanner.tgtg_client import BASE_URL, TgtgClient, extract_datadome, normalize_cookie, resolve_user_agent

log = logging.getLogger("tgtg")


class Activity:
    """Activity class that creates a spinner if active is True."""

    def __init__(self, active: bool):
        self.active = active
        self.spinner = None
        if self.active:
            self.spinner = Spinner("Scanning... ")

    def next(self) -> None:
        """Next function that updates the spinner."""
        if self.spinner:
            self.spinner.next()

    def flush(self) -> None:
        """Flush function that flushes the spinner."""
        if self.spinner:
            sys.stdout.write("\x1b[80D\x1b[K")
            sys.stdout.flush()


class Scanner:
    """Main Scanner class."""

    def __init__(self, config: Config):
        self.config = config
        self.metrics = Metrics(self.config.metrics_port)
        self.item_ids = {item_id for item_id in self.config.item_ids if item_id}
        self.cron = self.config.schedule_cron
        self.monitor = StockMonitor(price_monitoring=self.config.price_monitoring)
        self.notifiers: Notifiers | None = None
        self.location: Location | None = None
        self.tgtg_client = self._build_client(config)
        reservations_dir = None
        if self.config.file:
            reservations_dir = str(Path(self.config.file).parent)
        elif self.config.token_path:
            reservations_dir = self.config.token_path
        self.reservations = Reservations(self.tgtg_client, reservations_dir)
        self.drop_history = DropHistory(reservations_dir)
        self._rr_index = 0
        self.favorites = Favorites(self.tgtg_client)

    @staticmethod
    def _build_client(config: Config) -> TgtgClient:
        tgtg = config.tgtg
        kwargs: dict[str, Any] = {
            "url": tgtg.base_url or BASE_URL,
            "email": tgtg.username,
            "access_token": tgtg.access_token,
            "refresh_token": tgtg.refresh_token,
            "cookie": normalize_cookie(tgtg.datadome),
            "timeout": tgtg.timeout,
            "access_token_lifetime": tgtg.access_token_lifetime,
            "pin_port": config.port,
            "max_polling_tries": tgtg.max_polling_tries,
            "polling_wait_time": tgtg.polling_wait_time,
        }
        user_agent = resolve_user_agent(tgtg.user_agent, tgtg.apk_version)
        if user_agent:
            kwargs["user_agent"] = user_agent
        return TgtgClient(**kwargs)

    @property
    def state(self) -> dict[str, Item]:
        """Current item state from the stock monitor."""
        return self.monitor.state

    def _item_from_api(self, data: dict) -> Item:
        return Item(data, self.location, self.config.locale, self.config.time_format)

    def _save_tokens(self) -> None:
        self.config.save_tokens(
            self.tgtg_client.access_token or "",
            self.tgtg_client.refresh_token or "",
            extract_datadome(self.tgtg_client),
        )

    def _get_test_item(self) -> Item:
        """Returns an item for test notifications."""
        items = sorted(self._load_favorite_items(), key=lambda x: x.items_available, reverse=True)
        if items:
            return items[0]
        items = sorted(
            [
                self._item_from_api(item)
                for item in self.tgtg_client.get_items(
                    favorites_only=False,
                    latitude=53.5511,
                    longitude=9.9937,
                    radius=50,
                )
            ],
            key=lambda x: x.items_available,
            reverse=True,
        )
        return items[0]

    def _load_items(self) -> list[Item]:
        """Fetch configured item IDs and account favorites as Items."""
        items: list[Item] = []
        for item_id in list(self.item_ids):
            try:
                items.append(self._item_from_api(self.tgtg_client.get_item(item_id)))
            except TgtgAPIError as err:
                if err.args and err.args[0] == 410:
                    log.warning("Configured ItemID %s was deleted (410) - dropping it from the watch list", item_id)
                    self.item_ids.discard(item_id)
                else:
                    log.error(err)
        items.extend(self._load_favorite_items())
        return items

    def _load_favorite_items(self) -> list[Item]:
        try:
            return [self._item_from_api(item) for item in self.tgtg_client.get_favorites()]
        except TgtgAPIError as err:
            log.error("Failed to load favorites: %s", err)
            self.metrics.get_favorites_errors.inc()
            return []

    def _job(self) -> None:
        """Job iterates over all monitored items."""
        if self.notifiers is None:
            raise RuntimeError("Notifiers not initialized!")

        self._process_items(self._load_items())

        amounts = {item_id: item.items_available for item_id, item in self.monitor.state.items()}
        log.debug("new State: %s", amounts)
        self.reservations.make_orders(self.monitor.state, self.notifiers.send, on_sold_out=self.drop_history.record)

        if not self.monitor.state:
            log.warning("No items in observation! Did you add any favorites?")

        self._process_order_events()

        self._save_tokens()

    def _process_items(self, items: list[Item]) -> None:
        """Observe items, attempt reservations on transitions, send notifications."""
        for item in items:
            try:
                self._process_item(item)
            except Exception as exc:
                log.warning("Failed to process item %s: %s", item.display_name, exc)

    def _process_item(self, item: Item) -> None:
        prev = self.monitor.state.get(item.item_id)
        if self.monitor.observe(item):
            if (
                prev is not None
                and prev.items_available == 0
                and item.items_available > 0
                and self.reservations.is_queued(item.item_id)
            ):
                self.drop_history.record(item.item_id)
            self._attempt_reservation(item)
            if item.reservation_status == "reserved":
                self.reservations.remove(item.item_id)
                if self.notifiers:
                    self.notifiers.send(
                        ReservationObj(item.item_id, item.reservation_amount or 1, item.display_name, status="reserved")
                    )
            self._send_messages(item)
            self.metrics.send_notifications.labels(item.item_id, item.display_name).inc()
        self.metrics.update(item)

    def _process_order_events(self) -> None:
        """Poll tracked orders and notify on purchase/unpaid/terminal events."""
        try:
            for order in self.reservations.process_active_orders():
                self._notify_order_event(order)
        except Exception as exc:
            log.warning("Failed to process order events: %s", exc)

    def _fast_mode_active(self) -> bool:
        """True when a queued item is in a predicted drop window or an unpaid order is pending."""
        now = datetime.now()
        for reservation in self.reservations.reservation_query:
            if self.drop_history.in_predicted_window(reservation.item_id, now):
                return True
        for order in self.reservations.active_orders.values():
            if order.status in (
                "RESERVED",
                UNPAID_WARNING,
                "PAYMENT_INITIATED",
                PAYMENT_FAILED,
                PAYMENT_ACTION_REQUIRED,
                PAYMENT_RETRY_PENDING,
            ):
                return True
        return False

    def _next_sleep_time(self) -> float:
        if self.tgtg_client.captcha is not None:
            cooldown = self.config.tgtg.captcha_cooldown
            if cooldown <= 0:
                log.warning("Invalid CaptchaCooldown %s - using default 600", cooldown)
                cooldown = 600
            return cooldown * random.uniform(0.9, 1.1)
        if self.config.reservation.fast_poll and self._fast_mode_active():
            return 8.0 + 4.0 * random.random()
        return self.config.sleep_time * (0.9 + 0.2 * random.random())

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
            if not candidate or candidate == extract_datadome(self.tgtg_client):
                log.debug("Captcha pending - no fresh datadome cookie yet (solve: %s)", challenge.url)
                return
            self.tgtg_client.apply_datadome(candidate)
            try:
                self.tgtg_client.get_favorites()
            except Exception as exc:
                if isinstance(exc, TgtgAPIError):
                    log.warning("Candidate datadome cookie rejected: %s", exc)
                else:
                    log.warning("Datadome recovery probe failed (%s) - staying in cooldown", exc)
                if self.tgtg_client.captcha is None:
                    self.tgtg_client.captcha = challenge
                return
            self.tgtg_client.captcha = None
            self._save_tokens()
            if from_inbox and inbox_path is not None:
                try:
                    inbox_path.unlink()
                except OSError as exc:
                    log.warning("Could not remove consumed captcha inbox %s: %s", inbox_path, exc)
            log.info("Datadome captcha recovered - resuming normal scanning")
        except Exception as exc:
            log.warning("Captcha recovery attempt failed: %s", exc)

    def _fast_job(self) -> None:
        """Reduced cycle while fast mode is active."""
        if self.notifiers is None:
            raise RuntimeError("Notifiers not initialized!")
        items = self._load_favorite_items()
        favorite_ids = {item.item_id for item in items}
        non_favorites = sorted(r.item_id for r in self.reservations.reservation_query if r.item_id not in favorite_ids)
        if non_favorites:
            item_id = non_favorites[self._rr_index % len(non_favorites)]
            self._rr_index += 1
            try:
                items.append(self._item_from_api(self.tgtg_client.get_item(item_id)))
            except TgtgAPIError as err:
                if err.args and err.args[0] == 410:
                    log.warning("Queued item %s was deleted (410) - removing it from the reservation queue", item_id)
                    self.reservations.remove(item_id)
                else:
                    log.error(err)
        self._process_items(items)
        self.reservations.make_orders(self.monitor.state, self.notifiers.send, on_sold_out=self.drop_history.record)
        self._process_order_events()
        self._save_tokens()

    def _notify_order_event(self, order: Order) -> None:
        if self.notifiers:
            self.notifiers.send(ReservationObj(order.item_id, order.amount, order.display_name, status=order.status))

    def _attempt_reservation(self, item: Item) -> None:
        reservation = self.reservations.get_reservation(item.item_id)
        if reservation is None:
            return
        if reservation.schedule and not reservation.schedule.is_now():
            log.info("Outside schedule for %s - skipping reservation", item.display_name)
            item.reservation_status = "skipped_schedule"
            return
        amount = reservation.amount or 1
        log.info("Reserving %d bag(s) for %s", amount, item.display_name)
        self.reservations.prefetch_probe_methods(item.item_id)
        try:
            if self.tgtg_client.session:
                self.tgtg_client.session.last_api_request = None
            order = self.tgtg_client.create_order(item.item_id, amount)
            item.reservation_status = "reserved"
            item.reservation_amount = amount
            self.reservations.register_order(order["id"], item.item_id, amount, item.display_name)
            log.info("Reserved %d bag(s) for %s (order_id=%s)", amount, item.display_name, order["id"])
        except TgtgAPIError as err:
            item.reservation_status = "failed"
            item.reservation_error = str(err)
            log.warning("Reservation failed for %s: %s", item.display_name, err)
            if is_sold_out_error(err):
                self.drop_history.record(item.item_id)

    def _send_messages(self, item: Item) -> None:
        """Send notifications for Item."""
        if self.notifiers is None:
            raise RuntimeError("Notifiers not initialized!")

        log.info(
            "Sending notifications for %s - %s bags available",
            item.display_name,
            item.items_available,
        )
        self.notifiers.send(item)

    def _login_or_reauth(self) -> None:
        """Login to TGTG API. If token refresh fails and email is configured, re-authenticate."""
        try:
            self.tgtg_client.login()
        except TgtgAPIError as err:
            if not self.tgtg_client.email:
                log.error("Token refresh failed and no email configured for re-authentication: %s", err)
                raise
            log.warning("Token refresh failed, re-authenticating with %s...", self.tgtg_client.email)
            self.tgtg_client.access_token = None
            self.tgtg_client.refresh_token = None
            self.tgtg_client.cookie = None
            self.tgtg_client.session.cookies.clear()
            self.tgtg_client.login()
        self._save_tokens()

    def run(self) -> NoReturn:
        """Main Loop of the Scanner."""
        # test tgtg API
        self._login_or_reauth()
        # activate location service
        self.location = Location(
            self.config.location.enabled,
            self.config.location.google_maps_api_key,
            self.config.location.origin_address,
        )
        # activate and test notifiers
        if self.config.metrics:
            self.metrics.enable_metrics()
        self.notifiers = Notifiers(self.config, self.reservations, self.favorites)
        self.notifiers.start()
        if not self.config.disable_tests and self.notifiers.notifier_count > 0:
            log.info("Sending test Notifications ...")
            self.notifiers.send(self._get_test_item())
        # start scanner
        log.info("Scanner started ...")
        running = True
        if self.cron != Cron("* * * * *"):
            log.info("Active on schedule: %s", self.cron.get_description(self.config.locale))
        activity = Activity(self.config.activity and not (self.config.docker or self.config.quiet))
        while True:
            if self.cron.is_now:
                if not running:
                    log.info("Scanner reenabled by cron schedule.")
                    running = True
                try:
                    self._run_job()
                except Exception:
                    log.error("Job Error! - %s", sys.exc_info())
                finally:
                    try:
                        sleep_time = self._next_sleep_time()
                    except Exception:
                        log.warning("Failed to compute next sleep time - using default")
                        sleep_time = self.config.sleep_time * (0.9 + 0.2 * random.random())
                    steps = max(int(sleep_time), 1)
                    for _ in range(steps):
                        activity.next()
                        sleep(sleep_time / steps)
                        activity.flush()
            elif running:
                log.info("Scanner disabled by cron schedule.")
                running = False
            else:
                sleep(60)

    def stop(self) -> None:
        """Stop scanner."""
        if self.notifiers:
            self.notifiers.stop()

    def get_credentials(self) -> dict:
        """Returns current tgtg credentials.

        Returns:
            dict: dictionary containing access token, refresh token and datadome cookie

        """
        return self.tgtg_client.get_credentials()

    def get_items(self, lat, lng, radius) -> list[dict]:
        """Get items by geographic position.

        Args:
            lat (float): latitude
            lng (float): longitude
            radius (int): radius in meter

        Returns:
            List: List of found items

        """
        return self.tgtg_client.get_items(
            favorites_only=False,
            latitude=lat,
            longitude=lng,
            radius=radius,
        )

    def get_favorites(self) -> list[dict]:
        """Returns favorites of the current tgtg account.

        Returns:
            List: List of items

        """
        return self.tgtg_client.get_favorites()

    def set_favorite(self, item_id: str) -> None:
        """Add item to favorites.

        Args:
            item_id (str): Item ID

        """
        self.tgtg_client.set_favorite(item_id=item_id, is_favorite=True)

    def unset_favorite(self, item_id: str) -> None:
        """Remove item from favorites.

        Args:
            item_id (str): Item ID

        """
        self.tgtg_client.set_favorite(item_id=item_id, is_favorite=False)

    def unset_all_favorites(self) -> None:
        """Remove all items from favorites."""
        item_ids = [item.get("item", {}).get("item_id") for item in self.get_favorites()]
        for item_id in item_ids:
            if item_id:
                self.unset_favorite(item_id)


if __name__ == "__main__":
    print("Please use __main__.py.")
