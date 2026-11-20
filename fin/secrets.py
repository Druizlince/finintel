"""Secret storage backed by the OS keyring (Windows Credential Manager on Windows).

Secrets are entered with `fin secret set <name>` (hidden input) - never pasted into chat,
never written to .env, never logged.
"""

import re

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "finintel"

# Static secrets the app knows about, with where to get each one.
KNOWN_SECRETS: dict[str, str] = {
    "plaid_client_id": "Plaid Dashboard -> Developers -> Keys",
    "plaid_secret": "Plaid Dashboard -> Developers -> Keys (Sandbox secret)",
    "plaid_secret_production": "Plaid Dashboard -> Developers -> Keys (Production secret, later)",
    "snaptrade_client_id": "SnapTrade dashboard -> API keys",
    "snaptrade_consumer_key": "SnapTrade dashboard -> API keys",
    "fred_api_key": "https://fredaccount.stlouisfed.org/apikeys",
    "fmp_api_key": "Financial Modeling Prep dashboard",
    "finnhub_api_key": "Finnhub dashboard",
    "massive_api_key": "Massive (formerly Polygon.io) dashboard",
    "db_field_key": "Generated automatically by `fin init` - do not set by hand",
    "backup_passphrase": "Chosen by you with `fin backup setup` - also save it in a password manager",
}

# Plain names (plaid_secret) or namespaced per-connection names (plaid_token:<item_id>).
_NAME_RE = re.compile(r"^[a-z0-9_]+(:[A-Za-z0-9_\-]+)?$")


class MissingSecretError(RuntimeError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Secret '{name}' is not set. Run: uv run python -m fin secret set {name}")
        self.secret_name = name


def _check_name(name: str) -> None:
    if not _NAME_RE.match(name):
        raise ValueError(f"Invalid secret name: {name!r}")


def set_secret(name: str, value: str) -> None:
    _check_name(name)
    if not value or not value.strip():
        raise ValueError("Secret value is empty")
    keyring.set_password(SERVICE, name, value.strip())


def get_secret(name: str) -> str | None:
    _check_name(name)
    return keyring.get_password(SERVICE, name)


def require_secret(name: str) -> str:
    value = get_secret(name)
    if not value:
        raise MissingSecretError(name)
    return value


def delete_secret(name: str) -> bool:
    _check_name(name)
    try:
        keyring.delete_password(SERVICE, name)
    except PasswordDeleteError:
        return False
    return True


def status() -> dict[str, bool]:
    """Which known secrets are set. Returns booleans only - never values."""
    return {name: get_secret(name) is not None for name in KNOWN_SECRETS}


def backend_name() -> str:
    kr = keyring.get_keyring()
    return f"{type(kr).__module__}.{type(kr).__name__}"
