"""Typed provider error classes for Resume Lab AI calls."""

from enum import Enum


class ProviderErrorClass(str, Enum):
    """Stable error classes for provider/transport failures."""

    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    MODEL_MISSING = "model_missing"
    INVALID_RESPONSE = "invalid_response"
    CAPACITY = "capacity"
    INTERNAL = "internal"


class ProviderError(Exception):
    """Provider failure with a stable error class and correlation id (no PII)."""

    def __init__(
        self,
        error_class: ProviderErrorClass,
        message: str,
        *,
        correlation_id: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.error_class = error_class
        self.correlation_id = correlation_id
        self.provider = provider
        self.model = model
        super().__init__(message)

    @property
    def error_code(self) -> str:
        return self.error_class.value

    def to_dict(self) -> dict[str, str | None]:
        return {
            "error_code": self.error_code,
            "message": str(self),
            "correlation_id": self.correlation_id,
            "provider": self.provider,
            "model": self.model,
        }
