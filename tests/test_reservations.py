import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models.item import Item
from tgtg_scanner.models.reservations import UNPAID_WARNING, Order, Reservation, Reservations
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
