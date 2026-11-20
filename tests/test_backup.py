import datetime as dt
import sqlite3

import pytest

from fin.backup import (
    BackupError, MAGIC, create_backup, list_backups, prune, read_backup, restore_to, verify,
)
from fin.scheduler import TASK_NAME, install, query, task_xml

PW = "correct horse battery staple"


def _db(path):
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, amount TEXT)")
    con.executemany("INSERT INTO transactions(amount) VALUES (?)", [("1.00",), ("2.50",)])
    con.commit()
    return con   # kept open: backups must work while the app is using the DB


def test_roundtrip_verify_restore(tmp_path):
    con = _db(tmp_path / "fin.db")
    out = tmp_path / "backups"
    p = create_backup(tmp_path / "fin.db", out, PW, "FIELDKEY", now=dt.datetime(2026, 11, 20, 7, 30))
    assert p.name == "finintel-20261120-073000.fbk" and p.read_bytes().startswith(MAGIC)
    raw = p.read_bytes()
    assert b"transactions" not in raw and b"FIELDKEY" not in raw      # encrypted, not readable
    db, meta = read_backup(p, PW)
    assert meta["field_key"] == "FIELDKEY"
    info = verify(p, PW)
    assert info["integrity"] == "ok" and info["transactions"] == 2
    dest = tmp_path / "restored.db"
    restore_to(p, PW, dest)
    assert sqlite3.connect(dest).execute("select count(*) from transactions").fetchone()[0] == 2
    with pytest.raises(BackupError):
        restore_to(p, PW, dest)                                      # never overwrites
    con.close()


def test_wrong_passphrase_and_short_passphrase(tmp_path):
    _db(tmp_path / "fin.db").close()
    p = create_backup(tmp_path / "fin.db", tmp_path, PW, None)
    with pytest.raises(BackupError, match="Wrong passphrase"):
        read_backup(p, "not the right passphrase")
    with pytest.raises(BackupError, match="at least"):
        create_backup(tmp_path / "fin.db", tmp_path, "short", None)
    (tmp_path / "junk.fbk").write_bytes(b"hello")
    with pytest.raises(BackupError, match="Not a finintel"):
        read_backup(tmp_path / "junk.fbk", PW)


def test_prune_keeps_newest(tmp_path):
    for i in range(5):
        (tmp_path / f"finintel-2026110{i}-000000.fbk").write_bytes(b"x")
    removed = prune(tmp_path, keep=2)
    assert len(removed) == 3
    assert [p.name for p in list_backups(tmp_path)] == ["finintel-20261104-000000.fbk",
                                                        "finintel-20261103-000000.fbk"]


def test_task_xml_and_install_with_fake_schtasks():
    xml = task_xml(r"C:\finintel\.venv\Scripts\pythonw.exe", dt.datetime(2026, 11, 20, 7, 30),
                   r"C:\finintel")
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "<Arguments>-m fin daily</Arguments>" in xml and "LeastPrivilege" in xml
    calls = []

    class R:
        returncode, stdout, stderr = 0, "TaskName: \\finintel daily\nStatus: Ready\nFoo: bar", ""

    def fake(cmd, **kw):
        calls.append(cmd)
        return R()

    msg = install("07:30", runner=fake, python_exe="pythonw.exe", workdir="C:\\x")
    assert "07:30" in msg and calls[0][:4] == ["schtasks", "/Create", "/TN", TASK_NAME]
    assert "Status: Ready" in query(runner=fake) and "Foo" not in query(runner=fake)
    with pytest.raises(ValueError):
        install("25:00", runner=fake)
