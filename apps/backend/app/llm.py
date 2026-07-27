"""LiteLLM wrapper for multi-provider AI support."""

import json
import logging
import threading
from typing import Any

import litellm
from litellm import Router
from litellm.router import RetryPolicy
from pydantic import BaseModel

from app.config import settings
from app.providers.errors import ProviderError, ProviderErrorClass
from app.providers.ollama import (
    OLLAMA_COMPLETION_TIMEOUT,
    OLLAMA_CONTENT_RETRIES,
    OLLAMA_JSON_TIMEOUT,
    OLLAMA_TRANSPORT_RETRIES,
    classify_provider_exception,
    new_correlation_id,
    probe_ollama_health,
    raise_classified,
    repair_json_formatting,
    scrub_for_logs,
)
from app.providers.policy import assert_provider_allowed, is_codequest_local_mode

LITELLM_LOGGER_NAMES = ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy")


def _configure_litellm_logging() -> None:
    """Align LiteLLM logger levels with application settings."""
    numeric_level = getattr(logging, settings.log_llm, logging.WARNING)
    for logger_name in LITELLM_LOGGER_NAMES:
        logging.getLogger(logger_name).setLevel(numeric_level)


_configure_litellm_logging()

# LLM timeout configuration (seconds) - base values (non-Ollama)
LLM_TIMEOUT_HEALTH_CHECK = 120
LLM_TIMEOUT_COMPLETION = 120
LLM_TIMEOUT_JSON = 180  # JSON completions may take longer

# JSON-010: JSON extraction safety limits
MAX_JSON_EXTRACTION_RECURSION = 10
MAX_JSON_CONTENT_SIZE = 1024 * 1024  # 1MB


class LLMConfig(BaseModel):
    """LLM configuration model."""

    provider: str
    model: str
    api_key: str
    api_base: str | None = None


def _normalize_api_base(provider: str, api_base: str | None) -> str | None:
    """Normalize api_base for LiteLLM provider-specific expectations.

    When using proxies/aggregators, users often paste a base URL that already
    includes a version segment (e.g., `/v1`). Some LiteLLM provider handlers
    append those segments internally, which can lead to duplicated paths like
    `/v1/v1/...` and cause 404s.
    """
    if not api_base:
        return None

    base = api_base.strip()
    if not base:
        return None

    base = base.rstrip("/")

    # Anthropic handler appends '/v1/messages'. If base already ends with '/v1',
    # strip it to avoid '/v1/v1/messages'.
    if provider == "anthropic" and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")

    # Gemini handler appends '/v1/models/...'. If base already ends with '/v1',
    # strip it to avoid '/v1/v1/models/...'.
    if provider == "gemini" and base.endswith("/v1"):
        base = base[: -len("/v1")].rstrip("/")

    return base or None


def _extract_text_parts(value: Any, depth: int = 0, max_depth: int = 10) -> list[str]:
    """Recursively extract text segments from nested response structures."""
    if depth >= max_depth:
        return []

    if value is None:
        return []

    if isinstance(value, str):
        return [value]

    if isinstance(value, list):
        parts: list[str] = []
        next_depth = depth + 1
        for item in value:
            parts.extend(_extract_text_parts(item, next_depth, max_depth))
        return parts

    if isinstance(value, dict):
        next_depth = depth + 1
        if "text" in value:
            return _extract_text_parts(value.get("text"), next_depth, max_depth)
        if "content" in value:
            return _extract_text_parts(value.get("content"), next_depth, max_depth)
        if "value" in value:
            return _extract_text_parts(value.get("value"), next_depth, max_depth)
        return []

    next_depth = depth + 1
    if hasattr(value, "text"):
        return _extract_text_parts(getattr(value, "text"), next_depth, max_depth)
    if hasattr(value, "content"):
        return _extract_text_parts(getattr(value, "content"), next_depth, max_depth)

    return []


def _join_text_parts(parts: list[str]) -> str | None:
    """Join text parts with newlines, filtering empty strings."""
    joined = "\n".join(part for part in parts if part).strip()
    return joined or None


def _extract_message_text(message: Any) -> str | None:
    """Extract plain text from a LiteLLM message object across providers."""
    content: Any = None

    if hasattr(message, "content"):
        content = message.content
    elif isinstance(message, dict):
        content = message.get("content")

    return _join_text_parts(_extract_text_parts(content))


