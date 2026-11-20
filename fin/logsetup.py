"""Logging that never writes secrets or account numbers.

IMPORTANT: logs go to stderr and a file - never stdout. The MCP server uses stdout
for the protocol itself, so a stray print() or stdout log would break Claude's connection.
"""

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Plaid tokens: access-sandbox-..., public-production-..., link-...
    (
        re.compile(r"\b(access|public|link|processor)-(sandbox|development|production)-[A-Za-z0-9\-]+"),
        r"\1-\2-[REDACTED]",
    ),
    # key=value / "key": "value" for anything that looks like a credential
    (
        re.compile(
            r"(?i)\b(secret|token|api[_\-]?key|password|consumer[_\-]?key|user[_\-]?secret"
            r"|access[_\-]?token|authorization)(\"?\s*[:=]\s*\"?)([^\s\"',}]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]+"), "Bearer [REDACTED]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    # Any run of 8+ digits (account / card numbers): keep only the last 4
    (re.compile(r"\b\d{4,}(\d{4})\b"), r"****\1"),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Redacts the fully formatted record, including tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_FMT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_TAG = "_finintel_handler"


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        if getattr(h, _TAG, False):
            root.removeHandler(h)

    formatter = RedactingFormatter(_FMT)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(formatter)
    setattr(stderr, _TAG, True)
    root.addHandler(stderr)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "fin.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(formatter)
        setattr(fh, _TAG, True)
        root.addHandler(fh)

    quiet_sdk_loggers()

    return logging.getLogger("fin")


def quiet_sdk_loggers() -> None:
    """Third-party SDK chatter (e.g. SnapTrade "deprecated" notices, printed twice by its own
    handler): keep only real errors. Call again after importing an SDK, since importing can
    attach new handlers. TODO: move off deprecated SnapTrade calls once replacements are stable."""
    sdk = logging.getLogger("snaptrade_client")
    sdk.setLevel(logging.ERROR)
    for h in list(sdk.handlers):
        sdk.removeHandler(h)
