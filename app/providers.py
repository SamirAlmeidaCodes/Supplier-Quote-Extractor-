"""Vision calls for xAI, OpenAI, Anthropic, and Google Gemini.

Each provider receives the same JSON Schema and returns the raw JSON text.
The API key is placed only in a request header. It is never written to the
request body, a URL, a log line, or disk.

xAI is called through the chat-completions endpoint rather than the Responses
API. The Responses API stores response objects for 30 days. Chat completions
is the documented path for image understanding with a JSON schema, and it
matches the OpenAI request shape.

Documented shapes this module follows:
- xAI image input and structured outputs: chat completions with image_url and response_format
- OpenAI structured outputs: response_format json_schema, strict true
- Anthropic: Messages API, image block before text, output_config.format
- Gemini: generateContent, inline_data, responseJsonSchema, key in x-goog-api-key
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote

import httpx

from app.config import REQUEST_TIMEOUT_SECONDS, RETRY_STATUSES
from app.errors import AppError
from app.inputs import checked_api_key, checked_model
from app.redact import redact

TIMEOUT = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=15.0)
Sleep = Callable[[float], None]


@dataclass(frozen=True)
class ImagePayload:
    mime_type: str
    data: bytes


class VisionProvider:
    """Base class. Subclasses fill in the URL, headers, body, and parser."""

    id = ""
    label = ""

    def extract(
        self,
        *,
        api_key: str,
        model: str,
        images: list[ImagePayload],
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        client: httpx.Client | None = None,
        sleep: Sleep = time.sleep,
    ) -> str:
        key = _checked_key(api_key)
        model_name = _checked_model(model)
        if not images:
            raise AppError("There is no page image to send.")
        url, headers, payload = self.build_request(
            api_key=key,
            model=model_name,
            images=images,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
        )
        # The key must not travel in the JSON body or the URL. Providers that
        # echo a request back in an error would otherwise leak it into the UI.
        serialized = json.dumps(payload)
        if key in serialized or key in url:
            raise AppError("Refusing to send a request that places the API key outside the header.")
        body = _post_for_json(url, headers, payload, key, client=client, sleep=sleep)
        return self.parse_response(body)

    def build_request(
        self,
        *,
        api_key: str,
        model: str,
        images: list[ImagePayload],
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        raise NotImplementedError

    def parse_response(self, body: dict[str, Any]) -> str:
        raise NotImplementedError


class OpenAICompatibleProvider(VisionProvider):
    """Shared body for xAI and OpenAI chat completions."""

    base_url = ""

    def build_request(
        self,
        *,
        api_key: str,
        model: str,
        images: list[ImagePayload],
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        content: list[dict[str, Any]] = []
        for image in images:
            encoded = base64.standard_b64encode(image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{image.mime_type};base64,{encoded}",
                        "detail": "high",
                    },
                }
            )
        content.append({"type": "text", "text": user_prompt})
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "supplier_quote",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return f"{self.base_url}/chat/completions", headers, payload

    def parse_response(self, body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            raise AppError("The provider returned no choices. Try again.")
        choice = choices[0]
        finish = str(choice.get("finish_reason") or "")
        message = choice.get("message") or {}
        refusal = message.get("refusal")
        if refusal:
            raise AppError("The model declined to read this document.")
        if finish == "length":
            raise AppError(
                "The model response was cut off. Use page-by-page mode, or a shorter quote."
            )
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AppError("The model returned an empty response. Try again.")
        return content


class XAIProvider(OpenAICompatibleProvider):
    id = "xai"
    label = "xAI (Grok)"
    base_url = "https://api.x.ai/v1"


class OpenAIProvider(OpenAICompatibleProvider):
    id = "openai"
    label = "OpenAI"
    base_url = "https://api.openai.com/v1"


class AnthropicProvider(VisionProvider):
    id = "anthropic"
    label = "Anthropic"

    def build_request(
        self,
        *,
        api_key: str,
        model: str,
        images: list[ImagePayload],
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        # Claude reads images more reliably when they come before the text.
        content: list[dict[str, Any]] = []
        for image in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": image.mime_type,
                        "data": base64.standard_b64encode(image.data).decode("ascii"),
                    },
                }
            )
        content.append({"type": "text", "text": user_prompt})
        payload = {
            "model": model,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": [{"role": "user", "content": content}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        return "https://api.anthropic.com/v1/messages", headers, payload

    def parse_response(self, body: dict[str, Any]) -> str:
        stop = str(body.get("stop_reason") or "")
        if stop == "max_tokens":
            raise AppError(
                "The model response was cut off. Use page-by-page mode, or a shorter quote."
            )
        if stop == "refusal":
            raise AppError("The model declined to read this document.")
        blocks = body.get("content") or []
        texts = [block.get("text", "") for block in blocks if block.get("type") == "text"]
        combined = "\n".join(text for text in texts if text).strip()
        if not combined:
            raise AppError("The model returned an empty response. Try again.")
        return combined


class GeminiProvider(VisionProvider):
    id = "gemini"
    label = "Google Gemini"

    def build_request(
        self,
        *,
        api_key: str,
        model: str,
        images: list[ImagePayload],
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for image in images:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": image.mime_type,
                        "data": base64.standard_b64encode(image.data).decode("ascii"),
                    }
                }
            )
        parts.append({"text": user_prompt})
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        # The key is a header. A query-string key would be written to proxy logs.
        safe_model = quote(model, safe="-._")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{safe_model}:generateContent"
        )
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
        return url, headers, payload

    def parse_response(self, body: dict[str, Any]) -> str:
        feedback = body.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise AppError("The provider blocked this document before the model could read it.")
        candidates = body.get("candidates") or []
        if not candidates:
            raise AppError("The provider returned no candidates. Try again.")
        candidate = candidates[0]
        finish = str(candidate.get("finishReason") or "")
        if finish == "MAX_TOKENS":
            raise AppError(
                "The model response was cut off. Use page-by-page mode, or a shorter quote."
            )
        if finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT"}:
            raise AppError("The provider stopped the response because of a safety filter.")
        parts = (candidate.get("content") or {}).get("parts") or []
        texts = [part.get("text", "") for part in parts if isinstance(part, dict)]
        combined = "\n".join(text for text in texts if text).strip()
        if not combined:
            raise AppError("The model returned an empty response. Try again.")
        return combined


_PROVIDER_TYPES: dict[str, type[VisionProvider]] = {
    "xai": XAIProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
}


def get_provider(provider_id: str) -> VisionProvider:
    try:
        return _PROVIDER_TYPES[provider_id]()
    except KeyError:
        raise AppError("Unknown provider. Choose a provider from the sidebar.") from None


def _checked_key(api_key: str) -> str:
    return checked_api_key(api_key)


def _checked_model(model: str) -> str:
    return checked_model(model)


def _post_for_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    api_key: str,
    *,
    client: httpx.Client | None,
    sleep: Sleep,
) -> dict[str, Any]:
    owns_client = client is None
    http = client or httpx.Client(timeout=TIMEOUT)
    try:
        response: httpx.Response | None = None
        for attempt in (1, 2):
            try:
                response = http.post(url, headers=headers, json=payload)
            except httpx.TimeoutException:
                raise AppError(
                    "The provider took too long to respond. Try again, or extract fewer pages at once."
                ) from None
            except httpx.TransportError:
                raise AppError(
                    "Could not reach the provider. Check the network connection and try again."
                ) from None
            if response.status_code in RETRY_STATUSES and attempt == 1:
                sleep(1.5)
                continue
            break
        assert response is not None
        return _decode_response(response, api_key)
    finally:
        if owns_client:
            http.close()


def _decode_response(response: httpx.Response, api_key: str) -> dict[str, Any]:
    status = response.status_code
    if status in {401, 403}:
        raise AppError(
            "The provider rejected the API key. Check that the key matches the selected provider."
        )
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = None

    if status >= 400:
        detail = _error_detail(body, response.text, api_key)
        if status == 404:
            raise AppError(f"The model was not found. Check the model name. {detail}".strip())
        if status == 429:
            raise AppError("The provider rate-limited the request. Wait a moment and try again.")
        raise AppError(f"The provider returned HTTP {status}. {detail}".strip())

    if not isinstance(body, dict):
        raise AppError("The provider returned a response that was not JSON.")
    return body


def _error_detail(body: object, raw_text: str, api_key: str) -> str:
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("status") or "")
        elif isinstance(error, str):
            message = error
        elif body.get("message"):
            message = str(body.get("message"))
    if not message:
        message = raw_text or ""
    message = redact(message, api_key).strip().replace("\n", " ")
    if len(message) > 400:
        message = message[:400].rstrip() + "…"
    return message