def _extract_choice_text(choice: Any) -> str | None:
    """Extract plain text from a LiteLLM choice object."""
    message: Any = None
    if hasattr(choice, "message"):
        message = choice.message
    elif isinstance(choice, dict):
        message = choice.get("message")

    content = _extract_message_text(message)
    if content:
        return content

    if hasattr(choice, "text"):
        content = _join_text_parts(_extract_text_parts(getattr(choice, "text")))
        if content:
            return content
    if isinstance(choice, dict) and "text" in choice:
        content = _join_text_parts(_extract_text_parts(choice.get("text")))
        if content:
            return content

    if hasattr(choice, "delta"):
        content = _join_text_parts(_extract_text_parts(getattr(choice, "delta")))
        if content:
            return content
    if isinstance(choice, dict) and "delta" in choice:
        content = _join_text_parts(_extract_text_parts(choice.get("delta")))
        if content:
            return content

    return None


def _to_code_block(content: str | None, language: str = "text") -> str:
    """Wrap content in a markdown code block for client display."""
    text = (content or "").strip()
    if not text:
        text = "<empty>"
    return f"```{language}\n{text}\n```"


def _load_stored_config() -> dict:
    """Load config from config.json file."""
    config_path = settings.config_path
    if config_path.exists():
        try:
            return json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


_PROVIDER_KEY_MAP: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "openrouter": "openrouter",
    "deepseek": "deepseek",
    "ollama": "ollama",
}


def resolve_api_key(stored: dict, provider: str) -> str:
    """Resolve the effective API key from stored config.

    Priority: top-level api_key > api_keys[provider] > env/settings default.
    """
    api_key = stored.get("api_key", "")
    if not api_key:
        api_keys = stored.get("api_keys", {})
        if not isinstance(api_keys, dict):
            api_keys = {}
        config_provider = _PROVIDER_KEY_MAP.get(provider, provider)
        api_key = api_keys.get(config_provider, settings.llm_api_key)
    return api_key


def get_llm_config() -> LLMConfig:
    """Get current LLM configuration.

    Code Quest local mode forces Ollama and rejects paid providers.
    """
    stored = _load_stored_config()
    provider = stored.get("provider", settings.llm_provider)
    if is_codequest_local_mode():
        provider = "ollama"
    provider = assert_provider_allowed(provider)
    api_key = resolve_api_key(stored, provider)
    model = stored.get("model", settings.llm_model)
    api_base = stored.get("api_base", settings.llm_api_base)
    if provider == "ollama" and not api_base:
        api_base = settings.llm_api_base or "http://localhost:11434"

    return LLMConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        api_base=api_base,
    )


def get_model_name(config: LLMConfig) -> str:
    """Convert provider/model to LiteLLM format."""
    provider_prefixes = {
        "openai": "",
        "anthropic": "anthropic/",
        "openrouter": "openrouter/",
        "gemini": "gemini/",
        "deepseek": "deepseek/",
        "ollama": "ollama/",
    }

    prefix = provider_prefixes.get(config.provider, "")

    if config.provider == "openrouter":
        if config.model.startswith("openrouter/"):
            return config.model
        return f"openrouter/{config.model}"

    known_prefixes = ["openrouter/", "anthropic/", "gemini/", "deepseek/", "ollama/"]
    if any(config.model.startswith(p) for p in known_prefixes):
        return config.model

    return f"{prefix}{config.model}" if prefix else config.model


_router: Router | None = None
_router_config_key: str = ""
_router_lock = threading.Lock()


def _config_fingerprint(config: LLMConfig) -> str:
    """Generate a fingerprint to detect config changes (raw key never stored)."""
    key_hash = hash(config.api_key) if config.api_key else 0
    return f"{config.provider}|{config.model}|{key_hash}|{config.api_base}"


def _build_router(config: LLMConfig) -> Router:
    """Build a LiteLLM Router with error-type retry policies."""
    model_name = get_model_name(config)

    litellm_params: dict[str, Any] = {"model": model_name}
    if config.api_key:
        litellm_params["api_key"] = config.api_key
    api_base = _normalize_api_base(config.provider, config.api_base)
    if api_base:
        litellm_params["api_base"] = api_base

    # Ollama: small bounded transport retries (no paid fallback amplification).
    if config.provider == "ollama":
        num_retries = OLLAMA_TRANSPORT_RETRIES
        retry_policy = RetryPolicy(
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            TimeoutErrorRetries=1,
            RateLimitErrorRetries=1,
            ContentPolicyViolationErrorRetries=0,
            InternalServerErrorRetries=1,
        )
    else:
        num_retries = 3
        retry_policy = RetryPolicy(
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            TimeoutErrorRetries=2,
            RateLimitErrorRetries=3,
            ContentPolicyViolationErrorRetries=0,
            InternalServerErrorRetries=2,
        )

    return Router(
        model_list=[
            {
                "model_name": "primary",
                "litellm_params": litellm_params,
            }
        ],
        num_retries=num_retries,
        retry_policy=retry_policy,
        # Cooldowns disabled: single deployment, no paid fallback.
        disable_cooldowns=True,
    )


