"""Entry point for ``streamlit run app/main.py``.

Streamlit puts this file's directory, ``app/``, at the front of ``sys.path``.
That would hide standard-library modules that share a name with a file in
``app/`` and would also make ``import app`` fail. The project root is added,
and the script directory is removed, before the package import.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
script_dir = str(SCRIPT_DIR)
while script_dir in sys.path:
    sys.path.remove(script_dir)

import logging

from app.logging_setup import configure_logging
from app.ui import render

logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    try:
        render()
    except Exception as exc:
        logger.error("Unhandled application error (%s)", type(exc).__name__)
        import streamlit as st

        st.error("Something went wrong. The details were withheld from this page.")


main()
