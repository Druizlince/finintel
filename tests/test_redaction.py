from fin.logsetup import redact


def test_plaid_tokens_are_redacted():
    out = redact("exchanged -> access-sandbox-8ab976e6-64bc-4b38-98f7-731e7a349970")
    assert "8ab976e6" not in out
    assert "access-sandbox-[REDACTED]" in out


def test_key_value_credentials_are_redacted():
    out = redact('secret=abc123 api_key: "XYZ789" {"user_secret": "s3cr3t"}')
    assert "abc123" not in out and "XYZ789" not in out and "s3cr3t" not in out


def test_bearer_and_email_are_redacted():
    out = redact("Authorization header Bearer eyJhbGciOi.abc.def from user@example.com")
    assert "eyJhbGciOi" not in out
    assert "user@example.com" not in out


def test_long_numbers_keep_only_last_four():
    assert redact("account 123456785445") == "account ****5445"


def test_amounts_and_short_numbers_untouched():
    msg = "balance 1234.56 across 3 accounts, card ending 4417"
    assert redact(msg) == msg
