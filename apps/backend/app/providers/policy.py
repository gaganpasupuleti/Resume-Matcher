"""Provider enablement policy for Code Quest local vs standalone."""

from app.config import settings
from app.providers.errors import ProviderError, ProviderErrorClass
from app.providers.ollama import new_correlation_id

# Paid/cloud adapters remain in the codebase but must not activate in CQ local mode.
CODEQUEST_ENABLED_PROVIDERS = frozenset({"ollama"})
KNOWN_PROVIDERS = frozenset(
    {"ollama", "openai", "anthropic", "gemini", "deepseek", "openrouter"}
)


def is_codequest_local_mode() -> bool:
    """True when Code Quest Lab local mode is active (Ollama-only policy)."""
    return bool(settings.codequest_local_mode)


def is_provider_enabled(provider: str) -> bool:
    """Return whether a provider may be used under the current policy."""
    normalized = (provider or "").strip().lower()
    if is_codequest_local_mode():
        return normalized in CODEQUEST_ENABLED_PROVIDERS
    return normalized in KNOWN_PROVIDERS


def assert_provider_allowed(
    provider: str,
    *,
    correlation_id: str | None = None,
) -> str:
    """Normalize and enforce provider policy.

    Raises ProviderError(unavailable) when a disabled/paid provider would activate
    under Code Quest local mode.
    """
    normalized = (provider or "").strip().lower()
    cid = correlation_id or new_correlation_id()
    if not normalized:
        raise ProviderError(
            ProviderErrorClass.INTERNAL,
            "LLM provider is not configured",
            correlation_id=cid,
            provider=normalized or None,
        )
    if not is_provider_enabled(normalized):
        raise ProviderError(
            ProviderErrorClass.UNAVAILABLE,
            "Provider is disabled for Code Quest local mode; use Ollama only",
            correlation_id=cid,
            provider=normalized,
        )
    return normalized
