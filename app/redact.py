"""Remove API keys from text before it is shown or logged."""

from __future__ import annotations

import re

# Common provider key shapes. The exact key is always removed as well, so a
# provider that uses a different prefix is still covered when we have it.
_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"xai-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+"),
    re.compile(r"(?i)(api[_-]?key|access_token|key)=([^&\s]+)"),
)

_REPLACEMENTS = (
    "[redacted]",
    "[redacted]",
    "[redacted]",
    "Bearer [redacted]",
    r"\1=[redacted]",
)


def redact(text: str, secret: str | None = None) -> str:
    """Return ``text`` with secrets replaced.

    ``secret`` is the key from the current request. Replacing it exactly
    catches provider formats that do not match the patterns above.
    """

    cleaned = text or ""
    if secret:
        stripped = secret.strip()
        if stripped:
            cleaned = cleaned.replace(stripped, "[redacted]")
    for pattern, replacement in zip(_PATTERNS, _REPLACEMENTS):
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned
