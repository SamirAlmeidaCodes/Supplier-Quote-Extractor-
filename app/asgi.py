"""ASGI entrypoint for Supplier Quote Extractor.

Run ``streamlit run app/asgi.py`` from the project directory. The UI stays in
``app/main.py``. This module adds response headers and a generic script-error
handler. Debug mode on the ASGI app is off.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Streamlit puts this file's directory on sys.path. That directory is the
# ``app`` package, so ``import app`` would fail. Put the project root first.
ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = str(Path(__file__).resolve().parent)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
while SCRIPT_DIR in sys.path:
    sys.path.remove(SCRIPT_DIR)

import streamlit as st
from starlette.middleware import Middleware

from app.http_security import SecurityHeadersMiddleware
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)
_UI_SCRIPT = Path(__file__).resolve().parent / "main.py"


def _on_script_error(exc: Exception) -> bool:
    configure_logging()
    logger.error("Unhandled script error (%s)", type(exc).__name__)
    st.error("Something went wrong. The details were withheld from this page.")
    return True


configure_logging()
app = st.App(
    str(_UI_SCRIPT),
    middleware=[Middleware(SecurityHeadersMiddleware)],
    on_script_error=_on_script_error,
    debug=False,
)


if __name__ == "__main__":
    app.run()
