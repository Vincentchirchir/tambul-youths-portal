import logging

from .errors import (
    AI_CONFIGURATION_ERROR,
    AI_PROVIDER_TIMEOUT,
    AI_PROVIDER_UNAVAILABLE,
    AIServiceError,
)

logger = logging.getLogger(__name__)


def get_openai_client(config):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIServiceError(
            AI_CONFIGURATION_ERROR,
            "Tambul Assistant is not configured.",
            status=503,
        ) from exc

    return OpenAI(api_key=config.api_key, timeout=config.request_timeout)


def _is_timeout_error(exc):
    timeout_classes = {
        "APITimeoutError",
        "TimeoutError",
        "ReadTimeout",
        "ConnectTimeout",
    }
    return exc.__class__.__name__ in timeout_classes


def create_openai_response(client, **kwargs):
    try:
        return client.responses.create(**kwargs)
    except Exception as exc:
        error_class = exc.__class__.__name__
        if _is_timeout_error(exc):
            logger.warning(
                "OpenAI Responses API timeout",
                extra={"provider_error_class": error_class},
            )
            raise AIServiceError(
                AI_PROVIDER_TIMEOUT,
                "Tambul Assistant is temporarily unavailable.",
                status=504,
            ) from exc

        logger.warning(
            "OpenAI Responses API unavailable",
            extra={"provider_error_class": error_class},
        )
        raise AIServiceError(
            AI_PROVIDER_UNAVAILABLE,
            "Tambul Assistant is temporarily unavailable.",
            status=503,
        ) from exc
