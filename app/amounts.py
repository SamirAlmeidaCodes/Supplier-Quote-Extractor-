"""Parse amounts copied from quotes.

Quotes use both ``1,234.50`` and ``1.234,50``. A bare comma is treated as a
thousands separator when it does not look like a decimal comma.

This module is not named ``numbers`` because Streamlit puts the ``app``
directory on ``sys.path``, and that name would hide the standard-library
``numbers`` module.
"""

from __future__ import annotations

import re

_CURRENCY_WORDS = ("USD", "EUR", "GBP", "CAD", "AUD", "CHF", "JPY", "INR")
_BLANK = {"", "null", "none", "n/a", "na", "-", "—", "–"}


def parse_decimal(raw: object) -> float | None:
    """Return a float, or ``None`` when ``raw`` is blank or not numeric."""

    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip()
    if text.lower() in _BLANK:
        return None

    for word in _CURRENCY_WORDS:
        text = re.sub(rf"(?i)\b{word}\b", "", text)
    text = text.replace("€", "").replace("£", "").replace("$", "").replace("¥", "")
    text = text.replace(" ", "").replace("\u00a0", "")
    if not text or text.lower() in _BLANK:
        return None

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = True
        text = text[1:]

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        left, right = text.split(",", 1)
        if right.isdigit() and len(right) <= 2 and left.replace(",", "").isdigit():
            text = f"{left.replace(',', '')}.{right}"
        else:
            text = text.replace(",", "")

    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    value = float(text)
    return -value if negative else value


def format_amount(value: float) -> str:
    """Format a float the way the review table prints money."""

    return f"{value:,.2f}"
