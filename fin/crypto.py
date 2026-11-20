"""Field-level encryption for the few sensitive values stored in the database.

The Fernet key lives in the OS keyring (secret name: db_field_key). If that key is lost,
encrypted fields cannot be decrypted - they only hold re-linkable provider references,
so the fix is simply to re-link the account.
"""

from cryptography.fernet import Fernet, InvalidToken

from fin import secrets

FIELD_KEY = "db_field_key"


class DecryptionError(RuntimeError):
    pass


def ensure_field_key() -> bool:
    """Create the field key if missing. Returns True if a new key was created."""
    if secrets.get_secret(FIELD_KEY):
        return False
    secrets.set_secret(FIELD_KEY, Fernet.generate_key().decode())
    return True


def _fernet() -> Fernet:
    ensure_field_key()
    return Fernet(secrets.require_secret(FIELD_KEY).encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise DecryptionError("Could not decrypt field (wrong or rotated key)") from exc
