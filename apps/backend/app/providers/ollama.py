"""Ollama transport helpers: health probe, timeouts, safe JSON repair, error mapping."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import urljoin

import httpx

from app.providers.errors import ProviderError, ProviderErrorClass

logger = logging.getLogger(__name__)

OLLAMA_DEFAULT_API_BASE = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "gemma3:4b"

# Bounded, predictable Ollama budgets (seconds).
OLLAMA_HEALTH_TIMEOUT = 10
OLLAMA_COMPLETION_TIMEOUT = 240
OLLAMA_JSON_TIMEOUT = 360
OLLAMA_TRANSPORT_RETRIES = 1
OLLAMA_CONTENT_RETRIES = 1

_SECRET_RE = re.compile(
    r"(?i)(?:(?:api[_-]?key|authorization)\s*[:=]\s*\S+)|(?:bearer\s+\S+)|(?:sk-[A-Za-z0-9\-._]+)"
)


def new_correlation_id() -> str:
    """Short opaque id for log correlation — no PII."""
    return uuid.uuid4().hex[:12]


def scrub_for_logs(text: str, *, limit: int = 200) -> str:
    """Redact secret-looking tokens; never intended for resume bodies at INFO."""
    if not text:
        return ""
    cleaned = _SECRET_RE.sub("[REDACTED]", text)
    if len(cleaned) > limit:
        return cleaned[:limit] + "…"
    return cleaned


def normalize_ollama_base(api_base: str | None) -> str:
    base = (api_base or OLLAMA_DEFAULT_API_BASE).strip().rstrip("/")
    return base or OLLAMA_DEFAULT_API_BASE


def _model_aliases(model: str) -> set[str]:
    """Match configured model against Ollama tag names (with/without :latest)."""
    name = model.strip()
    if name.startswith("ollama/"):
        name = name[len("ollama/") :]
    aliases = {name}
    if ":" not in name:
        aliases.add(f"{name}:latest")
    elif name.endswith(":latest"):
        aliases.add(name[: -len(":latest")])
    return aliases


async def probe_ollama_health(
    *,
    api_base: str | None,
    model: str,
    correlation_id: str | None = None,
    timeout: float = OLLAMA_HEALTH_TIMEOUT,
) -> dict[str, Any]:
    """Lightweight readiness probe via Ollama /api/tags (no completion, no key)."""
    cid = correlation_id or new_correlation_id()
    base = normalize_ollama_base(api_base)
    tags_url = urljoin(base + "/", "api/tags")
    result: dict[str, Any] = {
        "healthy": False,
        "provider": "ollama",
        "model": model,
        "correlation_id": cid,
        "probe": "tags",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(tags_url)
    except httpx.TimeoutException:
        result["error_code"] = ProviderErrorClass.TIMEOUT.value
        result["message"] = "Ollama health probe timed out"
        return result
    except httpx.HTTPError:
        logger.warning(
            "Ollama unavailable correlation_id=%s",
            cid,
            extra={"correlation_id": cid, "provider": "ollama"},
        )
        result["error_code"] = ProviderErrorClass.UNAVAILABLE.value
        result["message"] = "Ollama endpoint is unavailable"
        return result

    if response.status_code >= 500:
        result["error_code"] = ProviderErrorClass.UNAVAILABLE.value
        result["message"] = "Ollama endpoint returned a server error"
        return result
    if response.status_code == 404:
        result["error_code"] = ProviderErrorClass.UNAVAILABLE.value
        result["message"] = "Ollama tags endpoint not found"
        return result
    if response.status_code >= 400:
        result["error_code"] = ProviderErrorClass.INTERNAL.value
        result["message"] = "Ollama health probe failed"
        return result

    try:
        payload = response.json()
    except ValueError:
        result["error_code"] = ProviderErrorClass.INVALID_RESPONSE.value
        result["message"] = "Ollama returned a non-JSON health response"
        return result

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        result["error_code"] = ProviderErrorClass.INVALID_RESPONSE.value
        result["message"] = "Ollama tags response missing models list"
        return result

    available: set[str] = set()
    for entry in models:
        if isinstance(entry, dict):
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name.strip():
                available.add(name.strip())

    wanted = _model_aliases(model)
    if available and wanted.isdisjoint(available):
        result["error_code"] = ProviderErrorClass.MODEL_MISSING.value
        result["message"] = "Configured Ollama model is not available locally"
        return result

    # Empty tags list: daemon up but no models — treat as model missing when a model is required.
    if not available:
        result["error_code"] = ProviderErrorClass.MODEL_MISSING.value
        result["message"] = "No Ollama models are installed"
        return result

    result["healthy"] = True
    return result


def classify_provider_exception(exc: BaseException) -> ProviderErrorClass:
    """Map transport/library exceptions to stable provider error classes."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or "timeout" in name:
        return ProviderErrorClass.TIMEOUT
    if "timeout" in text or "timed out" in text:
        return ProviderErrorClass.TIMEOUT

    if any(
        token in text
        for token in (
            "model not found",
            "model_not_found",
            "does not exist",
            "unknown model",
            "not found: model",
            "404",
        )
    ) and any(token in text for token in ("model", "ollama")):
        return ProviderErrorClass.MODEL_MISSING

    if any(
        token in text
        for token in (
            "connection refused",
            "connecterror",
            "connect error",
            "name or service not known",
            "nodename nor servname",
            "failed to establish",
            "connection reset",
            "unreachable",
        )
    ) or "connect" in name:
        return ProviderErrorClass.UNAVAILABLE

    if any(
        token in text
        for token in ("rate limit", "ratelimit", "429", "capacity", "overloaded", "busy")
    ):
        return ProviderErrorClass.CAPACITY

    if isinstance(exc, (json.JSONDecodeError, ValueError)) or any(
        token in text
        for token in ("json", "invalid response", "empty response", "no json")
    ):
        return ProviderErrorClass.INVALID_RESPONSE

    return ProviderErrorClass.INTERNAL


