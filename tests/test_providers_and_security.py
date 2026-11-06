"""Provider requests, redaction, and the no-secrets rule."""

import asyncio
import json
import logging
import subprocess
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.errors import AppError
from app.providers import (
    AnthropicProvider,
    GeminiProvider,
    ImagePayload,
    OpenAIProvider,
    XAIProvider,
)
from app.http_security import SecurityHeadersMiddleware
from app.inputs import SessionSettings, checked_api_key, checked_model
from app.limits import ExtractionRateLimiter
from app.logging_setup import configure_logging
from app.redact import redact
from app.schema import QUOTE_JSON_SCHEMA

ROOT = Path(__file__).resolve().parent.parent
SECRET = "sk-test-secret-value-123456"
IMAGE = ImagePayload(mime_type="image/jpeg", data=b"\xff\xd8\xff\x00fake")
QUOTE = json.dumps({"supplier_name": "Acme", "supplier_name_confidence": 0.9, "line_items": []})


def test_openai_and_xai_keep_the_key_in_the_header() -> None:
    for provider, url in (
        (OpenAIProvider(), "https://api.openai.com/v1/chat/completions"),
        (XAIProvider(), "https://api.x.ai/v1/chat/completions"),
    ):
        built_url, headers, payload = provider.build_request(
            api_key=SECRET,
            model="demo-model",
            images=[IMAGE],
            system_prompt="Read the quote.",
            user_prompt="Extract it.",
            schema=QUOTE_JSON_SCHEMA,
        )
        assert built_url == url
        assert headers["Authorization"] == f"Bearer {SECRET}"
        assert SECRET not in built_url
        assert SECRET not in json.dumps(payload)
        content = payload["messages"][1]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["detail"] == "high"
        assert content[-1] == {"type": "text", "text": "Extract it."}
        assert payload["response_format"]["json_schema"]["strict"] is True


def test_anthropic_puts_images_before_text_and_uses_json_schema() -> None:
    url, headers, payload = AnthropicProvider().build_request(
        api_key=SECRET,
        model="claude-sonnet-4-6",
        images=[IMAGE],
        system_prompt="Read the quote.",
        user_prompt="Extract it.",
        schema=QUOTE_JSON_SCHEMA,
    )
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == SECRET
    assert "Authorization" not in headers
    assert SECRET not in json.dumps(payload)
    content = payload["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1]["type"] == "text"
    assert payload["output_config"]["format"]["type"] == "json_schema"


def test_gemini_key_is_a_header_not_a_query_parameter() -> None:
    url, headers, payload = GeminiProvider().build_request(
        api_key="AIzaSyTestKeyValue1234567890",
        model="gemini-2.5-flash",
        images=[IMAGE],
        system_prompt="Read the quote.",
        user_prompt="Extract it.",
        schema=QUOTE_JSON_SCHEMA,
    )
    assert "generateContent" in url
    assert "AIza" not in url
    assert "?" not in url
    assert headers["x-goog-api-key"].startswith("AIza")
    assert "AIza" not in json.dumps(payload)
    assert payload["generationConfig"]["responseMimeType"] == "application/json"


def test_rejected_key_message_does_not_echo_the_key() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"invalid key {SECRET}"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AppError) as caught:
        OpenAIProvider().extract(
            api_key=SECRET,
            model="gpt-4.1",
            images=[IMAGE],
            system_prompt="system",
            user_prompt="user",
            schema=QUOTE_JSON_SCHEMA,
            client=client,
            sleep=lambda _seconds: None,
        )
    assert SECRET not in str(caught.value)
    assert "rejected the API key" in str(caught.value)


def test_bad_request_detail_is_redacted() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": f"schema failed for {SECRET}"}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AppError) as caught:
        XAIProvider().extract(
            api_key=SECRET,
            model="grok-4.7",
            images=[IMAGE],
            system_prompt="system",
            user_prompt="user",
            schema=QUOTE_JSON_SCHEMA,
            client=client,
            sleep=lambda _seconds: None,
        )
    assert SECRET not in str(caught.value)
    assert "HTTP 400" in str(caught.value)


def test_rate_limit_is_retried_once() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": QUOTE}}]},
        )

    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    text = OpenAIProvider().extract(
        api_key=SECRET,
        model="gpt-4.1",
        images=[IMAGE],
        system_prompt="system",
        user_prompt="user",
        schema=QUOTE_JSON_SCHEMA,
        client=client,
        sleep=sleeps.append,
    )
    assert calls["count"] == 2
    assert sleeps == [1.5]
    assert "Acme" in text


