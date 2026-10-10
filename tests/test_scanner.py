import pytest
from pytest_mock.plugin import MockerFixture

from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models import Config, Item
from tgtg_scanner.models.schedule import Schedule
from tgtg_scanner.scanner import Scanner


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


def test_fast_mode_off_without_queue_or_orders(scanner: Scanner, mocker: MockerFixture):
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=False)
    assert not scanner._fast_mode_active()


def test_fast_mode_disabled_by_config(scanner: Scanner, mocker: MockerFixture):
    scanner.config.reservation.fast_poll = False
    scanner.reservations.reserve("123", "Test")
    mocker.patch.object(scanner.drop_history, "in_predicted_window", return_value=True)
    # _fast_mode_active still reports True (it's the signal); the gate is in run()/_next_sleep_time
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
