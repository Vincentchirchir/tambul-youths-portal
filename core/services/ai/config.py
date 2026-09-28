from dataclasses import dataclass

from django.conf import settings

from .errors import AI_CONFIGURATION_ERROR, AI_DISABLED, AIServiceError


@dataclass(frozen=True)
class AIRuntimeConfig:
    enabled: bool
    api_key: str
    model: str
    max_input_length: int
    max_output_tokens: int
    max_tool_calls: int
    request_timeout: float


def get_ai_runtime_config():
    enabled = bool(getattr(settings, "AI_ENABLED", False))
    if not enabled:
        raise AIServiceError(
            AI_DISABLED,
            "Tambul Assistant is currently disabled.",
            status=503,
        )

    api_key = getattr(settings, "OPENAI_API_KEY", "").strip()
    model = getattr(settings, "OPENAI_MODEL", "").strip()
    if not api_key or not model:
        raise AIServiceError(
            AI_CONFIGURATION_ERROR,
            "Tambul Assistant is not configured.",
            status=503,
        )

    return AIRuntimeConfig(
        enabled=enabled,
        api_key=api_key,
        model=model,
        max_input_length=int(getattr(settings, "AI_MAX_INPUT_LENGTH", 1000)),
        max_output_tokens=int(getattr(settings, "AI_MAX_OUTPUT_TOKENS", 600)),
        max_tool_calls=int(getattr(settings, "AI_MAX_TOOL_CALLS", 3)),
        request_timeout=float(getattr(settings, "AI_REQUEST_TIMEOUT", 20)),
    )