def test_short_key_never_opens_a_request() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("The request should not be sent.")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(AppError):
        GeminiProvider().extract(
            api_key="too-short",
            model="gemini-2.5-flash",
            images=[IMAGE],
            system_prompt="system",
            user_prompt="user",
            schema=QUOTE_JSON_SCHEMA,
            client=client,
        )


def test_redact_covers_provider_prefixes_and_the_exact_secret() -> None:
    custom = "supplier-session-key-value"
    text = f"Bearer {SECRET} and {custom} and key={SECRET}"
    cleaned = redact(text, custom)
    assert SECRET not in cleaned
    assert custom not in cleaned
    assert "Bearer [redacted]" in cleaned


def test_application_code_does_not_load_secrets() -> None:
    banned = (
        "load_dotenv",
        "os.environ",
        "os.getenv",
        "st.cache",
        "st.secrets",
        "XAI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    )
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                offenders.append(f"{path.name}: {token}")
    assert offenders == []


def test_container_config_has_no_secret_channel() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8").lower()
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8").lower()
    for text in (compose, dockerfile):
        assert "env_file" not in text
        assert "api_key" not in text
        assert "xai-" not in text
        assert "sk-" not in text
    assert "secrets:" not in compose
    assert "poppler-utils" in dockerfile
    assert "user appuser" in dockerfile
    assert "app/asgi.py" in dockerfile
    assert 'user: "10001:10001"' in compose
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").lower()
    assert ".env" in dockerignore
    assert "*.pem" in dockerignore
    assert "*.key" in dockerignore


def test_api_key_rejects_header_injection_and_model_ids_stay_in_one_segment() -> None:
    with pytest.raises(AppError):
        checked_api_key("sk-test-secret\r\nX-Injected: 1-extra")
    with pytest.raises(AppError):
        checked_model("../gemini-2.5-flash")
    with pytest.raises(AppError):
        checked_model("demo model")


def test_invalid_session_settings_do_not_echo_the_key() -> None:
    secret = "super-secret-value-not-for-logs"
    with pytest.raises(ValidationError) as caught:
        SessionSettings(
            provider_id="xai",
            provider_label="xAI (Grok)",
            api_key=secret + " has spaces",
            model="grok-4.7",
            mode="combined",
            threshold=0.7,
        )
    assert secret not in str(caught.value)


def test_rate_limiter_enforces_interval_and_process_cap() -> None:
    now = {"value": 100.0}

    def clock() -> float:
        return now["value"]

    limiter = ExtractionRateLimiter(min_interval=3.0, max_per_minute=2, clock=clock)
    assert limiter.check("session-a") is None
    assert limiter.check("session-a") is not None
    now["value"] += 3.0
    assert limiter.check("session-b") is None
    now["value"] += 3.0
    assert "minute" in (limiter.check("session-c") or "")


def test_log_records_drop_secrets_tracebacks_and_paths(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging()
    logger = logging.getLogger("app.security_test")
    try:
        raise RuntimeError(f"failed at /tmp/internal/app.py with {SECRET}")
    except RuntimeError:
        logger.exception("saw %s", SECRET)
    text = caplog.text
    assert SECRET not in text
    assert "Traceback" not in text
    assert "/tmp/internal" not in text


def test_security_headers_are_added_to_http_responses() -> None:
    async def exercise() -> list[dict]:
        sent: list[dict] = []

        async def app(scope: dict, receive: object, send: object) -> None:
            del scope, receive
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive() -> dict:
            return {"type": "http.request", "body": b""}

        async def send(message: dict) -> None:
            sent.append(message)

        await SecurityHeadersMiddleware(app)({"type": "http"}, receive, send)
        return sent

    sent = asyncio.run(exercise())
    headers = dict(sent[0]["headers"])
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert b"frame-ancestors 'none'" in headers[b"content-security-policy"]


def test_asgi_entrypoint_imports_with_headers_and_debug_off() -> None:
    import importlib

    module = importlib.import_module("app.asgi")
    assert module.app._debug is False
    assert module._UI_SCRIPT.name == "main.py"
    assert any(item.cls.__name__ == "SecurityHeadersMiddleware" for item in module.app._user_middleware)


def test_env_files_and_private_keys_are_gitignored() -> None:
    ignored = subprocess.check_output(
        ["git", "check-ignore", ".env", ".env.production", "secrets/key.pem", "logs/app.log"],
        cwd=ROOT,
        text=True,
    )
    assert ".env" in ignored.split()
    assert "secrets/key.pem" in ignored.split()
    example = subprocess.run(["git", "check-ignore", ".env.example"], cwd=ROOT, capture_output=True)
    assert example.returncode != 0