def get_router(config: LLMConfig | None = None) -> tuple[Router, LLMConfig]:
    """Get or rebuild the LiteLLM Router."""
    global _router, _router_config_key

    if config is None:
        config = get_llm_config()
    else:
        assert_provider_allowed(config.provider)

    key = _config_fingerprint(config)
    with _router_lock:
        if _router is None or _router_config_key != key:
            _router = _build_router(config)
            _router_config_key = key
            logging.info(
                "LiteLLM Router rebuilt for %s/%s",
                config.provider,
                config.model,
            )
        router = _router

    return router, config


def _supports_temperature(provider: str, model: str) -> bool:
    """Return whether passing `temperature` is supported for this model/provider."""
    _ = provider
    model_lower = model.lower()
    if "gpt-5" in model_lower:
        return False
    return True


def _get_reasoning_effort(provider: str, model: str) -> str | None:
    """Return a default reasoning_effort for models that require it."""
    _ = provider
    model_lower = model.lower()
    if "gpt-5" in model_lower:
        return "minimal"
    return None


async def check_llm_health(
    config: LLMConfig | None = None,
    *,
    include_details: bool = False,
    test_prompt: str | None = None,
) -> dict[str, Any]:
    """Check if the LLM provider is accessible and working."""
    cid = new_correlation_id()
    if config is None:
        config = get_llm_config()
    else:
        try:
            assert_provider_allowed(config.provider, correlation_id=cid)
        except ProviderError as exc:
            return {
                "healthy": False,
                "provider": config.provider,
                "model": config.model,
                "error_code": exc.error_code,
                "correlation_id": cid,
                "message": str(exc),
            }

    # Ollama never requires an API key.
    if config.provider != "ollama" and not config.api_key:
        return {
            "healthy": False,
            "provider": config.provider,
            "model": config.model,
            "error_code": "api_key_missing",
            "correlation_id": cid,
        }

    # Lightweight Ollama readiness (tags + model presence) — no completion call.
    if config.provider == "ollama" and not test_prompt:
        probe = await probe_ollama_health(
            api_base=config.api_base,
            model=config.model,
            correlation_id=cid,
        )
        if include_details and not probe.get("healthy"):
            probe["test_prompt"] = _to_code_block(None)
            probe["model_output"] = _to_code_block(None)
        return probe

    model_name = get_model_name(config)
    prompt = test_prompt or "Hi"

    try:
        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "api_key": config.api_key,
            "api_base": _normalize_api_base(config.provider, config.api_base),
            "timeout": LLM_TIMEOUT_HEALTH_CHECK,
        }
        reasoning_effort = _get_reasoning_effort(config.provider, model_name)
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        response = await litellm.acompletion(**kwargs)
        content = _extract_choice_text(response.choices[0])
        if not content:
            message = response.choices[0].message
            has_reasoning = getattr(message, "reasoning_content", None) or getattr(
                message, "thinking", None
            )
            if not has_reasoning:
                logging.warning(
                    "LLM health check returned empty content correlation_id=%s",
                    cid,
                    extra={
                        "provider": config.provider,
                        "model": config.model,
                        "correlation_id": cid,
                    },
                )
                result: dict[str, Any] = {
                    "healthy": False,
                    "provider": config.provider,
                    "model": config.model,
                    "response_model": response.model if response else None,
                    "error_code": ProviderErrorClass.INVALID_RESPONSE.value,
                    "correlation_id": cid,
                    "message": "LLM returned empty response",
                }
                if include_details:
                    result["test_prompt"] = _to_code_block(prompt)
                    result["model_output"] = _to_code_block(None)
                return result

        result = {
            "healthy": True,
            "provider": config.provider,
            "model": config.model,
            "response_model": response.model if response else None,
            "correlation_id": cid,
        }
        if include_details:
            result["test_prompt"] = _to_code_block(prompt)
            result["model_output"] = _to_code_block(content)
        return result
    except Exception as e:
        error_class = classify_provider_exception(e)
        logging.exception(
            "LLM health check failed correlation_id=%s class=%s",
            cid,
            error_class.value,
            extra={
                "provider": config.provider,
                "model": config.model,
                "correlation_id": cid,
                "error_class": error_class.value,
            },
        )

        message = scrub_for_logs(str(e))
        error_code = error_class.value
        if "404" in message and "/v1/v1/" in message:
            error_code = "duplicate_v1_path"
        elif error_class == ProviderErrorClass.INTERNAL and "404" in message:
            error_code = "not_found_404"
        elif "<!doctype html" in message.lower() or "<html" in message.lower():
            error_code = "html_response"
        result = {
            "healthy": False,
            "provider": config.provider,
            "model": config.model,
            "error_code": error_code,
            "correlation_id": cid,
        }
        if include_details:
            result["test_prompt"] = _to_code_block(prompt)
            result["model_output"] = _to_code_block(None)
            result["error_detail"] = _to_code_block(message)
        return result


