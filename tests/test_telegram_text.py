from telegram import InlineKeyboardMarkup

from tgtg_scanner.models.reservations import PAYMENT_ACTION_REQUIRED, PAYMENT_FAILED, UNPAID_WARNING, USER_BLOCKED, Reservation
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


def test_user_blocked_text():
    r = Reservation("1", 1, "Sicily Vibes", status=USER_BLOCKED)
    text = Telegram._reservation_text(r)
    assert "BLOCKED" in text
    assert "Sicily Vibes" in text
    assert "TGTG app" in text


def test_orders_menu_hides_confirmed():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from tgtg_scanner.models.reservations import Order

    telegram = object.__new__(Telegram)
    telegram.reservations = MagicMock()
    telegram.reservations.active_orders = {
        "paid": Order("paid", "1", 1, "Paid", status="CONFIRMED", created_at=1),
        "unpaid": Order("unpaid", "2", 1, "Unpaid", status="RESERVED", created_at=1),
    }
    telegram._is_my_chat = lambda update: True  # type: ignore[method-assign]
    update = MagicMock()
    update.message.reply_text = AsyncMock()

    asyncio.run(telegram._cancel_orders_menu(update, None))

    update.message.reply_text.assert_called_once()
    _, kwargs = update.message.reply_text.call_args
    buttons = [btn for row in kwargs["reply_markup"].inline_keyboard for btn in row]
    assert [b.text for b in buttons] == ["Unpaid"]


def test_orders_menu_only_confirmed_shows_empty():
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from tgtg_scanner.models.reservations import Order

    telegram = object.__new__(Telegram)
    telegram.reservations = MagicMock()
    telegram.reservations.active_orders = {"paid": Order("paid", "1", 1, "Paid", status="CONFIRMED", created_at=1)}
    telegram._is_my_chat = lambda update: True  # type: ignore[method-assign]
    update = MagicMock()
    update.message.reply_text = AsyncMock()

    asyncio.run(telegram._cancel_orders_menu(update, None))

    update.message.reply_text.assert_called_once_with("No cancelable Orders")


def test_action_required_text_includes_url():
    r = Reservation(
        item_id="i",
        amount=1,
        display_name="Bakery",
        status=PAYMENT_ACTION_REQUIRED,
        auth_url="https://bank.example/3ds",
    )
    text = Telegram._reservation_text(r)
    assert "verification" in text.lower() or "bank" in text.lower()
    assert "https://bank.example/3ds" in text


def test_action_required_text_without_url():
    r = Reservation(item_id="i", amount=1, display_name="Bakery", status=PAYMENT_ACTION_REQUIRED)
    text = Telegram._reservation_text(r)
    assert "verification" in text.lower() or "bank" in text.lower()
    assert "(no link captured" in text


def test_reservation_reply_markup_has_url_button():
    r = Reservation(
        item_id="i",
        amount=1,
        display_name="Bakery",
        status=PAYMENT_ACTION_REQUIRED,
        auth_url="https://bank.example/3ds",
    )
    markup = Telegram._reservation_reply_markup(r)
    assert isinstance(markup, InlineKeyboardMarkup)
    button = markup.inline_keyboard[0][0]
    assert button.url == "https://bank.example/3ds"


def test_reservation_reply_markup_none_without_url():
    r = Reservation(item_id="i", amount=1, display_name="Bakery", status="reserved")
    assert Telegram._reservation_reply_markup(r) is None


def test_reservation_reply_markup_none_for_payment_failed():
    r = Reservation(
        item_id="i",
        amount=1,
        display_name="Bakery",
        status=PAYMENT_FAILED,
        auth_url="https://bank.example/3ds",
    )
    assert Telegram._reservation_reply_markup(r) is None
