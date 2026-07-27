"""Proof tests for Ollama provider hardening (Agent I)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.llm import LLMConfig, check_llm_health, complete, complete_json, get_llm_config
from app.providers.errors import ProviderError, ProviderErrorClass
from app.providers.ollama import (
    classify_provider_exception,
    probe_ollama_health,
    repair_json_formatting,
    scrub_for_logs,
)
from app.providers.policy import assert_provider_allowed, is_provider_enabled


def _ollama_config(**overrides: object) -> LLMConfig:
    data = {
        "provider": "ollama",
        "model": "gemma3:4b",
        "api_key": "",
        "api_base": "http://localhost:11434",
    }
    data.update(overrides)
    return LLMConfig(**data)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_healthy_ollama_tags_probe() -> None:
    response = httpx.Response(
        200,
        json={"models": [{"name": "gemma3:4b"}]},
        request=httpx.Request("GET", "http://localhost:11434/api/tags"),
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.providers.ollama.httpx.AsyncClient", return_value=mock_client):
        result = await probe_ollama_health(
            api_base="http://localhost:11434",
            model="gemma3:4b",
            correlation_id="abc123",
        )

    assert result["healthy"] is True
    assert result["provider"] == "ollama"
    assert result["correlation_id"] == "abc123"


@pytest.mark.asyncio
async def test_model_missing() -> None:
    response = httpx.Response(
        200,
        json={"models": [{"name": "llama3:8b"}]},
        request=httpx.Request("GET", "http://localhost:11434/api/tags"),
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.providers.ollama.httpx.AsyncClient", return_value=mock_client):
        result = await check_llm_health(_ollama_config())

    assert result["healthy"] is False
    assert result["error_code"] == ProviderErrorClass.MODEL_MISSING.value


@pytest.mark.asyncio
async def test_timeout_classified() -> None:
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.providers.ollama.httpx.AsyncClient", return_value=mock_client):
        result = await check_llm_health(_ollama_config())

    assert result["healthy"] is False
    assert result["error_code"] == ProviderErrorClass.TIMEOUT.value


@pytest.mark.asyncio
async def test_malformed_json_raises_invalid_response() -> None:
    choice = SimpleNamespace(message=SimpleNamespace(content="not json at all {{{"))
    response = SimpleNamespace(choices=[choice])
    router = MagicMock()
    router.acompletion = AsyncMock(return_value=response)

    with patch("app.llm.get_router", return_value=(router, _ollama_config())):
        with pytest.raises(ProviderError) as exc_info:
            await complete_json("parse this", retries=0)

    assert exc_info.value.error_class == ProviderErrorClass.INVALID_RESPONSE
    assert exc_info.value.correlation_id


@pytest.mark.asyncio
async def test_valid_completion() -> None:
    choice = SimpleNamespace(
        message=SimpleNamespace(content='```json\n{"ok": true, "name": "Ada"}\n```')
    )
    response = SimpleNamespace(choices=[choice])
    router = MagicMock()
    router.acompletion = AsyncMock(return_value=response)

    with patch("app.llm.get_router", return_value=(router, _ollama_config())):
        result = await complete_json("return json", retries=0)

    assert result == {"ok": True, "name": "Ada"}
    # Safe repair must not invent fields
    assert set(result.keys()) == {"ok", "name"}


@pytest.mark.asyncio
async def test_no_key_required_for_ollama_health() -> None:
    response = httpx.Response(
        200,
        json={"models": [{"name": "gemma3:4b"}]},
        request=httpx.Request("GET", "http://localhost:11434/api/tags"),
    )
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.providers.ollama.httpx.AsyncClient", return_value=mock_client):
        result = await check_llm_health(_ollama_config(api_key=""))

    assert result["healthy"] is True
    assert "api_key_missing" not in result.get("error_code", "")


def test_no_secret_logged(caplog: pytest.LogCaptureFixture) -> None:
    secret = "sk-secret-SHOULD-NOT-APPEAR"
    scrubbed = scrub_for_logs(f"api_key={secret} authorization: Bearer {secret}")
    assert secret not in scrubbed
    assert "REDACTED" in scrubbed

    with caplog.at_level(logging.WARNING):
        logging.getLogger("app.providers.ollama").warning(
            "provider_error class=%s detail=%s",
            "unavailable",
            scrub_for_logs(f"api_key={secret}"),
        )
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert secret not in joined


def test_codequest_local_disables_paid_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.providers.policy.settings.codequest_local_mode", True)
    assert is_provider_enabled("ollama") is True
    assert is_provider_enabled("openai") is False
    with pytest.raises(ProviderError) as exc_info:
        assert_provider_allowed("openai")
    assert exc_info.value.error_class == ProviderErrorClass.UNAVAILABLE


def test_standalone_still_allows_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.providers.policy.settings.codequest_local_mode", False)
    assert is_provider_enabled("openai") is True
    assert assert_provider_allowed("openai") == "openai"


def test_repair_json_formatting_strips_fence_only() -> None:
    raw = 'Here you go:\n```json\n{"a": 1}\n```\nthanks'
    assert repair_json_formatting(raw) == '{"a": 1}'


def test_classify_timeout_and_unavailable() -> None:
    assert classify_provider_exception(TimeoutError("x")) == ProviderErrorClass.TIMEOUT
    assert (
        classify_provider_exception(ConnectionError("connection refused"))
        == ProviderErrorClass.UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_complete_timeout_maps_to_provider_error() -> None:
    router = MagicMock()
    router.acompletion = AsyncMock(side_effect=TimeoutError("LLM timed out"))

    with patch("app.llm.get_router", return_value=(router, _ollama_config())):
        with pytest.raises(ProviderError) as exc_info:
            await complete("hello")

    assert exc_info.value.error_class == ProviderErrorClass.TIMEOUT


def test_get_llm_config_forces_ollama_in_codequest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.llm.is_codequest_local_mode", lambda: True)
    monkeypatch.setattr(
        "app.llm._load_stored_config",
        lambda: {"provider": "openai", "model": "gpt-4o", "api_key": "sk-x"},
    )
    monkeypatch.setattr("app.llm.settings.llm_provider", "openai")
    monkeypatch.setattr("app.llm.settings.llm_model", "gemma3:4b")
    monkeypatch.setattr("app.llm.settings.llm_api_base", "http://localhost:11434")
    monkeypatch.setattr("app.llm.settings.llm_api_key", "")

    config = get_llm_config()
    assert config.provider == "ollama"
    assert config.api_key == "sk-x" or config.api_key == ""  # ollama ignores key
