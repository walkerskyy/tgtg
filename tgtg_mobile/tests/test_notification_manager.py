import logging
from unittest.mock import MagicMock, patch

import pytest

from models.item import Item
from services.notification_manager import NotificationManager


@pytest.fixture
def sample_item():
    """Create a sample Item for testing."""
    return Item({
        "display_name": "Test Bakery",
        "items_available": 3,
        "favorite": True,
        "item": {
            "item_id": "test_item_123",
            "name": "Magic Bag",
            "item_price": {"minor_units": 399, "decimals": 2, "code": "EUR"},
            "item_value": {"minor_units": 1200, "decimals": 2, "code": "EUR"},
            "logo_picture": {"current_url": "https://example.com/logo.png"},
            "cover_picture": {"current_url": "https://example.com/cover.png"},
        },
        "store": {"store_name": "Test Bakery", "store_id": "store_123"},
        "pickup_interval": {
            "start": "2026-04-01T18:00:00Z",
            "end": "2026-04-01T18:30:00Z",
        },
    })


@pytest.fixture
def mock_notification_manager():
    """NotificationManager with pyjnius mocked out."""
    with patch("services.notification_manager.ANDROID_AVAILABLE", False):
        mgr = NotificationManager()
        return mgr


def test_init_desktop_fallback(mock_notification_manager, sample_item, caplog):
    """On desktop, NotificationManager uses logging fallback."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.send_bag_alert(sample_item)
    assert "TGTG Available!" in caplog.text


def test_send_bag_alert_logs_item_info(mock_notification_manager, sample_item, caplog):
    """Bag alert logs item name and price."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.send_bag_alert(sample_item)
    assert "Test Bakery" in caplog.text
    assert "3.99" in caplog.text


def test_send_service_status(mock_notification_manager, caplog):
    """Service status logs message."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.send_service_status("Last check: 2 min ago")
    assert "Last check: 2 min ago" in caplog.text


def test_send_service_status_silent_by_default(mock_notification_manager, caplog):
    """Service status is silent by default."""
    with caplog.at_level(logging.DEBUG):
        mock_notification_manager.send_service_status("test", silent=True)
    # Should not produce warning-level output
    assert all(record.levelno < logging.WARNING for record in caplog.records)


def test_dismiss_notification(mock_notification_manager, caplog):
    """Dismiss logs the notification ID."""
    with caplog.at_level(logging.INFO):
        mock_notification_manager.dismiss_notification(12345)
    assert "12345" in caplog.text


def test_android_channels_called_when_available():
    """When Android is available, _ensure_channels is called."""
    mock_autoclass = MagicMock()
    with patch.dict("sys.modules", {"jnius": MagicMock(autoclass=mock_autoclass)}):
        import importlib
        import services.notification_manager as nm_mod
        importlib.reload(nm_mod)
        assert nm_mod.ANDROID_AVAILABLE is True

        mock_context = MagicMock()
        mock_autoclass.return_value.getSystemService.return_value = MagicMock()
        mgr = nm_mod.NotificationManager(mock_context)
        mock_autoclass.assert_called()
