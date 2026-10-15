import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_mock.plugin import MockerFixture

from tgtg_scanner.captcha import CaptchaChallenge
from tgtg_scanner.errors import TgtgAPIError, TgtgLoginError
from tgtg_scanner.models import Config, Item
from tgtg_scanner.models.schedule import Schedule
from tgtg_scanner.scanner import Scanner
from tgtg_scanner.tgtg_client import extract_datadome


@pytest.fixture
def scanner(mocker: MockerFixture) -> Scanner:
    mocker.patch("tgtg_scanner.tgtg_client.TgtgClient.login", return_value=None)
    mocker.patch("tgtg_scanner.scanner.Metrics")
    config = Config()
    config.tgtg.access_token = "test_token"
    config.tgtg.refresh_token = "test_refresh"
    return Scanner(config)


def test_auto_reserve_queued_item_success(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mock_create_order = mocker.patch.object(scanner.tgtg_client, "create_order", return_value={"id": "ord_123"})
    mock_register = mocker.patch.object(scanner.reservations, "register_order")

    scanner._attempt_reservation(test_item)

    assert test_item.reservation_status == "reserved"
    assert test_item.reservation_amount == 1
    mock_create_order.assert_called_once_with(test_item.item_id, 1)
    mock_register.assert_called_once_with("ord_123", test_item.item_id, 1, test_item.display_name)


def test_auto_reserve_queued_item_failure(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mocker.patch.object(scanner.tgtg_client, "create_order", side_effect=TgtgAPIError(403, b"rate limited"))

    scanner._attempt_reservation(test_item)

    assert test_item.reservation_status == "failed"
    assert test_item.reservation_error is not None


def test_auto_reserve_skips_unlisted_item(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    mock_create_order = mocker.patch.object(scanner.tgtg_client, "create_order")

    scanner._attempt_reservation(test_item)

    assert test_item.reservation_status is None
    mock_create_order.assert_not_called()


def test_login_reauth_on_refresh_failure(scanner: Scanner, mocker: MockerFixture):
    """When login fails with TgtgAPIError and email is set, scanner re-authenticates."""
    login_mock = mocker.patch.object(scanner.tgtg_client, "login")
    login_mock.side_effect = [TgtgAPIError(403, b"datadome challenge"), None]
    scanner.tgtg_client.email = "test@example.com"
    save_mock = mocker.patch.object(scanner, "_save_tokens")

    scanner._login_or_reauth()

    assert login_mock.call_count == 2
    assert scanner.tgtg_client.access_token is None
    assert scanner.tgtg_client.refresh_token is None
    save_mock.assert_called()


def test_login_no_reauth_without_email(scanner: Scanner, mocker: MockerFixture):
    """When login fails and no email is set, TgtgAPIError propagates."""
    mocker.patch.object(scanner.tgtg_client, "login", side_effect=TgtgAPIError(403, b"challenge"))
    scanner.tgtg_client.email = None

    with pytest.raises(TgtgAPIError):
        scanner._login_or_reauth()


def _pending_challenge() -> CaptchaChallenge:
    return CaptchaChallenge(
        url="https://geo.captcha-delivery.com/interstitial/abc",
        first_seen=datetime.now(timezone.utc),
    )


def test_login_refresh_blocked_by_captcha_defers_without_wiping_tokens(scanner: Scanner, mocker: MockerFixture):
    """A DataDome-blocked refresh must not wipe tokens or force email re-auth - the run loop recovers."""
    scanner.tgtg_client.captcha = _pending_challenge()
    scanner.tgtg_client.access_token = "test_token"
    scanner.tgtg_client.refresh_token = "test_refresh"
    scanner.tgtg_client.email = "test@example.com"
    login_mock = mocker.patch.object(
        scanner.tgtg_client,
        "login",
        side_effect=TgtgAPIError(403, b'{"url":"https://geo.captcha-delivery.com/interstitial/abc"}'),
    )
    save_mock = mocker.patch.object(scanner, "_save_tokens")

    scanner._login_or_reauth()

    assert login_mock.call_count == 1  # no destructive re-auth attempt
    assert scanner.tgtg_client.access_token == "test_token"
    assert scanner.tgtg_client.refresh_token == "test_refresh"
    save_mock.assert_not_called()


def test_login_email_flow_blocked_by_captcha_does_not_crash(scanner: Scanner, mocker: MockerFixture):
    """TgtgLoginError from a captcha-blocked email login defers to recovery instead of killing startup."""
    scanner.tgtg_client.captcha = _pending_challenge()
    scanner.tgtg_client.email = "test@example.com"
    mocker.patch.object(
        scanner.tgtg_client,
        "login",
        side_effect=TgtgLoginError(403, b'{"url":"https://geo.captcha-delivery.com/interstitial/abc"}'),
    )

    scanner._login_or_reauth()  # must not raise


def test_login_email_flow_failure_without_captcha_propagates(scanner: Scanner, mocker: MockerFixture):
    """A non-captcha TgtgLoginError still propagates."""
    scanner.tgtg_client.captcha = None
    scanner.tgtg_client.email = "test@example.com"
    mocker.patch.object(scanner.tgtg_client, "login", side_effect=TgtgLoginError(403, b"bad request"))

    with pytest.raises(TgtgLoginError):
        scanner._login_or_reauth()


def test_reauth_second_login_blocked_by_captcha_defers(scanner: Scanner, mocker: MockerFixture):
    """Captcha appearing during the post-wipe email login must not crash either."""
    scanner.tgtg_client.captcha = None
    scanner.tgtg_client.email = "test@example.com"
    mocker.patch.object(scanner, "_save_tokens")
    login_mock = mocker.patch.object(scanner.tgtg_client, "login")
    attempts: list[int] = []

    def fake_login() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise TgtgAPIError(403, b"datadome challenge")
        scanner.tgtg_client.captcha = _pending_challenge()
        raise TgtgLoginError(403, b'{"url":"https://geo.captcha-delivery.com/interstitial/abc"}')

    login_mock.side_effect = fake_login

    scanner._login_or_reauth()

    assert login_mock.call_count == 2


def test_startup_test_notification_skipped_while_captcha_pending(scanner: Scanner, mocker: MockerFixture):
    """Startup must not hit the API for a test notification while captcha recovery is pending."""
    scanner.tgtg_client.captcha = _pending_challenge()
    notifiers_mock = mocker.MagicMock()
    notifiers_mock.notifier_count = 1
    scanner.notifiers = notifiers_mock
    get_test_item = mocker.patch.object(scanner, "_get_test_item")

    scanner._send_startup_test_notification()

    get_test_item.assert_not_called()
    notifiers_mock.send.assert_not_called()


def test_startup_test_notification_sent_when_captcha_clear(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = None
    notifiers_mock = mocker.MagicMock()
    notifiers_mock.notifier_count = 1
    scanner.notifiers = notifiers_mock
    test_item = mocker.patch.object(scanner, "_get_test_item", return_value=Item({"item_id": "test"}))

    scanner._send_startup_test_notification()

    test_item.assert_called_once()
    notifiers_mock.send.assert_called_once()


def test_startup_test_notification_skipped_when_tests_disabled(scanner: Scanner, mocker: MockerFixture):
    scanner.config.disable_tests = True
    notifiers_mock = mocker.MagicMock()
    notifiers_mock.notifier_count = 1
    scanner.notifiers = notifiers_mock
    get_test_item = mocker.patch.object(scanner, "_get_test_item")

    scanner._send_startup_test_notification()

    get_test_item.assert_not_called()


def test_auto_reserve_uses_queue_amount(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name, amount=2)
    mock_create_order = mocker.patch.object(scanner.tgtg_client, "create_order", return_value={"id": "ord_1"})
    mocker.patch.object(scanner.reservations, "register_order")

    scanner._attempt_reservation(test_item)

    mock_create_order.assert_called_once_with(test_item.item_id, 2)
    assert test_item.reservation_amount == 2


def test_auto_reserve_outside_schedule_skips(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Reservation

    schedule = Schedule.parse({"days": [0], "windows": ["00:00-00:01"]})
    assert schedule is not None
    schedule.is_now = lambda now=None: False  # type: ignore[method-assign]
    scanner.reservations.reservation_query = [Reservation(test_item.item_id, 1, test_item.display_name, schedule=schedule)]
    mock_create_order = mocker.patch.object(scanner.tgtg_client, "create_order")

    scanner._attempt_reservation(test_item)

    mock_create_order.assert_not_called()
    assert test_item.reservation_status == "skipped_schedule"


def test_auto_reserve_inside_schedule_proceeds(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    schedule = Schedule.parse({"days": [0], "windows": ["00:00-00:01"]})
    assert schedule is not None
    schedule.is_now = lambda now=None: True  # type: ignore[method-assign]
    from tgtg_scanner.models.reservations import Reservation

    scanner.reservations.reservation_query = [Reservation(test_item.item_id, 1, test_item.display_name, schedule=schedule)]
    mocker.patch.object(scanner.tgtg_client, "create_order", return_value={"id": "ord_1"})
    mock_register = mocker.patch.object(scanner.reservations, "register_order")

    scanner._attempt_reservation(test_item)

    mock_register.assert_called_once()
    assert test_item.reservation_status == "reserved"


def test_lost_race_records_drop_event(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mocker.patch.object(scanner.tgtg_client, "create_order", side_effect=TgtgAPIError(400, b"Item is sold out"))
    mock_record = mocker.patch.object(scanner.drop_history, "record")

    scanner._attempt_reservation(test_item)

    mock_record.assert_called_once_with(test_item.item_id)


def test_non_sold_out_error_records_nothing(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mocker.patch.object(scanner.tgtg_client, "create_order", side_effect=TgtgAPIError(403, b"captcha challenge"))
    mock_record = mocker.patch.object(scanner.drop_history, "record")

    scanner._attempt_reservation(test_item)

    mock_record.assert_not_called()


def test_job_records_drop_on_transition(scanner: Scanner, test_item: Item, tgtg_item: dict, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    # StockMonitor.observe returns False on first sight - pre-seed with an unavailable snapshot
    scanner.monitor.state[test_item.item_id] = Item(dict(tgtg_item, items_available=0))
    mocker.patch.object(scanner, "_load_items", return_value=[test_item])
    mocker.patch.object(scanner.tgtg_client, "create_order", side_effect=TgtgAPIError(403, b"captcha challenge"))
    mocker.patch.object(scanner, "_send_messages")
    mocker.patch.object(scanner, "_save_tokens")
    scanner.notifiers = mocker.MagicMock()
    mock_record = mocker.patch.object(scanner.drop_history, "record")

    scanner._job()

    mock_record.assert_called_once_with(test_item.item_id)


def test_price_drop_notify_does_not_record_drop(scanner: Scanner, test_item: Item, tgtg_item: dict, mocker: MockerFixture):
    scanner.monitor.state[test_item.item_id] = Item(dict(tgtg_item))
    scanner.monitor.price_monitoring = True
    data = dict(tgtg_item)
    data["item"] = dict(tgtg_item["item"], item_price={"code": "EUR", "decimals": 2, "minor_units": 250})
    new_item = Item(data)
    assert new_item._price < test_item._price  # lower price, same availability

    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mocker.patch.object(scanner.tgtg_client, "create_order", return_value={"id": "ord_1"})
    mocker.patch.object(scanner.reservations, "register_order")
    mock_record = mocker.patch.object(scanner.drop_history, "record")
    scanner.notifiers = mocker.MagicMock()

    scanner._process_items([new_item])

    assert scanner.monitor.state[new_item.item_id] is new_item
    mock_record.assert_not_called()
    # _attempt_reservation only runs when observe() returned True (price-drop notify)
    assert new_item.reservation_status == "reserved"


def test_unqueued_transition_does_not_record_drop(scanner: Scanner, test_item: Item, tgtg_item: dict, mocker: MockerFixture):
    scanner.monitor.state[test_item.item_id] = Item(dict(tgtg_item, items_available=0))
    mocker.patch.object(scanner, "_load_items", return_value=[test_item])
    mocker.patch.object(scanner, "_send_messages")
    scanner.notifiers = mocker.MagicMock()
    mock_record = mocker.patch.object(scanner.drop_history, "record")
    mock_create = mocker.patch.object(scanner.tgtg_client, "create_order")

    scanner._job()

    mock_record.assert_not_called()
    mock_create.assert_not_called()


def test_process_order_events_sends_reservation_notifications(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Order

    order = Order("o1", "123", 1, "Bakery", status="CONFIRMED", created_at=1)
    scanner.notifiers = mocker.MagicMock()
    mocker.patch.object(scanner.reservations, "process_active_orders", return_value=[order])

    scanner._process_order_events()

    sent = scanner.notifiers.send.call_args[0][0]  # type: ignore[union-attr]
    assert sent.item_id == "123"
    assert sent.status == "CONFIRMED"
    assert sent.display_name == "Bakery"


def test_notify_order_event_passes_auth_url(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Order

    scanner.notifiers = mocker.MagicMock()
    send = mocker.patch.object(scanner.notifiers, "send")
    order = Order(
        id="o5",
        item_id="i1",
        amount=1,
        display_name="Bakery",
        status="PAYMENT_ACTION_REQUIRED",
        auth_url="https://bank.example/3ds",
    )
    scanner._notify_order_event(order)
    sent_obj = send.call_args[0][0]
    assert sent_obj.auth_url == "https://bank.example/3ds"
    assert sent_obj.status == "PAYMENT_ACTION_REQUIRED"


def test_job_calls_process_order_events(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_load_items", return_value=[])
    mocker.patch.object(scanner, "_process_items")
    mocker.patch.object(scanner, "_save_tokens")
    scanner.notifiers = mocker.MagicMock()
    mocker.patch.object(scanner.reservations, "make_orders")
    mock_events = mocker.patch.object(scanner, "_process_order_events")

    scanner._job()

    mock_events.assert_called_once()


def test_fast_mode_when_predicted_window(scanner: Scanner, mocker: MockerFixture):
    scanner.reservations.reserve("123", "Test")
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=True)
    assert scanner._fast_mode_active()


def test_fast_mode_when_unpaid_order_pending(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Order

    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    scanner.reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=1)
    assert scanner._fast_mode_active()


def test_fast_mode_when_payment_initiated(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import Order

    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    scanner.reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=1)
    assert scanner._fast_mode_active()


def test_fast_mode_off_without_queue_or_orders(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    assert not scanner._fast_mode_active()


def test_fast_mode_disabled_by_config(scanner: Scanner, mocker: MockerFixture):
    scanner.config.reservation.fast_poll = False
    scanner.reservations.reserve("123", "Test")
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=True)
    # _fast_mode_active still reports True (it's the signal); the gate is in _run_job/_next_sleep_time
    assert scanner._fast_mode_active()
    assert scanner._next_sleep_time() >= scanner.config.sleep_time * 0.9


def test_next_sleep_time_fast_range(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_fast_mode_active", return_value=True)
    for _ in range(20):
        assert 8.0 <= scanner._next_sleep_time() <= 12.0


def test_next_sleep_time_normal_range(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_fast_mode_active", return_value=False)
    for _ in range(20):
        assert scanner.config.sleep_time * 0.9 <= scanner._next_sleep_time() <= scanner.config.sleep_time * 1.1


def test_fast_job_favorites_and_one_non_favorite(scanner: Scanner, tgtg_item: dict, mocker: MockerFixture):
    fav = dict(tgtg_item, item=dict(tgtg_item["item"], item_id="fav1"))
    scanner.reservations.reserve("nf1", "NF One")
    scanner.reservations.reserve("nf2", "NF Two")
    scanner.reservations.reserve("fav1", "Fav")
    mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[fav])
    mock_get_item = mocker.patch.object(scanner.tgtg_client, "get_item", return_value=tgtg_item)
    mocker.patch.object(scanner, "_process_items")
    mock_order_events = mocker.patch.object(scanner, "_process_order_events")
    mock_make_orders = mocker.patch.object(scanner.reservations, "make_orders")
    mocker.patch.object(scanner, "_save_tokens")
    scanner.notifiers = mocker.MagicMock()

    scanner._fast_job()
    first = mock_get_item.call_args[0][0]
    scanner._fast_job()
    second = mock_get_item.call_args[0][0]

    assert {first, second} == {"nf1", "nf2"}
    assert mock_get_item.call_count == 2
    assert mock_order_events.call_count == 2  # spec §4: order events every cycle, fast or normal
    assert mock_make_orders.call_count == 2  # already-available queued items still get bought


def test_attempt_reservation_prefetches_probe_methods(scanner: Scanner, test_item: Item, mocker: MockerFixture):
    scanner.reservations.reserve(test_item.item_id, test_item.display_name)
    mock_prefetch = mocker.patch.object(scanner.reservations, "prefetch_probe_methods")
    mocker.patch.object(scanner.tgtg_client, "create_order", return_value={"id": "ord_1"})

    scanner._attempt_reservation(test_item)

    mock_prefetch.assert_called_once_with(test_item.item_id)


def test_load_items_prunes_deleted_item_id(scanner: Scanner, mocker: MockerFixture):
    scanner.item_ids.add("dead1")
    mocker.patch.object(
        scanner.tgtg_client,
        "get_item",
        side_effect=TgtgAPIError(410, b'{"errors":[{"code":"ENTITY_DELETED"}]}'),
    )
    mocker.patch.object(scanner, "_load_favorite_items", return_value=[])
    assert scanner._load_items() == []
    assert "dead1" not in scanner.item_ids


def test_job_removes_deleted_queued_reservation(scanner: Scanner, tgtg_item: dict, mocker: MockerFixture):
    fav_item = Item(dict(tgtg_item, item=dict(tgtg_item["item"], item_id="fav1")))
    scanner.reservations.reserve("gone1", "Gone Bag")
    mocker.patch.object(scanner, "_load_favorite_items", return_value=[fav_item])
    mocker.patch.object(scanner, "_process_items")
    mocker.patch.object(scanner, "_process_order_events")
    mocker.patch.object(scanner, "_save_tokens")
    mocker.patch.object(scanner.reservations, "make_orders")
    mocker.patch.object(
        scanner.tgtg_client,
        "get_item",
        side_effect=TgtgAPIError(410, b'{"errors":[{"code":"ENTITY_DELETED"}]}'),
    )
    scanner.notifiers = mocker.MagicMock()

    scanner._fast_job()

    assert scanner.reservations.reservation_query == []


def test_fast_mode_when_payment_failed(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import PAYMENT_FAILED, Order

    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    scanner.reservations.active_orders["o1"] = Order("o1", "123", 1, "T", status=PAYMENT_FAILED, created_at=1, payment_id="p1")
    assert scanner._fast_mode_active()


def test_fast_mode_when_payment_retry_pending(scanner: Scanner, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import PAYMENT_RETRY_PENDING, Order

    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    scanner.reservations.active_orders["o1"] = Order(
        "o1", "123", 1, "T", status=PAYMENT_RETRY_PENDING, created_at=1, payment_id="p1"
    )
    assert scanner._fast_mode_active()


def _pending_captcha() -> CaptchaChallenge:
    return CaptchaChallenge(url="https://geo.captcha-delivery.com/interstitial/x", first_seen=datetime.now(timezone.utc))


def test_next_sleep_uses_captcha_cooldown(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = _pending_captcha()
    scanner.config.tgtg.captcha_cooldown = 600
    mocker.patch("tgtg_scanner.scanner.random.uniform", return_value=1.05)

    assert scanner._next_sleep_time() == 600 * 1.05


def test_next_sleep_cooldown_clamps_nonpositive(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.random.uniform", return_value=1.0)
    scanner.config.tgtg.captcha_cooldown = 0
    assert scanner._next_sleep_time() == 600.0
    scanner.config.tgtg.captcha_cooldown = -5
    assert scanner._next_sleep_time() == 600.0


def test_run_job_routes_to_recovery_when_captcha_pending(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_recover = mocker.patch.object(scanner, "_recover_captcha")
    mock_job = mocker.patch.object(scanner, "_job")
    mock_fast = mocker.patch.object(scanner, "_fast_job")

    scanner._run_job()

    mock_recover.assert_called_once()
    mock_job.assert_not_called()
    mock_fast.assert_not_called()


def test_run_job_runs_normal_job_when_no_captcha(scanner: Scanner, mocker: MockerFixture):
    mock_recover = mocker.patch.object(scanner, "_recover_captcha")
    mock_job = mocker.patch.object(scanner, "_job")
    mock_fast = mocker.patch.object(scanner, "_fast_job")

    scanner._run_job()

    mock_job.assert_called_once()
    mock_fast.assert_not_called()
    mock_recover.assert_not_called()


def test_run_job_uses_fast_job_in_fast_mode(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner, "_recover_captcha")
    mocker.patch.object(scanner, "_fast_mode_active", return_value=True)
    mock_job = mocker.patch.object(scanner, "_job")
    mock_fast = mocker.patch.object(scanner, "_fast_job")

    scanner._run_job()

    mock_fast.assert_called_once()
    mock_job.assert_not_called()


def test_recover_captcha_browser_success_resumes(scanner: Scanner, tmp_path: Path, mocker: MockerFixture, caplog):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_browser = mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="browsercookie")
    mock_inbox = mocker.patch("tgtg_scanner.scanner.read_inbox_datadome")
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    with caplog.at_level(logging.INFO, logger="tgtg"):
        scanner._recover_captcha()

    mock_browser.assert_called_once()
    mock_inbox.assert_not_called()
    mock_favorites.assert_called_once()
    mock_save.assert_called_once()
    assert scanner.tgtg_client.captcha is None
    assert scanner.tgtg_client.cookie == "datadome=browsercookie"
    assert "resuming normal scanning" in caplog.text


def test_recover_captcha_falls_back_to_inbox(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    (tmp_path / "datadome_in").write_text("datadome=inboxcookie; Path=/\n", encoding="utf-8")
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_browser = mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value=None)
    mock_inbox = mocker.patch("tgtg_scanner.scanner.read_inbox_datadome", return_value="inboxcookie")
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    mock_browser.assert_called_once()
    mock_inbox.assert_called_once_with(Path(tmp_path, "datadome_in"))
    assert scanner.tgtg_client.captcha is None
    assert scanner.tgtg_client.cookie == "datadome=inboxcookie"
    mock_favorites.assert_called_once()
    mock_save.assert_called_once()
    assert not (tmp_path / "datadome_in").exists()


def test_recover_captcha_stays_pending_on_failed_probe(scanner: Scanner, tmp_path: Path, mocker: MockerFixture, caplog):
    scanner.config.token_path = str(tmp_path)
    (tmp_path / "datadome_in").write_text("badcookie", encoding="utf-8")
    scanner.tgtg_client.captcha = _pending_captcha()
    scanner.tgtg_client.apply_datadome("oldcookie")
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value=None)
    mocker.patch.object(scanner.tgtg_client, "get_favorites", side_effect=TgtgAPIError(403, b"captcha challenge"))
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    with caplog.at_level(logging.WARNING, logger="tgtg"):
        scanner._recover_captcha()

    assert scanner.tgtg_client.captcha is not None
    assert scanner.tgtg_client.cookie == "datadome=oldcookie"
    assert extract_datadome(scanner.tgtg_client) == "oldcookie"
    mock_save.assert_not_called()
    assert (tmp_path / "datadome_in").exists()
    assert "rejected" in caplog.text


def test_recover_captcha_rearms_challenge_when_probe_clears_it(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    challenge = _pending_captcha()
    challenge.warned = True
    scanner.tgtg_client.captcha = challenge
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="newcookie")

    def probe():
        scanner.tgtg_client.captcha = None  # a 2xx mid-probe (paged fetch) clears it
        raise TgtgAPIError(429, b"rate limited")

    mocker.patch.object(scanner.tgtg_client, "get_favorites", side_effect=probe)
    mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    assert scanner.tgtg_client.captcha is challenge
    assert scanner.tgtg_client.captcha.warned


def test_recover_captcha_no_candidates_logs_once_per_cycle(scanner: Scanner, tmp_path: Path, mocker: MockerFixture, caplog):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value=None)
    mocker.patch("tgtg_scanner.scanner.read_inbox_datadome", return_value=None)
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites")
    mock_apply = mocker.patch.object(scanner.tgtg_client, "apply_datadome")

    with caplog.at_level(logging.DEBUG, logger="tgtg"):
        scanner._recover_captcha()
        scanner._recover_captcha()

    mock_favorites.assert_not_called()
    mock_apply.assert_not_called()
    assert scanner.tgtg_client.captcha is not None
    waiting = [record for record in caplog.records if "no fresh datadome cookie" in record.getMessage()]
    assert len(waiting) == 2


def test_recover_captcha_browser_off_skips_browser_reader(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    scanner.config.tgtg.captcha_browser = "off"
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_browser = mocker.patch("tgtg_scanner.scanner.read_browser_datadome")
    mock_inbox = mocker.patch("tgtg_scanner.scanner.read_inbox_datadome", return_value=None)

    scanner._recover_captcha()

    mock_browser.assert_not_called()
    mock_inbox.assert_called_once_with(Path(tmp_path, "datadome_in"))


def test_recover_captcha_skips_probe_for_unchanged_cookie(scanner: Scanner, tmp_path: Path, mocker: MockerFixture, caplog):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.captcha = _pending_captcha()
    scanner.tgtg_client.apply_datadome("samecookie")
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="samecookie")
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites")
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    with caplog.at_level(logging.DEBUG, logger="tgtg"):
        scanner._recover_captcha()

    mock_favorites.assert_not_called()
    mock_save.assert_not_called()
    assert scanner.tgtg_client.captcha is not None
    assert "not fresh - still waiting" in caplog.text


def test_recover_captcha_swallows_unexpected_exceptions(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", side_effect=RuntimeError("boom"))

    scanner._recover_captcha()  # must not raise

    assert scanner.tgtg_client.captcha is not None


def test_recover_captcha_noop_without_challenge(scanner: Scanner, mocker: MockerFixture):
    scanner.tgtg_client.captcha = None
    mock_favorites = mocker.patch.object(scanner.tgtg_client, "get_favorites")

    scanner._recover_captcha()

    mock_favorites.assert_not_called()


def test_recover_captcha_retries_after_transient_probe_failure(scanner: Scanner, tmp_path: Path, mocker: MockerFixture, caplog):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.captcha = _pending_captcha()
    scanner.tgtg_client.apply_datadome("oldcookie")
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="newcookie")
    probe = mocker.patch.object(scanner.tgtg_client, "get_favorites", side_effect=TgtgAPIError(429, b"rate limited"))
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    with caplog.at_level(logging.WARNING, logger="tgtg"):
        scanner._recover_captcha()  # cycle 1: candidate applied, probe fails, must roll back
        assert scanner.tgtg_client.captcha is not None
        assert scanner.tgtg_client.cookie == "datadome=oldcookie"
        assert extract_datadome(scanner.tgtg_client) == "oldcookie"
        scanner._recover_captcha()  # cycle 2: same candidate must be re-probed, not short-circuited

    assert probe.call_count == 2
    mock_save.assert_not_called()
    assert "transient" in caplog.text
    assert "rejected" not in caplog.text


def test_recover_captcha_403_rejects_rolls_back_and_reprobes(scanner: Scanner, tmp_path: Path, mocker: MockerFixture, caplog):
    scanner.config.token_path = str(tmp_path)
    scanner.tgtg_client.session.cookies.clear()
    scanner.tgtg_client.cookie = None
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="newcookie")
    probe = mocker.patch.object(scanner.tgtg_client, "get_favorites", side_effect=TgtgAPIError(403, b"captcha challenge"))
    mock_save = mocker.patch.object(scanner, "_save_tokens")

    with caplog.at_level(logging.WARNING, logger="tgtg"):
        scanner._recover_captcha()  # cycle 1: no previous cookie - candidate must be fully cleared
        assert scanner.tgtg_client.captcha is not None
        assert scanner.tgtg_client.cookie is None
        assert extract_datadome(scanner.tgtg_client) == ""
        scanner._recover_captcha()  # cycle 2: candidate still fresh vs session - re-probe

    assert probe.call_count == 2
    mock_save.assert_not_called()
    assert "rejected" in caplog.text
    assert "transient" not in caplog.text


def test_recover_captcha_no_token_path_with_browser_off_stays_pending(scanner: Scanner, mocker: MockerFixture):
    scanner.config.token_path = None
    scanner.config.tgtg.captcha_browser = "off"
    scanner.tgtg_client.captcha = _pending_captcha()
    mock_browser = mocker.patch("tgtg_scanner.scanner.read_browser_datadome")
    mock_inbox = mocker.patch("tgtg_scanner.scanner.read_inbox_datadome")
    mock_probe = mocker.patch.object(scanner.tgtg_client, "get_favorites")

    scanner._recover_captcha()  # must not raise

    mock_browser.assert_not_called()
    mock_inbox.assert_not_called()
    mock_probe.assert_not_called()
    assert scanner.tgtg_client.captcha is not None


def test_recover_captcha_browser_success_keeps_inbox_file(scanner: Scanner, tmp_path: Path, mocker: MockerFixture):
    scanner.config.token_path = str(tmp_path)
    (tmp_path / "datadome_in").write_text("inboxcookie", encoding="utf-8")
    scanner.tgtg_client.captcha = _pending_captcha()
    mocker.patch("tgtg_scanner.scanner.read_browser_datadome", return_value="browsercookie")
    mocker.patch.object(scanner.tgtg_client, "get_favorites", return_value=[])
    mocker.patch.object(scanner, "_save_tokens")

    scanner._recover_captcha()

    assert scanner.tgtg_client.captcha is None
    assert scanner.tgtg_client.cookie == "datadome=browsercookie"
    assert (tmp_path / "datadome_in").exists()
