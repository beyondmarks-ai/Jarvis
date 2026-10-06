import pytest
from .pairing import pairing_url

TOKEN = "test_private_microphone_key_123456789"


def test_pairing_uses_fragment_only():
    assert pairing_url("https://hub.example.com/", TOKEN) == "https://hub.example.com/#key=" + TOKEN


@pytest.mark.parametrize("url", ["http://hub.example.com", "https://user:pass@hub.example.com",
    "https://hub.example.com/?key=bad", "https://hub.example.com/#old", "https://hub.example.com/path"])
def test_pairing_rejects_unsafe_urls(url):
    with pytest.raises(ValueError):
        pairing_url(url, TOKEN)


@pytest.mark.parametrize("token", ["", "short", "x" * 129, "<script>" * 6])
def test_pairing_rejects_invalid_keys(token):
    with pytest.raises(ValueError):
        pairing_url("https://hub.example.com", token)
