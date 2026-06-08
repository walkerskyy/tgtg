from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tgtg_scanner.models.item import Item
from tgtg_scanner.models.reservations import Order, Reservation, Reservations


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


def test_make_orders_does_not_remove_from_query(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    reservations.reserve("123", "Test Item")
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock)
    assert len(reservations.active_orders) == 1
    assert len(reservations.reservation_query) == 1
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


def test_update_active_orders(reservations: Reservations):
    order = Order("1", "123", 1, "Test Item")
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    reservations.active_orders = {order.id: order}
    reservations.update_active_orders()
    assert len(reservations.active_orders) == 1
    reservations.client.get_order_status.return_value = {"state": "CANELLED"}  # type: ignore[attr-defined]
    reservations.update_active_orders()
    assert len(reservations.active_orders) == 0


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
