import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock.plugin import MockerFixture

from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models.item import Item
from tgtg_scanner.models.reservations import (
    PAYMENT_ACTION_REQUIRED,
    PAYMENT_FAILED,
    PAYMENT_RETRY_PENDING,
    UNPAID_WARNING,
    Order,
    Reservation,
    Reservations,
    _extract_price,
    _order_from_dict,
)
from tgtg_scanner.models.schedule import Schedule


@pytest.fixture(scope="function")
def reservations():
    mock_client = MagicMock()
    return Reservations(mock_client)


def test_reserve(reservations: Reservations):
    reservations.reserve("123", "Test Item")
    assert len(reservations.reservation_query) == 1


def test_is_queued(reservations: Reservations):
    assert not reservations.is_queued("123")
    reservations.reserve("123", "Test Item")
    assert reservations.is_queued("123")
    assert not reservations.is_queued("456")


def test_remove(reservations: Reservations):
    reservations.reserve("123", "Test Item")
    reservations.reserve("456", "Other Item")
    assert reservations.remove("123") is True
    assert len(reservations.reservation_query) == 1
    assert reservations.reservation_query[0].item_id == "456"
    assert reservations.remove("999") is False
    assert len(reservations.reservation_query) == 1


def test_make_orders_removes_from_query_on_success(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    reservations.reserve("123", "Test Item")
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock)
    assert len(reservations.active_orders) == 1
    assert len(reservations.reservation_query) == 0
    callback_mock.assert_called_once_with(Reservation("123", 1, "Test Item"))


def test_make_orders_skips_already_reserved(reservations: Reservations, tgtg_item: dict):
    """Items with reservation_status set (handled by _attempt_reservation) are skipped."""
    callback_mock = MagicMock()
    item = Item(tgtg_item)
    item.reservation_status = "reserved"
    reservations.reserve(item.item_id, "Test Item")
    reservations.make_orders({item.item_id: item}, callback_mock)
    assert callback_mock.call_count == 0


def test_persistence(tmp_path: Path):
    mock_client = MagicMock()
    r1 = Reservations(mock_client, str(tmp_path))
    r1.reserve("123", "Test Item")
    r1.reserve("456", "Other Item")

    r2 = Reservations(mock_client, str(tmp_path))
    assert len(r2.reservation_query) == 2
    assert r2.is_queued("123")
    assert r2.is_queued("456")

    r2.remove("123")
    r3 = Reservations(mock_client, str(tmp_path))
    assert len(r3.reservation_query) == 1
    assert r3.reservation_query[0].item_id == "456"


def test_no_persistence_when_no_dir(reservations: Reservations):
    """When persist_dir is None, no file is written."""
    reservations.reserve("123", "Test Item")
    assert reservations._persist_path is None


def test_cancel_order(reservations: Reservations):
    order = Order("1", "123", 1, "Test Item")
    reservations.active_orders = {order.id: order}
    reservations.cancel_order(order.id)


def test_register_order(reservations: Reservations):
    reservations.register_order("ord_1", "item_123", 1, "Test Bag")
    assert "ord_1" in reservations.active_orders
    order = reservations.active_orders["ord_1"]
    assert order.id == "ord_1"
    assert order.item_id == "item_123"
    assert order.amount == 1
    assert order.display_name == "Test Bag"


def test_cancel_all_orders(reservations: Reservations):
    order1 = Order("1", "123", 1, "Test Item 1")
    order2 = Order("2", "123", 2, "Test Item 2")
    reservations.active_orders = {order1.id: order1, order2.id: order2}
    reservations.cancel_all_orders()


def test_cancel_all_orders_skips_confirmed(reservations: Reservations):
    reservations.active_orders["paid"] = Order("paid", "1", 1, "Paid", status="CONFIRMED", created_at=int(time.time()))
    reservations.active_orders["unpaid"] = Order("unpaid", "2", 1, "Unpaid", status="RESERVED", created_at=int(time.time()))
    cancelled, skipped = reservations.cancel_all_orders()
    reservations.client.abort_order.assert_called_once_with("unpaid")  # type: ignore[attr-defined]
    assert "paid" in reservations.active_orders
    assert "unpaid" not in reservations.active_orders
    assert (cancelled, skipped) == (1, 1)


def test_cancel_order_refuses_confirmed(reservations: Reservations):
    reservations.active_orders["paid"] = Order("paid", "1", 1, "Paid", status="CONFIRMED", created_at=int(time.time()))
    assert reservations.cancel_order("paid") is False
    reservations.client.abort_order.assert_not_called()  # type: ignore[attr-defined]
    assert "paid" in reservations.active_orders


def test_reservation_with_schedule_and_amount_roundtrip(tmp_path: Path):
    mock_client = MagicMock()
    r1 = Reservations(mock_client, str(tmp_path))
    schedule = Schedule.parse({"days": [1, 2], "windows": ["16:00-20:00"]})
    r1.reservation_query.append(Reservation("123", 2, "Test Item", schedule=schedule))
    r1._save()

    r2 = Reservations(mock_client, str(tmp_path))
    loaded = r2.reservation_query[0]
    assert loaded.amount == 2
    assert loaded.schedule is not None
    assert loaded.schedule.days == [1, 2]
    assert loaded.schedule.windows == ["16:00-20:00"]


def test_order_with_status_created_at_roundtrip(tmp_path: Path):
    mock_client = MagicMock()
    r1 = Reservations(mock_client, str(tmp_path))
    r1.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=123)
    r1._save()

    r2 = Reservations(mock_client, str(tmp_path))
    o = r2.active_orders["o1"]
    assert o.status == "RESERVED"
    assert o.created_at == 123


