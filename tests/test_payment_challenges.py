import json

from tgtg_scanner.models.payment_challenges import extract_auth_url


def test_none_for_empty():
    assert extract_auth_url(None) is None
    assert extract_auth_url({}) is None


def test_direct_url_keys():
    assert extract_auth_url({"url": "https://bank.example/3ds"}) == "https://bank.example/3ds"
    assert extract_auth_url({"redirect_url": "https://bank.example/3ds"}) == "https://bank.example/3ds"
    assert extract_auth_url({"verification_url": "https://bank.example/3ds"}) == "https://bank.example/3ds"


def test_adyen_action_url():
    payload = {"action": {"type": "redirect", "url": "https://checkoutshopper-test.adyen.com/3ds/abc"}}
    assert extract_auth_url(payload) == "https://checkoutshopper-test.adyen.com/3ds/abc"


def test_nested_payload_json_string():
    inner = {"url": "https://bank.example/challenge", "type": "redirect"}
    payload = {"payload": json.dumps(inner)}
    assert extract_auth_url(payload) == "https://bank.example/challenge"


def test_authorization_payload_nested_string():
    inner = {"threeDS2SdkVersion": "2.2.27", "challengeUrl": "https://bank.example/c2"}
    outer = {"authorizations": [{"authorization_payload": {"payload": json.dumps(inner)}}]}
    assert extract_auth_url(outer) == "https://bank.example/c2"


def test_ignores_non_https_and_tgtg_urls():
    assert extract_auth_url({"url": "javascript:alert(1)"}) is None
    assert extract_auth_url({"url": "https://apptoogoodtogo.com/x"}) is None


def test_prefers_action_over_generic():
    payload = {"url": "https://a.example/1", "action": {"url": "https://b.example/2"}}
    assert extract_auth_url(payload) == "https://b.example/2"