def raise_classified(
    exc: BaseException,
    *,
    correlation_id: str,
    provider: str | None = None,
    model: str | None = None,
    message: str | None = None,
) -> None:
    """Raise ProviderError from an underlying exception (never re-raise secrets)."""
    error_class = classify_provider_exception(exc)
    safe_message = message or {
        ProviderErrorClass.UNAVAILABLE: "AI provider is unavailable",
        ProviderErrorClass.TIMEOUT: "AI provider timed out",
        ProviderErrorClass.MODEL_MISSING: "Configured model is missing",
        ProviderErrorClass.INVALID_RESPONSE: "AI provider returned an invalid response",
        ProviderErrorClass.CAPACITY: "AI provider is at capacity",
        ProviderErrorClass.INTERNAL: "AI provider failed internally",
    }[error_class]
    logger.warning(
        "provider_error class=%s correlation_id=%s provider=%s",
        error_class.value,
        correlation_id,
        provider,
        extra={
            "correlation_id": correlation_id,
            "error_class": error_class.value,
            "provider": provider,
            "model": model,
        },
    )
    raise ProviderError(
        error_class,
        safe_message,
        correlation_id=correlation_id,
        provider=provider,
        model=model,
    ) from exc


def repair_json_formatting(content: str) -> str:
    """Safe formatting-only repair: strip fences/noise; never invent JSON fields.

    Returns the best JSON-looking substring. Caller must json.loads + schema-validate.
    """
    text = (content or "").strip()
    if not text:
        raise ValueError("Empty response from LLM")

    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.lstrip().startswith(("json", "JSON")):
                text = text.lstrip()[4:]

    text = text.strip()
    if not text.startswith("{"):
        start = text.find("{")
        if start < 0:
            raise ValueError("No JSON found in response")
        text = text[start:]

    # Truncate to matching top-level object (formatting only).
    depth = 0
    in_string = False
    escape_next = False
    end_idx = -1
    for i, char in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if char == "\\":
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end_idx = i
                break
    if end_idx == -1:
        raise ValueError("Unbalanced JSON object in response")
    return text[: end_idx + 1]