def test_legacy_array_load_defaults(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text('[{"item_id": "1", "amount": 1, "display_name": "A"}]')
    r = Reservations(mock_client, str(tmp_path))
    assert len(r.reservation_query) == 1
    assert r.reservation_query[0].schedule is None
    assert r.reservation_query[0].amount == 1


def test_entry_missing_amount_defaults_to_1(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text(
        '{"reservation_query": [{"item_id": "1", "display_name": "A"}], "active_orders": []}'
    )
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query[0].amount == 1


def test_malformed_schedule_treated_as_no_schedule(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text(
        '{"reservation_query": [{"item_id": "1", "amount": 1, "display_name": "A", '
        '"schedule": {"windows": ["bogus"]}}], "active_orders": []}'
    )
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query[0].schedule is None


def test_corrupt_reservations_file_quarantined(tmp_path: Path):
    mock_client = MagicMock()
    path = tmp_path / "reservations.json"
    path.write_text("{broken")
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query == []
    assert list(tmp_path.glob("reservations.json.corrupt-*"))


def test_get_reservation(reservations: Reservations):
    reservations.reserve("123", "Test Item")
    got = reservations.get_reservation("123")
    assert got is not None
    assert got.item_id == "123"
    assert reservations.get_reservation("999") is None


def test_register_order_sets_status_and_created_at(reservations: Reservations):
    reservations.register_order("o1", "123", 1, "Test")
    o = reservations.active_orders["o1"]
    assert o.status == "RESERVED"
    assert isinstance(o.created_at, int)


def test_make_order_creates_order_with_status(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    reservations.client.create_order.return_value = {"id": "ord_1"}  # type: ignore[attr-defined]
    reservations.reserve("123", "Test Item")
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock)
    o = reservations.active_orders["ord_1"]
    assert o.status == "RESERVED"
    assert isinstance(o.created_at, int)


def test_make_orders_respects_schedule(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    schedule = Schedule.parse({"days": [0], "windows": ["00:00-00:01"]})
    assert schedule is not None
    schedule.is_now = lambda now=None: False  # type: ignore[method-assign]
    reservations.reservation_query = [Reservation("123", 1, "Test", schedule=schedule)]
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock)
    reservations.client.create_order.assert_not_called()  # type: ignore[attr-defined]
    callback_mock.assert_not_called()
    assert len(reservations.reservation_query) == 1  # stays queued


def test_null_arrays_load_as_empty(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text('{"reservation_query": null, "active_orders": null}')
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query == []
    assert r.active_orders == {}
    assert not list(tmp_path.glob("*.corrupt-*"))


def test_unknown_extra_keys_ignored(tmp_path: Path):
    mock_client = MagicMock()
    (tmp_path / "reservations.json").write_text(
        '{"reservation_query": [{"item_id": "1", "amount": 1, "display_name": "A", "future_field": 42, "status": "queued"}], '
        '"active_orders": [{"id": "o1", "item_id": "1", "amount": 1, "display_name": "A", "extra": true}]}'
    )
    r = Reservations(mock_client, str(tmp_path))
    assert r.reservation_query[0].item_id == "1"
    assert r.reservation_query[0].status == "queued"
    assert r.active_orders["o1"].status is None


def test_save_leaves_no_tmp_file(tmp_path: Path):
    mock_client = MagicMock()
    r = Reservations(mock_client, str(tmp_path))
    r.reserve("123", "Test Item")
    assert not list(tmp_path.glob("*.tmp"))
    assert (tmp_path / "reservations.json").is_file()


def test_process_confirmed_order_notifies_and_keeps(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "CONFIRMED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert [o.id for o in notified] == ["o1"]
    assert notified[0].status == "CONFIRMED"
    assert "o1" in reservations.active_orders  # kept in ledger


def test_process_reserved_order_no_notification_before_deadline(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert reservations.active_orders["o1"].status == "RESERVED"


def test_process_unpaid_warning_fires_once_past_deadline(reservations: Reservations):
    old = int(time.time()) - 300
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=old)
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert len(notified) == 1
    assert notified[0].status == UNPAID_WARNING
    assert reservations.active_orders["o1"].status == UNPAID_WARNING
    # second call: no repeat warning
    notified2 = reservations.process_active_orders()
    assert notified2 == []


def test_process_terminal_state_removes_and_notifies(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "CANCELED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert len(notified) == 1
    assert notified[0].status == "CANCELED"
    assert "o1" not in reservations.active_orders


def test_process_aborted_state_is_terminal(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "ABORTED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert len(notified) == 1
    assert notified[0].status == "ABORTED"
    assert "o1" not in reservations.active_orders


def test_process_order_entity_deleted_removes_as_deleted(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.side_effect = TgtgAPIError(410, b'{"errors":[{"code":"ENTITY_DELETED"}]}')  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert len(notified) == 1
    assert notified[0].status == "DELETED"
    assert "o1" not in reservations.active_orders
    notified2 = reservations.process_active_orders()
    assert notified2 == []


def test_process_status_error_skips_order(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.side_effect = RuntimeError("boom")  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert "o1" in reservations.active_orders


def test_confirmed_order_pruned_when_leaves_active(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="CONFIRMED", created_at=int(time.time()))
    reservations.client.get_active.return_value = {"orders": []}  # type: ignore[attr-defined]
    reservations.process_active_orders()
    assert "o1" not in reservations.active_orders


def test_confirmed_order_kept_while_still_active(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="CONFIRMED", created_at=int(time.time()))
    reservations.client.get_active.return_value = {"orders": [{"id": "o1"}]}  # type: ignore[attr-defined]
    reservations.process_active_orders()
    assert "o1" in reservations.active_orders


def test_unpaid_warning_then_late_confirmation(reservations: Reservations):
    old = int(time.time()) - 300
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=old)
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    first = reservations.process_active_orders()
    assert [o.status for o in first] == [UNPAID_WARNING]

    reservations.client.get_order_status.return_value = {"state": "CONFIRMED"}  # type: ignore[attr-defined]
    second = reservations.process_active_orders()
    assert [o.status for o in second] == ["CONFIRMED"]
    assert reservations.active_orders["o1"].status == "CONFIRMED"
    third = reservations.process_active_orders()
    assert third == []  # no repeat


def test_confirmed_order_then_terminal_removes(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="CONFIRMED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "REFUNDED"}  # type: ignore[attr-defined]
    reservations.client.get_active.return_value = {"orders": [{"id": "o1"}]}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert [o.status for o in notified] == ["REFUNDED"]
    assert "o1" not in reservations.active_orders


def test_unknown_state_kept_in_ledger(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "SOME_FUTURE_STATE"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert "o1" in reservations.active_orders
    assert reservations.active_orders["o1"].status == "RESERVED"


def test_process_payment_initiated_updates_status_silently(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="RESERVED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []  # no notification, just a status update
    assert reservations.active_orders["o1"].status == "PAYMENT_INITIATED"
    assert "o1" in reservations.active_orders


def test_process_paid_notifies_once_and_keeps(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "PAID"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert [o.id for o in notified] == ["o1"]
    assert notified[0].status == "PAID"
    assert "o1" in reservations.active_orders  # kept until collected/cancelled
    # no repeat warning on subsequent polls
    notified2 = reservations.process_active_orders()
    assert notified2 == []
    assert reservations.active_orders["o1"].status == "PAID"


def test_process_paid_order_then_cancelled_removes(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="PAID", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "CANCELLED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert [o.status for o in notified] == ["CANCELLED"]
    assert "o1" not in reservations.active_orders


def test_process_cancelled_double_l_is_terminal(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="PAID", created_at=int(time.time()))
    reservations.client.get_order_status.return_value = {"state": "CANCELLED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert len(notified) == 1
    assert "o1" not in reservations.active_orders
    # no repeat warnings after removal
    notified2 = reservations.process_active_orders()
    assert notified2 == []


def test_make_orders_sold_out_calls_callback(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    sold_out_mock = MagicMock()
    reservations.client.create_order.side_effect = TgtgAPIError(400, b"Item is sold out")  # type: ignore[attr-defined]
    reservations.reserve("123", "Test Item")
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock, on_sold_out=sold_out_mock)
    sold_out_mock.assert_called_once_with("123")
    callback_mock.assert_not_called()
    assert len(reservations.reservation_query) == 1  # stays queued for retry


def test_make_orders_non_sold_out_error_no_callback(reservations: Reservations, tgtg_item: dict):
    sold_out_mock = MagicMock()
    reservations.client.create_order.side_effect = TgtgAPIError(403, b"captcha challenge")  # type: ignore[attr-defined]
    reservations.reserve("123", "Test Item")
    reservations.make_orders({"123": Item(tgtg_item)}, MagicMock(), on_sold_out=sold_out_mock)
    sold_out_mock.assert_not_called()


def test_pay_probe_runs_after_order_created(tgtg_item: dict):
    client = MagicMock()
    client.create_order.return_value = {"id": "o1"}
    client.get_payment_methods.return_value = {
        "payment_methods_state": "SUCCESS",
        "payment_methods": [
            {"payment_type": "PAYPAL", "payment_provider": "ADYEN", "adyen_api_payload": '{"type":"paypal"}'},
        ],
    }
    client.create_payment.return_value = {"payments": [{"payment_id": "p1"}]}
    client.pay_order.return_value = {"payment_id": "p1"}
    client.get_payment_status.return_value = {"state": "PENDING"}
    r = Reservations(client)
    r.reserve("123", "Test Item")
    r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    assert "o1" in r.active_orders
    assert len(r.reservation_query) == 0
    client.get_payment_methods.assert_any_call(item_id="123")  # type: ignore[attr-defined]
    client.create_payment.assert_called_once_with("o1")  # type: ignore[attr-defined]
    client.pay_order.assert_called_once()  # type: ignore[attr-defined]
    assert client.pay_order.call_args[0][0] == "o1"  # type: ignore[attr-defined]
    body = client.pay_order.call_args[0][1]  # type: ignore[attr-defined]
    assert body["authorizations"][0]["authorization_payload"]["payload"] == '{"type":"paypal"}'
    client.get_payment_status.assert_called_once_with("p1")  # type: ignore[attr-defined]


def test_pay_probe_failure_does_not_break_reservation(tgtg_item: dict):
    client = MagicMock()
    client.create_order.return_value = {"id": "o2"}
    client.get_payment_methods.side_effect = RuntimeError("probe boom")
    client.get_vouchers.side_effect = RuntimeError("probe boom")
    client.pay_order.side_effect = RuntimeError("probe boom")
    r = Reservations(client)
    r.reserve("123", "Test Item")
    callback_mock = MagicMock()
    r.make_orders({"123": Item(tgtg_item)}, callback_mock)
    client.create_payment.assert_not_called()  # type: ignore[attr-defined]
    assert "o2" in r.active_orders
    assert len(r.reservation_query) == 0
    callback_mock.assert_called_once()


def test_pay_probe_runs_on_register_order():
    """register_order is the shared hook for every reserve path (scanner + queue)."""
    client = MagicMock()
    client.get_payment_methods.return_value = {
        "payment_methods_state": "SUCCESS",
        "payment_methods": [
            {"payment_type": "PAYPAL", "payment_provider": "ADYEN", "adyen_api_payload": '{"type":"paypal"}'},
            {"payment_type": "CREDITCARD", "payment_provider": "ADYEN", "adyen_api_payload": '{"type":"cardcfg"}'},
        ],
    }
    client.create_payment.return_value = {"payments": [{"payment_id": "p1"}]}
    client.pay_order.return_value = {"payment_id": "p1"}
    client.get_payment_status.return_value = {"state": "PENDING"}
    r = Reservations(client)
    r.register_order("o9", "123", 1, "Test Bag")
    client.create_payment.assert_called_once_with("o9")  # type: ignore[attr-defined]
    client.pay_order.assert_called_once()  # type: ignore[attr-defined]
    assert client.pay_order.call_args[0][0] == "o9"  # type: ignore[attr-defined]
    body = client.pay_order.call_args[0][1]  # type: ignore[attr-defined]
    assert "authorizations" in body and isinstance(body["authorizations"], list)
    payload = body["authorizations"][0]["authorization_payload"]
    assert payload["payment_type"] == "CREDITCARD"
    assert payload["payload"] == '{"type":"cardcfg"}'
    client.get_payment_status.assert_called_once_with("p1")  # type: ignore[attr-defined]


def test_pay_probe_skips_pay_without_adyen_payload():
    client = MagicMock()
    client.get_payment_methods.return_value = {"payment_methods": []}
    r = Reservations(client)
    r.register_order("o10", "123", 1, "Test Bag")
    client.create_payment.assert_not_called()  # type: ignore[attr-defined]
    client.pay_order.assert_not_called()  # type: ignore[attr-defined]


def test_extract_price_from_order_line():
    price = {"currency": "AUD", "decimals": 2, "minor_units": 850}
    assert _extract_price({"order_line": {"total_price": price}}) == price
    assert _extract_price({"order_line": [{"total_price": price}]}) == price
    assert _extract_price({"total_price": price}) == price
    assert _extract_price({"order_line": None}) is None
    assert _extract_price({}) is None


def test_create_order_price_from_order_line(tgtg_item: dict):
    price = {"currency": "AUD", "decimals": 2, "minor_units": 850}
    client = MagicMock()
    client.create_order.return_value = {"id": "o31", "order_line": {"total_price": price}}
    r = Reservations(client)
    r.reserve("123", "Test Item")
    r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    assert r.active_orders["o31"].price == price


def test_probe_fetches_order_detail_when_create_lacks_price(tgtg_item: dict):
    """create_order omits totals; fall back to order/v8/{id} before paying."""
    price = {"currency": "AUD", "decimals": 2, "minor_units": 850}
    client = MagicMock()
    client.create_order.return_value = {"id": "o32", "order_line": None}
    client.get_order_detail.return_value = {"total_price": price}
    client.get_payment_methods.return_value = {
        "payment_methods_state": "SUCCESS",
        "payment_methods": [
            {"payment_type": "CREDITCARD", "payment_provider": "ADYEN", "adyen_api_payload": '{"type":"cardcfg"}'},
        ],
    }
    client.create_payment.return_value = {"payments": []}
    client.pay_order.return_value = {"payments": []}
    client.get_payment_status.return_value = {"state": "PENDING"}
    r = Reservations(client)
    r.reserve("123", "Test Item")
    r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    client.get_order_detail.assert_called_once_with("o32")  # type: ignore[attr-defined]
    assert r.active_orders["o32"].price == price
    body = client.pay_order.call_args[0][1]  # type: ignore[attr-defined]
    assert body["authorizations"][0]["amount"] == price


def test_create_order_stores_total_price_and_pay_includes_amount(tgtg_item: dict):
    """Authorization.amount must carry the order's total_price (app always passes Price)."""
    client = MagicMock()
    price = {"currency": "AUD", "decimals": 2, "minor_units": 850}
    client.create_order.return_value = {"id": "o30", "total_price": price}
    client.get_payment_methods.return_value = {
        "payment_methods_state": "SUCCESS",
        "payment_methods": [
            {"payment_type": "CREDITCARD", "payment_provider": "ADYEN", "adyen_api_payload": '{"type":"cardcfg"}'},
        ],
    }
    client.create_payment.return_value = {"payments": [{"payment_id": "p1"}]}
    client.pay_order.return_value = {"payments": [{"payment_id": "p1"}]}
    client.get_payment_status.return_value = {"state": "PENDING"}
    r = Reservations(client)
    r.reserve("123", "Test Item")
    r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    assert r.active_orders["o30"].price == price
    body = client.pay_order.call_args[0][1]  # type: ignore[attr-defined]
    assert body["authorizations"][0]["amount"] == price
    assert body["authorizations"][0]["authorization_payload"]["payload"] == '{"type":"cardcfg"}'


def test_pay_probe_retries_pay_once(mocker):
    client = MagicMock()
    client.get_payment_methods.return_value = {
        "payment_methods": [{"payment_type": "CREDITCARD", "payment_provider": "ADYEN", "adyen_api_payload": "{}"}],
    }
    client.create_payment.return_value = {"payments": [{"payment_id": "p2"}]}
    client.pay_order.side_effect = [RuntimeError("too fast"), {"payment_id": "p2"}]
    client.get_payment_status.return_value = {"state": "PENDING"}
    mocker.patch("time.sleep")
    r = Reservations(client)
    r.register_order("o11", "123", 1, "Test Bag")
    assert client.pay_order.call_count == 2
    client.get_payment_status.assert_called_once_with("p2")  # type: ignore[attr-defined]


def test_pay_probe_saved_card_token_when_item_methods_fail(tgtg_item: dict):
    """Item methods 500 (bag already taken) must not block the saved-card token path."""
    client = MagicMock()
    saved = {
        "consumer_saved_psp_payment_methods": [
            {
                "payment_type": "CREDITCARD",
                "identifier": "TESTCARDIDENT01",
                "display_value": "•••• 4242",
                "status": "ACTIVE",
                "brand": "VISA",
                "payment_provider": "ADYEN",
            }
        ]
    }
    client.get_payment_methods.side_effect = [saved, RuntimeError("(500, ILLEGAL_STATE)")]
    client.create_payment.return_value = {"payments": [{"payment_id": "p1"}]}
    client.payment_biometrics.return_value = {
        "biometrics_protected_secret": "sec",
        "saved_payment_method_token": "tok",
        "payment_id": "p1",
    }
    client.pay_order.return_value = {"payments": [{"payment_id": "p1"}]}
    client.get_payment_status.return_value = {"state": "PENDING"}
    price = {"currency": "AUD", "decimals": 2, "minor_units": 850}
    r = Reservations(client)
    r.register_order("o20", "123", 1, "Test Bag", price=price)
    client.create_payment.assert_called_once_with("o20")  # type: ignore[attr-defined]
    client.payment_biometrics.assert_called_once_with("p1")  # type: ignore[attr-defined]
    client.pay_order.assert_called_once()  # type: ignore[attr-defined]
    body = client.pay_order.call_args[0][1]  # type: ignore[attr-defined]
    assert body["authorizations"][0]["amount"] == price
    payload = body["authorizations"][0]["authorization_payload"]
    assert json.loads(payload["payload"]) == {
        "type": "scheme",
        "storedPaymentMethodId": "TESTCARDIDENT01",
        "threeDS2SdkVersion": "2.2.27",
    }
    assert "payment_method_token" not in payload
    assert payload["biometrics_protected_secret"] == "sec"
    assert payload["payment_type"] == "CREDITCARD"
    assert payload["type"] == "adyenAuthorizationPayload"
    client.get_payment_status.assert_called_once_with("p1")  # type: ignore[attr-defined]


def test_prefetched_methods_used_by_probe_without_refetch():
    """Item methods must be fetched BEFORE the order exists; probe must use the cache."""
    client = MagicMock()
    client.get_payment_methods.return_value = {
        "payment_methods_state": "SUCCESS",
        "payment_methods": [
            {"payment_type": "CREDITCARD", "payment_provider": "ADYEN", "adyen_api_payload": '{"type":"cardcfg"}'},
        ],
    }
    client.create_payment.return_value = {"payments": [{"payment_id": "p1"}]}
    client.pay_order.return_value = {"payments": [{"payment_id": "p1"}]}
    client.get_payment_status.return_value = {"state": "PENDING"}
    r = Reservations(client)
    r.prefetch_probe_methods("123")
    r.register_order("o30", "123", 1, "Test Bag")
    # exactly one item-scoped fetch (prefetch); the other call is the saved-cards lookup
    assert client.get_payment_methods.call_count == 2  # type: ignore[attr-defined]
    assert client.get_payment_methods.call_args_list[0].kwargs == {"item_id": "123"}  # type: ignore[attr-defined]
    body = client.pay_order.call_args[0][1]  # type: ignore[attr-defined]
    assert body["authorizations"][0]["authorization_payload"]["payload"] == '{"type":"cardcfg"}'


def test_prefetch_runs_by_default():
    client = MagicMock()
    client.get_payment_methods.return_value = {"payment_methods_state": "SUCCESS", "payment_methods": []}
    r = Reservations(client)
    r.prefetch_probe_methods("123")
    client.get_payment_methods.assert_called_once_with(item_id="123")  # type: ignore[attr-defined]
    # second call is cached
    r.prefetch_probe_methods("123")
    assert client.get_payment_methods.call_count == 1  # type: ignore[attr-defined]


def test_order_from_dict_keeps_payment_id():
    order = _order_from_dict({"id": "o1", "payment_id": "p9", "created_at": 5})
    assert order.payment_id == "p9"


def test_order_retry_fields_roundtrip():
    order = Order(
        "o1",
        "123",
        1,
        "Test",
        status=PAYMENT_RETRY_PENDING,
        created_at=1,
        payment_id="p1",
        pay_attempts=2,
        pay_retry_at=1760000000,
        pay_alerted=True,
    )
    restored = _order_from_dict(asdict(order))
    assert restored.pay_attempts == 2
    assert restored.pay_retry_at == 1760000000
    assert restored.pay_alerted is True
    legacy = _order_from_dict({"id": "o2", "item_id": "123", "amount": 1, "display_name": "T", "status": "RESERVED"})
    assert legacy.pay_attempts == 0
    assert legacy.pay_retry_at is None
    assert legacy.pay_alerted is False


def _probe_client() -> MagicMock:
    client = MagicMock()
    client.create_order.return_value = {"id": "o1"}
    client.get_payment_methods.return_value = {
        "payment_methods_state": "SUCCESS",
        "payment_methods": [
            {"payment_type": "PAYPAL", "payment_provider": "ADYEN", "adyen_api_payload": '{"type":"paypal"}'},
        ],
    }
    client.create_payment.return_value = {"payments": [{"payment_id": "p1"}]}
    client.pay_order.return_value = {"payment_id": "p1"}
    return client


def test_probe_polls_payment_status_until_settled(tgtg_item: dict, mocker: MockerFixture):
    mocker.patch("time.sleep")
    client = _probe_client()
    client.get_payment_status.side_effect = [
        {"state": "AUTHORIZATION_INITIATED"},
        {"state": "CAPTURED"},
    ]
    r = Reservations(client)
    r.reserve("123", "Test Item")
    r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    assert client.get_payment_status.call_count == 2
    assert r.active_orders["o1"].payment_id == "p1"


def test_probe_stops_polling_on_payment_failure(tgtg_item: dict, mocker: MockerFixture):
    mocker.patch("time.sleep")
    client = _probe_client()
    client.get_payment_status.side_effect = [{"state": "FAILED"}]
    r = Reservations(client)
    r.reserve("123", "Test Item")
    r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    assert client.get_payment_status.call_count == 1
    assert r.active_orders["o1"].payment_id == "p1"  # kept so the watcher can keep tracking


def test_probe_payment_returns_bool(mocker: MockerFixture):
    mocker.patch("time.sleep")
    client = _probe_client()
    client.get_payment_status.side_effect = [{"state": "FAILED", "failure_reason": "risk_declined"}]
    r = Reservations(client)
    r.active_orders["o1"] = Order(
        "o1",
        "123",
        1,
        "Test",
        status="RESERVED",
        created_at=int(time.time()),
        price={"total_price": {"minor": 500, "code": "EUR"}},
    )
    # pay accepted (True) even though the probe's own poll saw FAILED — the watcher handles that next cycle
    assert r._probe_payment("o1", "123", "Test") is True
    client.pay_order.side_effect = RuntimeError("declined")
    assert r._probe_payment("o1", "123", "Test") is False


def test_process_payment_failure_first_signal_is_silent(reservations: Reservations):
    order = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1")
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "FAILED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None
    assert "o1" in reservations.active_orders  # reservation still held until expiry


def test_payment_retry_pending_status_survives_next_cycle(reservations: Reservations):
    order = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1")
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "FAILED"}  # type: ignore[attr-defined]
    assert reservations.process_active_orders() == []
    assert order.status == PAYMENT_RETRY_PENDING
    first_timer = order.pay_retry_at
    assert first_timer is not None
    assert reservations.process_active_orders() == []
    assert order.status == PAYMENT_RETRY_PENDING  # not clobbered back to PAYMENT_INITIATED
    assert order.pay_retry_at == first_timer  # timer not reset by duplicate signals


def test_process_payment_action_required_first_signal_is_silent(reservations: Reservations):
    order = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1")
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "REAUTHORIZATION_REQUIRED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None
    assert "o1" in reservations.active_orders


def test_process_payment_action_required_alerts_after_fast_budget(reservations: Reservations):
    order = Order(
        "o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1", pay_attempts=2
    )
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "REAUTHORIZATION_REQUIRED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert [o.status for o in notified] == [PAYMENT_ACTION_REQUIRED]
    assert order.pay_alerted is True
    assert order.pay_retry_at is not None
    notified2 = reservations.process_active_orders()
    assert notified2 == []


def test_check_payment_logs_failure_reason(reservations: Reservations, caplog):
    order = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1")
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {  # type: ignore[attr-defined]
        "state": "FAILED",
        "failure_reason": "risk_declined",
        "payload": {"reason": "risk_declined"},
    }
    with caplog.at_level(logging.WARNING):
        reservations.process_active_orders()
    assert any("risk_declined" in record.getMessage() for record in caplog.records)


def test_probe_payment_status_logs_failure_reason(tgtg_item: dict, mocker: MockerFixture, caplog):
    mocker.patch("time.sleep")
    client = _probe_client()
    client.get_payment_status.side_effect = [{"state": "FAILED", "failure_reason": "risk_declined", "payload": {"av": "manual"}}]
    r = Reservations(client)
    r.reserve("123", "Test Item")
    with caplog.at_level(logging.WARNING):
        r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    assert any("risk_declined" in record.getMessage() for record in caplog.records)


def test_process_in_flight_payment_stays_silent(reservations: Reservations):
    reservations.active_orders["o1"] = Order(
        "o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1"
    )
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "AUTHORIZATION_INITIATED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert reservations.active_orders["o1"].status == "PAYMENT_INITIATED"


def test_process_order_reverts_to_reserved_schedules_silent_retry(reservations: Reservations):
    order = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1")
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "AUTHORIZATION_INITIATED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None
    assert "o1" in reservations.active_orders
    notified2 = reservations.process_active_orders()
    assert notified2 == []


def test_process_order_revert_alerts_after_fast_budget(reservations: Reservations):
    order = Order(
        "o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=int(time.time()), payment_id="p1", pay_attempts=2
    )
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "AUTHORIZATION_INITIATED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert [o.status for o in notified] == [PAYMENT_FAILED]
    assert order.pay_alerted is True
    assert order.pay_retry_at is not None


def test_process_payment_initiated_past_deadline_schedules_retry(reservations: Reservations):
    old = int(time.time()) - 300
    order = Order("o1", "123", 1, "Test", status="PAYMENT_INITIATED", created_at=old, payment_id="p1")
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "AUTHORIZATION_INITIATED"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None


def test_paid_order_does_not_poll_payment(reservations: Reservations):
    reservations.active_orders["o1"] = Order("o1", "123", 1, "Test", status="PAID", created_at=int(time.time()), payment_id="p1")
    reservations.client.get_order_status.return_value = {"state": "PAID"}  # type: ignore[attr-defined]
    notified = reservations.process_active_orders()
    assert notified == []
    reservations.client.get_payment_status.assert_not_called()  # type: ignore[attr-defined]


def _retry_order(**kwargs) -> Order:
    defaults: dict = dict(
        id="o1",
        item_id="123",
        amount=1,
        display_name="T",
        status="PAYMENT_INITIATED",
        created_at=int(time.time()),
        payment_id="p1",
    )
    defaults.update(kwargs)
    return Order(**defaults)


def test_payment_failure_first_signal_schedules_silent_retry(reservations: Reservations):
    order = _retry_order()
    reservations.active_orders["o1"] = order
    events: list[Order] = []
    before = int(time.time())
    changed = reservations._handle_payment_failure(order, "o1", events, "payment state FAILED (risk_declined)", PAYMENT_FAILED)
    assert changed is True
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None
    assert 1 <= order.pay_retry_at - before <= 15
    assert events == []


def test_payment_failure_second_signal_schedules_45s(reservations: Reservations):
    order = _retry_order(pay_attempts=1)
    reservations.active_orders["o1"] = order
    events: list[Order] = []
    before = int(time.time())
    changed = reservations._handle_payment_failure(order, "o1", events, "retry attempt 1 failed", PAYMENT_FAILED)
    assert changed is True
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None
    assert 30 <= order.pay_retry_at - before <= 45
    assert events == []


def test_payment_failure_exhausts_budget_and_alerts_once(reservations: Reservations):
    order = _retry_order(pay_attempts=2)
    reservations.active_orders["o1"] = order
    events: list[Order] = []
    changed = reservations._handle_payment_failure(order, "o1", events, "retry attempt 2 failed", PAYMENT_FAILED)
    assert changed is True
    assert order.status == PAYMENT_FAILED
    assert order.pay_alerted is True
    assert order.pay_retry_at is not None
    assert 50 <= order.pay_retry_at - int(time.time()) <= 60
    assert events == [order]
    events2: list[Order] = []
    assert reservations._handle_payment_failure(order, "o1", events2, "retry attempt 3 failed", PAYMENT_FAILED) is False
    assert events2 == []


def test_payment_failure_rearms_slow_retry_after_alert(reservations: Reservations):
    order = _retry_order(pay_attempts=3, pay_alerted=True, pay_retry_at=None)
    reservations.active_orders["o1"] = order
    events: list[Order] = []
    before = int(time.time())
    changed = reservations._handle_payment_failure(order, "o1", events, "retry attempt 3 failed", PAYMENT_FAILED)
    assert changed is True
    assert order.status == PAYMENT_FAILED
    assert events == []
    assert order.pay_retry_at is not None
    assert 50 <= order.pay_retry_at - before <= 61


def test_payment_failure_ignored_while_retry_scheduled(reservations: Reservations):
    order = _retry_order(status=PAYMENT_RETRY_PENDING, pay_retry_at=int(time.time()) + 100)
    reservations.active_orders["o1"] = order
    events: list[Order] = []
    assert reservations._handle_payment_failure(order, "o1", events, "duplicate signal", PAYMENT_FAILED) is False
    assert order.status == PAYMENT_RETRY_PENDING
    assert events == []


def test_payment_failure_ignored_for_paid_order(reservations: Reservations):
    order = _retry_order(status="PAID")
    reservations.active_orders["o1"] = order
    events: list[Order] = []
    assert reservations._handle_payment_failure(order, "o1", events, "late signal", PAYMENT_FAILED) is False
    assert order.status == "PAID"
    assert events == []


def _due_order(**kwargs) -> Order:
    defaults: dict = dict(
        id="o1",
        item_id="123",
        amount=1,
        display_name="T",
        status=PAYMENT_RETRY_PENDING,
        created_at=int(time.time()),
        payment_id="p1",
        pay_retry_at=int(time.time()) - 1,
    )
    defaults.update(kwargs)
    return Order(**defaults)


def test_retry_executor_runs_when_due_and_pay_succeeds(reservations: Reservations, mocker: MockerFixture):
    order = _due_order()
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "FAILED"}  # type: ignore[attr-defined]
    probe = mocker.patch.object(reservations, "_probe_payment", return_value=True)
    notified = reservations.process_active_orders()
    probe.assert_called_once_with("o1", "123", "T")
    assert order.pay_attempts == 1
    assert order.status == "PAYMENT_INITIATED"
    assert order.pay_retry_at is None
    assert notified == []


def test_retry_executor_skips_when_not_due(reservations: Reservations, mocker: MockerFixture):
    order = _due_order(pay_retry_at=int(time.time()) + 300)
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "FAILED"}  # type: ignore[attr-defined]
    probe = mocker.patch.object(reservations, "_probe_payment", return_value=True)
    reservations.process_active_orders()
    probe.assert_not_called()
    assert order.pay_attempts == 0


def test_retry_executor_cancels_timer_when_order_paid(reservations: Reservations, mocker: MockerFixture):
    order = _due_order(status="PAYMENT_RETRY_PENDING")
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAID"}  # type: ignore[attr-defined]
    probe = mocker.patch.object(reservations, "_probe_payment", return_value=True)
    notified = reservations.process_active_orders()
    probe.assert_not_called()
    assert order.pay_retry_at is None
    assert [o.status for o in notified] == ["PAID"]


def test_retry_executor_skips_terminal_order(reservations: Reservations, mocker: MockerFixture):
    order = _due_order()
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "EXPIRED"}  # type: ignore[attr-defined]
    probe = mocker.patch.object(reservations, "_probe_payment", return_value=True)
    notified = reservations.process_active_orders()
    probe.assert_not_called()
    assert "o1" not in reservations.active_orders
    assert [o.status for o in notified] == ["EXPIRED"]


def test_retry_executor_does_not_repay_settled_payment(reservations: Reservations, mocker: MockerFixture):
    order = _due_order()
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "AUTHORIZED"}  # type: ignore[attr-defined]
    probe = mocker.patch.object(reservations, "_probe_payment", return_value=True)
    reservations.process_active_orders()
    probe.assert_not_called()
    assert order.status == "PAYMENT_INITIATED"
    assert order.pay_retry_at is None


def test_retry_executor_skips_repay_when_status_check_fails(reservations: Reservations, mocker: MockerFixture):
    order = _due_order()
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.side_effect = RuntimeError("status endpoint down")  # type: ignore[attr-defined]
    probe = mocker.patch.object(reservations, "_probe_payment", return_value=True)
    notified = reservations.process_active_orders()
    probe.assert_not_called()
    assert notified == []
    assert order.pay_attempts == 0  # not incremented - attempt was skipped, not consumed
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None
    assert 1 <= order.pay_retry_at - int(time.time()) <= 16


def test_retry_executor_isolated_when_probe_raises(reservations: Reservations, mocker: MockerFixture):
    order = _due_order()
    reservations.active_orders["o1"] = order
    order2 = _due_order(id="o2", pay_retry_at=int(time.time()) - 1)
    reservations.active_orders["o2"] = order2
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "FAILED"}  # type: ignore[attr-defined]
    mocker.patch.object(reservations, "_probe_payment", side_effect=RuntimeError("probe exploded"))
    notified = reservations.process_active_orders()  # must not raise
    assert notified == []
    assert "o2" in reservations.active_orders
    assert order.pay_retry_at is not None
    assert 1 <= order.pay_retry_at - int(time.time()) <= 16
    assert order.pay_attempts == 1  # attempt consumed before the probe raised


def test_retry_executor_failure_reschedules_silently(reservations: Reservations, mocker: MockerFixture):
    order = _due_order(pay_attempts=0)
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "FAILED"}  # type: ignore[attr-defined]
    mocker.patch.object(reservations, "_probe_payment", return_value=False)
    before = int(time.time())
    notified = reservations.process_active_orders()
    assert notified == []
    assert order.pay_attempts == 1
    assert order.status == PAYMENT_RETRY_PENDING
    assert order.pay_retry_at is not None
    assert 30 <= order.pay_retry_at - before <= 46


def test_retry_executor_alerts_once_when_budget_exhausted(reservations: Reservations, mocker: MockerFixture):
    order = _due_order(pay_attempts=2, pay_alerted=False, pay_retry_at=int(time.time()) - 1)
    reservations.active_orders["o1"] = order
    reservations.client.get_order_status.return_value = {"state": "PAYMENT_INITIATED"}  # type: ignore[attr-defined]
    reservations.client.get_payment_status.return_value = {"state": "FAILED"}  # type: ignore[attr-defined]
    mocker.patch.object(reservations, "_probe_payment", return_value=False)
    notified = reservations.process_active_orders()
    assert [o.status for o in notified] == [PAYMENT_FAILED]
    assert order.pay_alerted is True
    assert order.status == PAYMENT_FAILED
    assert order.pay_attempts == 3
    assert order.pay_retry_at is not None
    order.pay_retry_at = int(time.time()) - 1  # due again next cycle
    notified2 = reservations.process_active_orders()
    assert notified2 == []
    assert "o1" in reservations.active_orders  # ledger keeps the order while retrying
    assert order.pay_retry_at is not None  # slow retry still armed
    assert order.pay_attempts == 4


def test_order_auth_url_roundtrip(tmp_path: Path):
    (tmp_path / "reservations.json").write_text(
        json.dumps(
            {
                "reservation_query": [],
                "active_orders": [
                    {
                        "id": "order-1",
                        "item_id": "item-1",
                        "amount": 1,
                        "display_name": "Bakery",
                        "status": PAYMENT_ACTION_REQUIRED,
                        "auth_url": "https://bank.example/3ds",
                        "action_alerted_at": 100,
                    }
                ],
            }
        )
    )
    res = Reservations(MagicMock(), str(tmp_path))
    order = res.active_orders["order-1"]
    assert order.auth_url == "https://bank.example/3ds"
    assert order.action_alerted_at == 100
    # save side roundtrips through asdict too
    res._save()
    reloaded = Reservations(MagicMock(), str(tmp_path)).active_orders["order-1"]
    assert reloaded.auth_url == "https://bank.example/3ds"
    assert reloaded.action_alerted_at == 100


def test_check_payment_extracts_auth_url_and_dumps_shape(reservations: Reservations, mocker: MockerFixture):
    order = _retry_order(pay_attempts=2)
    reservations.active_orders["o1"] = order
    mocker.patch.object(
        reservations.client,
        "get_payment_status",
        return_value={"state": "REAUTHORIZATION_REQUIRED", "action": {"url": "https://bank.example/go"}},
    )
    dump = mocker.patch("tgtg_scanner.models.reservations.dump_payment_shape")
    events: list[Order] = []
    changed = reservations._check_payment(order, "o1", events)
    assert changed is True
    assert order.status == PAYMENT_ACTION_REQUIRED
    assert order.auth_url == "https://bank.example/go"
    assert dump.called
    assert events and events[0].auth_url == "https://bank.example/go"
    assert order.action_alerted_at is not None
    alerted_at = order.action_alerted_at
    # second poll with the same payload: no re-timestamp, no second shape dump
    events2: list[Order] = []
    reservations._check_payment(order, "o1", events2)
    assert order.action_alerted_at == alerted_at
    assert dump.call_count == 1


def test_check_payment_no_url_leaves_auth_url_none(reservations: Reservations, mocker: MockerFixture):
    order = _retry_order(id="o2", pay_attempts=2)
    reservations.active_orders["o2"] = order
    mocker.patch.object(reservations.client, "get_payment_status", return_value={"state": "REAUTHORIZATION_REQUIRED"})
    dump = mocker.patch("tgtg_scanner.models.reservations.dump_payment_shape")
    events: list[Order] = []
    reservations._check_payment(order, "o2", events)
    assert order.status == PAYMENT_ACTION_REQUIRED
    assert order.auth_url is None
    assert dump.called


def test_probe_payment_action_required_captures_auth_url(tgtg_item: dict, mocker: MockerFixture):
    mocker.patch("time.sleep")
    client = _probe_client()
    client.get_payment_status.side_effect = [
        {"state": "REAUTHORIZATION_REQUIRED", "action": {"url": "https://bank.example/3ds"}},
    ]
    r = Reservations(client)
    r.reserve("123", "Test Item")
    r.make_orders({"123": Item(tgtg_item)}, MagicMock())
    assert r.active_orders["o1"].auth_url == "https://bank.example/3ds"


def test_action_required_realerts_every_interval(reservations: Reservations, mocker: MockerFixture):
    from tgtg_scanner.models.reservations import ACTION_RE_ALERT_SECONDS

    now = int(time.time())
    order = Order(
        id="o3",
        item_id="i1",
        amount=1,
        display_name="Bakery",
        status=PAYMENT_ACTION_REQUIRED,
        created_at=now,
        auth_url="https://bank.example/3ds",
        action_alerted_at=now - ACTION_RE_ALERT_SECONDS - 1,
    )
    reservations.active_orders["o3"] = order
    mocker.patch.object(reservations.client, "get_order_status", return_value={"state": "PAYMENT_INITIATED"})
    mocker.patch.object(
        reservations.client,
        "get_payment_status",
        return_value={"state": "REAUTHORIZATION_REQUIRED", "action": {"url": "https://bank.example/3ds"}},
    )
    mocker.patch.object(reservations.client, "get_active", return_value={"orders": []})
    events = reservations.process_active_orders()
    realerted = [o for o in events if o.id == "o3"]
    assert len(realerted) == 1
    assert realerted[0].auth_url == "https://bank.example/3ds"
    assert order.action_alerted_at is not None and order.action_alerted_at >= now
    # last alert was just refreshed: no re-alert on the next poll
    events2 = reservations.process_active_orders()
    assert [o for o in events2 if o.id == "o3"] == []