async def complete(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
) -> str:
    """Make a completion request to the LLM."""
    cid = new_correlation_id()
    router, config = get_router(config)
    model_name = get_model_name(config)

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        timeout = (
            OLLAMA_COMPLETION_TIMEOUT
            if config.provider == "ollama"
            else LLM_TIMEOUT_COMPLETION
        )
        kwargs: dict[str, Any] = {
            "model": "primary",
            "messages": messages,
            "max_tokens": max_tokens,
            "timeout": timeout,
        }
        if _supports_temperature(config.provider, model_name):
            kwargs["temperature"] = temperature
        reasoning_effort = _get_reasoning_effort(config.provider, model_name)
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        response = await router.acompletion(**kwargs)

        content = _extract_choice_text(response.choices[0])
        if not content:
            raise ProviderError(
                ProviderErrorClass.INVALID_RESPONSE,
                "Empty response from LLM",
                correlation_id=cid,
                provider=config.provider,
                model=config.model,
            )
        return content
    except ProviderError:
        raise
    except Exception as e:
        logging.error(
            "LLM completion failed correlation_id=%s model=%s",
            cid,
            model_name,
            extra={
                "correlation_id": cid,
                "model": model_name,
                "provider": config.provider,
            },
        )
        raise_classified(
            e,
            correlation_id=cid,
            provider=config.provider,
            model=config.model,
        )
        raise  # pragma: no cover


def _supports_json_mode(model_name: str) -> bool:
    """Check if the model supports JSON mode via LiteLLM's model registry."""
    try:
        info = litellm.get_model_info(model=model_name)
        supported_params = info.get("supported_openai_params", [])
        return "response_format" in supported_params
    except Exception:
        logging.debug(
            "Model %s not in LiteLLM registry, skipping JSON mode", model_name
        )
        return False


def _appears_truncated(data: dict) -> bool:
    """LLM-001: Check if JSON data appears to be truncated."""
    if not isinstance(data, dict):
        return False

    suspicious_empty_arrays = ["workExperience", "education", "skills"]
    for key in suspicious_empty_arrays:
        if key in data and data[key] == []:
            logging.warning(
                "Possible truncation detected: '%s' is empty",
                key,
            )
            return True

    return False


def _get_retry_temperature(attempt: int, base_temp: float = 0.1) -> float:
    """LLM-002: Get temperature for retry attempt - increases with each retry."""
    temperatures = [base_temp, 0.3, 0.5, 0.7]
    return temperatures[min(attempt, len(temperatures) - 1)]


def _calculate_timeout(
    operation: str,
    max_tokens: int = 4096,
    provider: str = "openai",
) -> int:
    """LLM-005: Calculate adaptive timeout based on operation and parameters."""
    if provider == "ollama":
        if operation == "json":
            return OLLAMA_JSON_TIMEOUT
        return OLLAMA_COMPLETION_TIMEOUT

    base_timeouts = {
        "health_check": LLM_TIMEOUT_HEALTH_CHECK,
        "completion": LLM_TIMEOUT_COMPLETION,
        "json": LLM_TIMEOUT_JSON,
    }

    base = base_timeouts.get(operation, LLM_TIMEOUT_COMPLETION)
    token_factor = max(1.0, max_tokens / 4096)
    provider_factors = {
        "openai": 1.0,
        "anthropic": 1.2,
        "openrouter": 1.5,
        "ollama": 2.0,
    }
    provider_factor = provider_factors.get(provider, 1.0)
    return int(base * token_factor * provider_factor)


def get_resume_parse_timeout(retries: int = 2) -> float:
    """Timeout for resume JSON parsing (must exceed per-attempt LLM timeout × attempts)."""
    config = get_llm_config()
    per_attempt = _calculate_timeout("json", 4096, config.provider)
    return float(per_attempt * (retries + 1) + 30)


