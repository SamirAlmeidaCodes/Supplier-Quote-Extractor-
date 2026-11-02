"""Logging that cannot print an API key.

httpx is kept at warning level so request headers are not written. Every log
line is passed through the same redaction used for provider error text.
"""

from __future__ import annotations

import logging
import traceback

from app.redact import redact

_CONFIGURED = False


class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_info:
            record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)))
            record.exc_info = None
        return True


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().addFilter(_RedactFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("streamlit").setLevel(logging.INFO)
    _CONFIGURED = True
