"""Logging that cannot print an API key, a traceback, or a filesystem path.

httpx is kept at warning level so request headers are not written. Every log
line is passed through the same redaction used for provider error text.
"""

from __future__ import annotations

import logging
import re

from app.redact import redact

_CONFIGURED = False
_ABS_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:[A-Za-z0-9._@+-]+/){1,}[A-Za-z0-9._@+-]*")
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s'\"]+")
_ORIGINAL_FACTORY = logging.getLogRecordFactory()


def _strip_paths(text: str) -> str:
    cleaned = _ABS_PATH.sub("[path]", text)
    return _WIN_PATH.sub("[path]", cleaned)


def _sanitize_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact a record at creation time so every handler sees the safe text.

    A filter on the root logger does not run for records emitted by child
    loggers, and those loggers are where request failures are reported.
    """

    try:
        record.msg = _strip_paths(redact(record.getMessage()))
        record.args = ()
        if record.exc_info:
            exc_type = record.exc_info[0]
            record.exc_text = exc_type.__name__ if isinstance(exc_type, type) else "Error"
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = _strip_paths(redact(record.exc_text.splitlines()[0]))
    except Exception:
        record.msg = "Log record withheld."
        record.args = ()
        record.exc_info = None
        record.exc_text = None
    return record


def _record_factory(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _ORIGINAL_FACTORY(*args, **kwargs)
    return _sanitize_record(record)


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.setLogRecordFactory(_record_factory)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("streamlit").setLevel(logging.INFO)
    _CONFIGURED = True
