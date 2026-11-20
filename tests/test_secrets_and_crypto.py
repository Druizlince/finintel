import pytest

from fin import crypto, secrets


def test_set_get_delete_roundtrip():
    secrets.set_secret("fred_api_key", "  abc  ")
    assert secrets.get_secret("fred_api_key") == "abc"  # whitespace stripped
    assert secrets.delete_secret("fred_api_key") is True
    assert secrets.get_secret("fred_api_key") is None
    assert secrets.delete_secret("fred_api_key") is False


def test_namespaced_names_allowed_and_bad_names_rejected():
    secrets.set_secret("plaid_token:item-ABC_123", "tok")
    assert secrets.get_secret("plaid_token:item-ABC_123") == "tok"
    with pytest.raises(ValueError):
        secrets.set_secret("bad name!", "x")


def test_require_missing_secret_explains_fix():
    with pytest.raises(secrets.MissingSecretError, match="fin secret set plaid_secret"):
        secrets.require_secret("plaid_secret")


def test_status_never_returns_values():
    secrets.set_secret("fmp_api_key", "super-secret-value")
    st = secrets.status()
    assert st["fmp_api_key"] is True
    assert "super-secret-value" not in repr(st)


def test_field_encryption_roundtrip_creates_key_once():
    assert crypto.ensure_field_key() is True
    assert crypto.ensure_field_key() is False
    token = crypto.encrypt("item-ABC")
    assert token != "item-ABC"
    assert crypto.decrypt(token) == "item-ABC"


def test_decrypt_with_wrong_key_fails_cleanly():
    token = crypto.encrypt("item-ABC")
    secrets.delete_secret(crypto.FIELD_KEY)  # simulate a lost key; a new one gets generated
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token)
