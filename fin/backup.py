"""Phase 6 - encrypted backups of the finintel database.

What is in a backup: the database (transactions, balances, rules, planned expenses, owed-to-me,
targets...) and the field key that decrypts its few encrypted columns.
What is NOT in a backup: bank tokens, API keys, passwords. Those stay in Windows Credential
Manager, so a stolen backup cannot reach any account. After restoring on a new computer you
re-link banks (fin plaid link) and re-enter API keys (fin secret set).

File format (.fbk): b"FINBK1\\n" + 16-byte salt + Fernet token of a zip.
Key = scrypt(passphrase, salt). Without the passphrase a backup cannot be opened - save it in
a password manager.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

MAGIC = b"FINBK1\n"
SUFFIX = ".fbk"
PASS_SECRET = "backup_passphrase"
MIN_PASS = 12


class BackupError(Exception):
    pass


def _key(passphrase: str, salt: bytes) -> bytes:
    raw = hashlib.scrypt(passphrase.encode(), salt=salt, n=2**15, r=8, p=1, dklen=32,
                         maxmem=64 * 1024 * 1024)
    return base64.urlsafe_b64encode(raw)


def check_passphrase(p: str) -> None:
    if len(p) < MIN_PASS:
        raise BackupError(f"Passphrase must be at least {MIN_PASS} characters "
                          "(a few random words works well).")


def snapshot(db_path: Path) -> bytes:
    """Consistent copy of a live SQLite DB (safe while the app or Claude is using it)."""
    if not db_path.exists():
        raise BackupError(f"Database not found: {db_path}")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "snap.db"
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        return tmp.read_bytes()


def create_backup(db_path: Path, out_dir: Path, passphrase: str, field_key: str | None,
                  now: dt.datetime | None = None) -> Path:
    check_passphrase(passphrase)
    now = now or dt.datetime.now()
    data = snapshot(db_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("fin.db", data)
        z.writestr("meta.json", json.dumps({"format": 1, "created": now.isoformat(timespec="seconds"),
                                            "db_bytes": len(data), "field_key": field_key}))
    salt = os.urandom(16)
    token = Fernet(_key(passphrase, salt)).encrypt(buf.getvalue())
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"finintel-{now:%Y%m%d-%H%M%S}{SUFFIX}"
    tmp = path.with_suffix(".part")
    tmp.write_bytes(MAGIC + salt + token)
    tmp.replace(path)          # never leave a half-written backup behind
    return path


def read_backup(path: Path, passphrase: str) -> tuple[bytes, dict]:
    blob = Path(path).read_bytes()
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + 17:
        raise BackupError("Not a finintel backup file.")
    salt = blob[len(MAGIC):len(MAGIC) + 16]
    try:
        raw = Fernet(_key(passphrase, salt)).decrypt(blob[len(MAGIC) + 16:])
    except InvalidToken:
        raise BackupError("Wrong passphrase, or the file is damaged.") from None
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        db = z.read("fin.db")
        meta = json.loads(z.read("meta.json"))
    if len(db) != meta.get("db_bytes"):
        raise BackupError("Backup is incomplete.")
    return db, meta


def verify(path: Path, passphrase: str) -> dict:
    """Decrypt and run SQLite's integrity check on the copy. Returns summary info."""
    db, meta = read_backup(path, passphrase)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "check.db"
        p.write_bytes(db)
        con = sqlite3.connect(p)
        try:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            tx = con.execute("SELECT count(*) FROM transactions").fetchone()[0] \
                if con.execute("SELECT name FROM sqlite_master WHERE name='transactions'").fetchone() else 0
        finally:
            con.close()
    return {"file": str(path), "created": meta.get("created"), "integrity": ok,
            "transactions": tx, "size_mb": round(Path(path).stat().st_size / 1e6, 2)}


def restore_to(path: Path, passphrase: str, dest: Path) -> dict:
    """Write the backup's database to dest (never overwrites an existing file)."""
    if dest.exists():
        raise BackupError(f"{dest} already exists - choose a new file name.")
    db, meta = read_backup(path, passphrase)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(db)
    return meta


def list_backups(out_dir: Path) -> list[Path]:
    return sorted(out_dir.glob(f"finintel-*{SUFFIX}"), reverse=True) if out_dir.exists() else []


def prune(out_dir: Path, keep: int = 30) -> list[Path]:
    """Keep the newest `keep` backups; remove older ones."""
    removed = []
    for p in list_backups(out_dir)[keep:]:
        try:
            p.unlink()
            removed.append(p)
        except OSError:
            pass
    return removed
