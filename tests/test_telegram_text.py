from tgtg_scanner.models.reservations import UNPAID_WARNING, Reservation
from tgtg_scanner.notifiers.telegram import Telegram


def test_reserved_text_default():
    r = Reservation("1", 1, "Bakery", status="reserved")
    assert Telegram._reservation_text(r) == "Bakery is reserved for 5 minutes"


def test_reserved_text_no_status():
    r = Reservation("1", 1, "Bakery")
    assert Telegram._reservation_text(r) == "Bakery is reserved for 5 minutes"


def test_confirmed_text():
    r = Reservation("1", 1, "Bakery", status="CONFIRMED")
    assert "purchased" in Telegram._reservation_text(r)


def test_unpaid_warning_text():
    r = Reservation("1", 1, "Bakery", status=UNPAID_WARNING)
    text = Telegram._reservation_text(r)
    assert "UNPAID" in text
    assert "app" in text


def test_terminal_text():
    r = Reservation("1", 1, "Bakery", status="CANCELED")
    assert "CANCELED" in Telegram._reservation_text(r)
