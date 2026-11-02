"""Limits, defaults, and the provider catalog.

API keys are not configured here. The running app accepts a key only from the
Streamlit password field and keeps it in memory for that session.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.errors import AppError


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PAGES = 10
# Vision requests get expensive and large quickly. Longer documents are split
# into batches of this size and merged after validation.
MAX_PAGES_PER_REQUEST = 4
MAX_EDGE_PX = 1800
JPEG_QUALITY_START = 85
TARGET_IMAGE_BYTES = 1_200_000
REQUEST_TIMEOUT_SECONDS = 240
DEFAULT_REVIEW_THRESHOLD = 0.70
MIN_API_KEY_LENGTH = 12
MAX_API_KEY_LENGTH = 500
RETRY_STATUSES = frozenset({429, 502, 503})


@dataclass(frozen=True)
class ProviderSpec:
    """One vision provider the sidebar can select."""

    id: str
    label: str
    default_model: str
    key_hint: str
    docs_url: str


# xAI is first because it is the default. Model ids follow the provider docs
# as of the 2026 guides used to build this app. The sidebar can override them.
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        id="xai",
        label="xAI (Grok)",
        default_model="grok-4.7",
        key_hint="Create a key in the xAI console. It is sent only to api.x.ai.",
        docs_url="https://console.x.ai/team/default/api-keys",
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI",
        default_model="gpt-4.1",
        key_hint="Create a key on the OpenAI platform. It is sent only to api.openai.com.",
        docs_url="https://platform.openai.com/api-keys",
    ),
    ProviderSpec(
        id="anthropic",
        label="Anthropic",
        default_model="claude-sonnet-4-6",
        key_hint="Create a key in the Anthropic console. It is sent only to api.anthropic.com.",
        docs_url="https://console.anthropic.com/settings/keys",
    ),
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        default_model="gemini-2.5-flash",
        key_hint="Create a key in Google AI Studio. It is sent only to generativelanguage.googleapis.com.",
        docs_url="https://aistudio.google.com/apikey",
    ),
)

PROVIDERS_BY_ID = {spec.id: spec for spec in PROVIDERS}


def get_provider_spec(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS_BY_ID[provider_id]
    except KeyError:
        known = ", ".join(spec.label for spec in PROVIDERS)
        raise AppError(f"Unknown provider. Choose one of: {known}.") from None


