"""Tests for scanner-specific tgtg glue (not the upstream package itself)."""

import json
import pathlib
from os import getenv
from typing import Any
from urllib.parse import urljoin

import pytest
import responses
import tgtg
from pytest_mock.plugin import MockerFixture
from tgtg.exceptions import TgtgAPIError

from tgtg_scanner.models import Config
from tgtg_scanner.tgtg_client import TgtgClient, extract_datadome, normalize_cookie, resolve_user_agent

BASE_URL = tgtg.BASE_URL


def _datadome_sdk():
    responses.add(
        responses.POST,
        "https://api-sdk.datadome.co/sdk/",
        json={"status": 200, "cookie": "datadome=testdd;"},
        status=200,
    )


def test_cookie_helpers():
    assert normalize_cookie(None) is None
    assert normalize_cookie("abc") == "datadome=abc"
    assert normalize_cookie("datadome=xyz; Path=/") == "datadome=xyz; Path=/"


def test_resolve_user_agent():
    assert resolve_user_agent() is None
    assert resolve_user_agent(user_agent="Custom") == "Custom"
    assert resolve_user_agent(apk_version="22.11.11").startswith("TGTG/22.11.11 ")


@responses.activate
def test_login_with_browser_pin_fallback_to_polling(mocker: MockerFixture):
    """Empty browser PIN falls through to upstream email-link polling."""
    mocker.patch("tgtg_scanner.tgtg_client.prompt_via_browser", return_value="")
    mocker.patch("tgtg.time.sleep", return_value=None)
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, tgtg.AUTH_BY_EMAIL_ENDPOINT),
        json={"state": "WAIT", "polling_id": "poll-1"},
        status=200,
    )
    responses.add(responses.POST, urljoin(BASE_URL, tgtg.AUTH_POLLING_ENDPOINT), status=202)
    responses.add(
        responses.POST,
        urljoin(BASE_URL, tgtg.AUTH_POLLING_ENDPOINT),
        json={"access_token": "at", "refresh_token": "rt"},
        status=200,
        headers={"Set-Cookie": "datadome=dd; Path=/"},
    )
    client = TgtgClient(email="test@example.com", user_agent="TGTG/test")
    client.login()
    assert client.access_token == "at"
    assert client.refresh_token == "rt"


@responses.activate
def test_login_with_tokens_and_datadome():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, tgtg.REFRESH_ENDPOINT),
        json={"access_token": "new_at", "refresh_token": "new_rt"},
        status=200,
        headers={"Set-Cookie": "datadome=newdd; Path=/"},
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="old_at",
        refresh_token="old_rt",
        cookie=normalize_cookie("olddd"),
        user_agent="TGTG/test",
    )
    client.login()
    assert client.access_token == "new_at"
    assert extract_datadome(client)


@responses.activate
def test_get_favorites_loads_all_pages(tgtg_item: dict):
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, tgtg.API_BUCKET_ENDPOINT),
        json={"mobile_bucket": {"items": [tgtg_item] * 100}},
        status=200,
    )
    responses.add(
        responses.POST,
        urljoin(BASE_URL, tgtg.API_BUCKET_ENDPOINT),
        json={"mobile_bucket": {"items": [tgtg_item] * 2}},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    favorites = client.get_favorites()
    assert len(favorites) == 102
    bodies = [json.loads(c.request.body) for c in responses.calls if tgtg.API_BUCKET_ENDPOINT in c.request.url]
    assert bodies[0]["paging"] == {"page": 0, "size": 100}
    assert bodies[1]["paging"] == {"page": 1, "size": 100}


@pytest.mark.tgtg_api
def test_tgtg_api(item_properties: dict):
    config = Config("config.ini") if pathlib.Path("config.ini").is_file() else Config()
    env_file = getenv("GITHUB_ENV")
    kwargs: dict[str, Any] = {
        "email": config.tgtg.username,
        "timeout": config.tgtg.timeout,
        "access_token_lifetime": config.tgtg.access_token_lifetime,
        "max_polling_tries": config.tgtg.max_polling_tries,
        "polling_wait_time": config.tgtg.polling_wait_time,
        "access_token": config.tgtg.access_token,
        "refresh_token": config.tgtg.refresh_token,
        "cookie": normalize_cookie(config.tgtg.datadome),
    }
    if ua := resolve_user_agent(config.tgtg.user_agent, config.tgtg.apk_version):
        kwargs["user_agent"] = ua
    client = TgtgClient(**kwargs)

    if env_file:
        creds = client.get_credentials()
        with open(env_file, "a", encoding="utf-8") as file:
            file.write(f"TGTG_ACCESS_TOKEN={creds['access_token']}\n")
            file.write(f"TGTG_REFRESH_TOKEN={creds['refresh_token']}\n")
            file.write(f"TGTG_COOKIE={creds['datadome_cookie']}\n")

    items = client.get_favorites() or client.get_items(favorites_only=True)
    assert items
    item = items[0]
    item_id = item.get("item", {}).get("item_id")
    for prop in item_properties.get("GLOBAL_PROPERTIES", []):
        assert prop in item
    for prop in item_properties.get("ITEM_PROPERTIES", []):
        assert prop in item.get("item", {})
    for prop in item_properties.get("PRICE_PROPERTIES", []):
        assert prop in item.get("item", {}).get("price_including_taxes")
    client.set_favorite(item_id, False)
    client.set_favorite(item_id, True)
    assert client.get_item(item_id).get("item", {}).get("item_id") == item_id


@responses.activate
def test_login_raises_when_refresh_fails_without_email():
    """When no email configured and refresh fails, TgtgAPIError propagates."""
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, tgtg.REFRESH_ENDPOINT),
        status=403,
        body='{"url":"https://geo.captcha-delivery.com/interstitial/..."}',
    )
    client = TgtgClient(
        access_token="stale_at",
        refresh_token="stale_rt",
        cookie=normalize_cookie("stale_dd"),
        user_agent="TGTG/test",
    )
    with pytest.raises(TgtgAPIError):
        client.login()


