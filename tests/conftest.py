import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class MemoryKeyring(KeyringBackend):
    """In-memory keyring so tests never touch your real Windows Credential Manager."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):  # noqa: ANN001
        self.store[(service, username)] = password

    def get_password(self, service, username):  # noqa: ANN001
        return self.store.get((service, username))

    def delete_password(self, service, username):  # noqa: ANN001
        try:
            del self.store[(service, username)]
        except KeyError as exc:
            raise PasswordDeleteError(username) from exc


@pytest.fixture(autouse=True)
def memory_keyring():
    original = keyring.get_keyring()
    kr = MemoryKeyring()
    keyring.set_keyring(kr)
    yield kr
    keyring.set_keyring(original)


@pytest.fixture
def engine():
    from fin.db.session import init_db, make_engine

    eng = make_engine("sqlite:///:memory:")
    init_db(eng)
    yield eng
    eng.dispose()