def _extract_json(content: str, _depth: int = 0) -> str:
    """Extract JSON via safe formatting repair only — never invent fields."""
    _ = _depth
    if len(content) > MAX_JSON_CONTENT_SIZE:
        raise ValueError(
            f"Content too large for JSON extraction: {len(content)} bytes"
        )
    try:
        return repair_json_formatting(content)
    except ValueError:
        logging.error(
            "Could not extract JSON from response format. Content length=%d",
            len(content) if content else 0,
        )
        raise


def get_json_content_retries(provider: str | None = None) -> int:
    """App-level content retries for malformed/truncated JSON (not transport)."""
    resolved = provider or get_llm_config().provider
    return OLLAMA_CONTENT_RETRIES if resolved == "ollama" else 2


async def complete_json(
    prompt: str,
    system_prompt: str | None = None,
    config: LLMConfig | None = None,
    max_tokens: int = 4096,
    retries: int | None = None,
) -> dict[str, Any]:
    """Make a completion request expecting JSON response.

    Repairs formatting only; does not invent schema fields. Caller must
    schema-validate (e.g. ResumeData).
    """
    cid = new_correlation_id()
    router, config = get_router(config)
    model_name = get_model_name(config)
    if retries is None:
        retries = get_json_content_retries(config.provider)

    json_system = (
        system_prompt or ""
    ) + "\n\nYou must respond with valid JSON only. No explanations, no markdown."
    messages = [
        {"role": "system", "content": json_system},
        {"role": "user", "content": prompt},
    ]

    use_json_mode = _supports_json_mode(model_name)

    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            kwargs: dict[str, Any] = {
                "model": "primary",
                "messages": messages,
                "max_tokens": max_tokens,
                "timeout": _calculate_timeout("json", max_tokens, config.provider),
            }
            if _supports_temperature(config.provider, model_name):
                kwargs["temperature"] = _get_retry_temperature(attempt)
            reasoning_effort = _get_reasoning_effort(config.provider, model_name)
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort

            if use_json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            response = await router.acompletion(**kwargs)
            content = _extract_choice_text(response.choices[0])

            if not content:
                raise ValueError("Empty response from LLM")

            # DEBUG only — never log resume/prompt bodies at INFO by default.
            logging.debug(
                "LLM JSON response attempt=%d correlation_id=%s length=%d",
                attempt + 1,
                cid,
                len(content),
            )

            json_str = _extract_json(content)
            result = json.loads(json_str)
            if not isinstance(result, dict):
                raise ValueError("JSON root must be an object")

            if _appears_truncated(result):
                if attempt < retries:
                    logging.warning(
                        "Parsed JSON appears truncated (attempt %d/%d) correlation_id=%s",
                        attempt + 1,
                        retries + 1,
                        cid,
                        extra={"correlation_id": cid},
                    )
                    messages[-1]["content"] = (
                        prompt
                        + "\n\nIMPORTANT: Output the COMPLETE JSON object with ALL sections including personalInfo. Do not truncate."
                    )
                    continue
                logging.warning(
                    "Parsed JSON appears truncated on final attempt correlation_id=%s",
                    cid,
                    extra={"correlation_id": cid},
                )

            return result

        except json.JSONDecodeError as e:
            last_error = e
            logging.warning(
                "JSON parse failed attempt=%d correlation_id=%s",
                attempt + 1,
                cid,
                extra={"correlation_id": cid},
            )
            if attempt < retries:
                messages[-1]["content"] = (
                    prompt
                    + "\n\nIMPORTANT: Output ONLY a valid JSON object. Start with { and end with }."
                )
                continue
            raise ProviderError(
                ProviderErrorClass.INVALID_RESPONSE,
                f"Failed to parse JSON after {retries + 1} attempts",
                correlation_id=cid,
                provider=config.provider,
                model=config.model,
            ) from e

        except ValueError as e:
            last_error = e
            logging.warning(
                "Content extraction failed attempt=%d correlation_id=%s",
                attempt + 1,
                cid,
                extra={"correlation_id": cid},
            )
            if attempt < retries:
                continue
            raise ProviderError(
                ProviderErrorClass.INVALID_RESPONSE,
                "AI provider returned an invalid response",
                correlation_id=cid,
                provider=config.provider,
                model=config.model,
            ) from e

        except ProviderError:
            raise

        except Exception as e:
            raise_classified(
                e,
                correlation_id=cid,
                provider=config.provider,
                model=config.model,
            )

    raise ProviderError(
        ProviderErrorClass.INVALID_RESPONSE,
        f"Failed after {retries + 1} attempts: {last_error}",
        correlation_id=cid,
        provider=config.provider,
        model=config.model,
    )
