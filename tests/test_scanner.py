import pytest
from pytest_mock.plugin import MockerFixture

from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models import Config, Item
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
