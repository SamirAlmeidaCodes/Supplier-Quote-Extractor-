"""Validation for values that arrive from the browser or a model id field.

API keys are session input, not configuration. Nothing in this module reads
the process environment or a file.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.config import (
    MAX_API_KEY_LENGTH,
    MAX_UPLOAD_BYTES,
    MIN_API_KEY_LENGTH,
    get_provider_spec,
)
from app.errors import AppError

_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def checked_api_key(api_key: str) -> str:
    """Accept a single-line ASCII token and reject header-injection characters."""

    key = (api_key or "").strip()
    if (
        len(key) < MIN_API_KEY_LENGTH
        or len(key) > MAX_API_KEY_LENGTH
        or not key.isascii()
        or not key.isprintable()
        or any(char.isspace() for char in key)
    ):
        raise AppError(
            "Paste a valid API key for the selected provider. The key stays in this session only."
        )
    return key


def checked_model(model: str) -> str:
    """Accept a provider model id. Slashes and spaces cannot retarget the URL."""

    name = (model or "").strip()
    if _MODEL_RE.fullmatch(name) is None:
        raise AppError(
            "Enter a model id using letters, numbers, and . _ : - , or leave the override blank to use the default."
        )
    return name


def sanitize_filename(filename: str) -> str:
    """Reduce an upload name to a display-safe basename."""

    base = Path(str(filename or "quote")).name
    cleaned = _FILENAME_SAFE.sub("_", base.replace("\\", "_").replace("/", "_"))
    cleaned = re.sub(r"\.{2,}", ".", cleaned).strip(" ._")
    if not cleaned:
        return "quote"
    return cleaned[:120]


def path_within(path: Path, parent: Path) -> bool:
    """Return whether ``path`` resolves inside ``parent``."""

    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


class SessionSettings(BaseModel):
    """Sidebar values checked before any provider call.

    An empty API key is allowed so the page can render before the user pastes
    one. Extraction stays disabled until the key passes ``checked_api_key``.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    provider_id: str
    provider_label: str = Field(max_length=80)
    api_key: SecretStr = SecretStr("")
    model: str
    mode: str
    threshold: float = Field(ge=0.5, le=0.95)

    @field_validator("provider_id")
    @classmethod
    def _provider(cls, value: str) -> str:
        try:
            get_provider_spec(value)
        except AppError as exc:
            raise ValueError(str(exc)) from None
        return value

    @field_validator("provider_label")
    @classmethod
    def _label(cls, value: str) -> str:
        label = (value or "").strip()
        if not label:
            raise ValueError("Choose a provider from the sidebar.")
        return label

    @field_validator("api_key", mode="before")
    @classmethod
    def _key(cls, value: object) -> str:
        if isinstance(value, SecretStr):
            raw = value.get_secret_value()
        else:
            raw = "" if value is None else str(value)
        raw = raw.strip()
        if not raw:
            return ""
        try:
            return checked_api_key(raw)
        except AppError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("model")
    @classmethod
    def _model(cls, value: str) -> str:
        try:
            return checked_model(value)
        except AppError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("mode")
    @classmethod
    def _mode(cls, value: str) -> str:
        if value not in {"combined", "per_page"}:
            raise ValueError("Choose combined extraction or page-by-page extraction.")
        return value

    def revealed_key(self) -> str:
        """Return the session key for the provider call. Do not log the result."""

        return self.api_key.get_secret_value()


class UploadConstraints(BaseModel):
    """Size bound checked again at the document loader."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    size: int = Field(ge=1, le=MAX_UPLOAD_BYTES)

    @field_validator("filename")
    @classmethod
    def _filename(cls, value: str) -> str:
        return sanitize_filename(value)