@responses.activate
def test_get_payment_methods():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "paymentMethod/v2"),
        json={"consumer_saved_psp_payment_methods": [{"identifier": "1111", "display_value": "•••• 4444"}]},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    result = client.get_payment_methods()
    assert result["consumer_saved_psp_payment_methods"][0]["identifier"] == "1111"
    assert responses.calls[1].request.body in (None, "", b"")


@responses.activate
def test_get_payment_methods_for_item():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "paymentMethod/v2/item/123"),
        json={"payment_methods": []},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    assert client.get_payment_methods("123") == {"payment_methods": []}
    body = json.loads(responses.calls[1].request.body)
    # exact request SupportedPaymentProviderConstant builds in APK 26.10.0
    assert body == {
        "supported_types": [
            {
                "provider": "ADYEN",
                "payment_types": [
                    "CREDITCARD",
                    "SOFORT",
                    "IDEAL",
                    "PAYPAL",
                    "BCMCMOBILE",
                    "BCMCCARD",
                    "VIPPS",
                    "TWINT",
                    "MBWAY",
                    "BLIK",
                    "CASH_APP_PAY",
                    "GOOGLEPAY",
                ],
            },
            {"provider": "CHARITY", "payment_types": ["CHARITY"]},
            {"provider": "SATISPAY", "payment_types": ["SATISPAY"]},
            {"provider": "EDENRED", "payment_types": ["EDENRED"]},
        ]
    }


@responses.activate
def test_get_vouchers():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "voucher/v4/active"),
        json={"vouchers": []},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    assert client.get_vouchers() == {"vouchers": []}


@responses.activate
def test_pay_order_posts_to_v8():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "order/v8/o1/pay"),
        json={"payment_id": "p1", "state": "PENDING"},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    result = client.pay_order("o1", {"authorization": {"payment_provider": "ADYEN"}})
    assert result["payment_id"] == "p1"


@responses.activate
def test_pay_order_falls_back_to_v7_on_404():
    _datadome_sdk()
    responses.add(responses.POST, urljoin(BASE_URL, "order/v8/o1/pay"), status=404)
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "order/v7/o1/pay"),
        json={"payment_id": "p2", "state": "PENDING"},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    result = client.pay_order("o1", {})
    assert result["payment_id"] == "p2"


@responses.activate
def test_pay_order_error_raises():
    _datadome_sdk()
    responses.add(responses.POST, urljoin(BASE_URL, "order/v8/o1/pay"), status=400, body="bad request")
    responses.add(responses.POST, urljoin(BASE_URL, "order/v7/o1/pay"), status=400, body="bad request")
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    with pytest.raises(TgtgAPIError):
        client.pay_order("o1", {})


@responses.activate
def test_get_payment_status():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "payment/v4/p1"),
        json={"payment_id": "p1", "state": "PENDING"},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    assert client.get_payment_status("p1") == {"payment_id": "p1", "state": "PENDING"}


@responses.activate
def test_create_payment_for_order():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "payment/v4/order/o1"),
        json={"payments": [{"payment_id": "p9", "state": "CREATED"}]},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    result = client.create_payment("o1")
    assert result["payments"][0]["payment_id"] == "p9"


@responses.activate
def test_payment_biometrics():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "payment/v4/p1/biometrics"),
        json={"biometrics_protected_secret": "sec", "saved_payment_method_token": "tok", "payment_id": "p1"},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    result = client.payment_biometrics("p1")
    assert result["saved_payment_method_token"] == "tok"


@responses.activate
def test_get_order_status():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "order/v8/o1/status"),
        json={"state": "PAYMENT_PENDING"},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    assert client.get_order_status("o1") == {"state": "PAYMENT_PENDING"}


@responses.activate
def test_get_order_detail():
    _datadome_sdk()
    responses.add(
        responses.POST,
        urljoin(BASE_URL, "order/v8/o7"),
        json={"order": {"id": "o7", "total_price": {"currency": "AUD", "decimals": 2, "minor_units": 850}}},
        status=200,
    )
    client = TgtgClient(
        email="test@example.com",
        access_token="at",
        refresh_token="rt",
        cookie=normalize_cookie("dd"),
        user_agent="TGTG/test",
    )
    client.login = lambda: None  # type: ignore[method-assign]
    assert client.get_order_detail("o7") == {
        "id": "o7",
        "total_price": {"currency": "AUD", "decimals": 2, "minor_units": 850},
    }
    assert responses.calls[1].request.method == "POST"
