import json
import logging
import uuid

from django.conf import settings
from django.http import JsonResponse
from django.views import View

from core.models import AIAuditLog
from core.services.ai import run_tambul_ai_chat
from core.services.ai.access import user_has_ai_pilot_access
from core.services.ai.audit import record_ai_audit_log
from core.services.ai.errors import (
    AI_PILOT_ACCESS_DENIED,
    AI_RATE_LIMITED,
    AI_RATE_LIMIT_UNAVAILABLE,
    AIServiceError,
)
from core.services.ai.rate_limit import consume_ai_rate_limit


logger = logging.getLogger(__name__)

FORBIDDEN_AI_REQUEST_FIELDS = {
    "api_key",
    "conversation",
    "history",
    "memory",
    "messages",
    "model",
    "openai_api_key",
    "openai_model",
    "previous_response_id",
    "provider_response_id",
    "user_id",
    "member_id",
    "role",
    "roles",
    "permission",
    "permissions",
    "capability",
    "capabilities",
    "sql",
    "tool",
    "tool_name",
}


def _error_response(code, message, *, status):
    return JsonResponse(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        },
        status=status,
    )


def _record_endpoint_ai_failure(user, error_category):
    record_ai_audit_log(
        request_id=uuid.uuid4(),
        user=user,
        event_type=AIAuditLog.EVENT_REQUEST,
        success=False,
        error_category=error_category,
    )


class AIChatView(View):
    http_method_names = ["post", "get", "head", "options"]

    def get(self, request, *args, **kwargs):
        return _error_response(
            "METHOD_NOT_ALLOWED",
            "Use POST for Tambul Assistant requests.",
            status=405,
        )

    def post(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return _error_response(
                "AUTHENTICATION_REQUIRED",
                "Authentication is required.",
                status=403,
            )

        if not user_has_ai_pilot_access(request.user):
            _record_endpoint_ai_failure(request.user, AI_PILOT_ACCESS_DENIED)
            return _error_response(
                AI_PILOT_ACCESS_DENIED,
                "Tambul Assistant is limited to approved pilot users.",
                status=403,
            )

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error_response(
                "INVALID_JSON",
                "Request body must be valid JSON.",
                status=400,
            )

        if not isinstance(payload, dict):
            return _error_response(
                "INVALID_JSON",
                "Request body must be a JSON object.",
                status=400,
            )

        payload_fields = {str(field).lower() for field in payload}
        forbidden = sorted(FORBIDDEN_AI_REQUEST_FIELDS.intersection(payload_fields))
        if forbidden:
            return _error_response(
                "UNSUPPORTED_FIELD",
                "Unsupported request field.",
                status=400,
            )

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            return _error_response(
                "INVALID_MESSAGE",
                "A non-empty message is required.",
                status=400,
            )

        message = message.strip()
        max_input_length = int(getattr(settings, "AI_MAX_INPUT_LENGTH", 1000))
        if len(message) > max_input_length:
            return _error_response(
                "MESSAGE_TOO_LONG",
                f"Message must be {max_input_length} characters or less.",
                status=400,
            )

        try:
            allowed, retry_after = consume_ai_rate_limit(request.user)
        except Exception:
            logger.warning("AI rate-limit check failed", exc_info=True)
            _record_endpoint_ai_failure(request.user, AI_RATE_LIMIT_UNAVAILABLE)
            return _error_response(
                AI_RATE_LIMIT_UNAVAILABLE,
                "Tambul Assistant is temporarily unavailable.",
                status=503,
            )
        if not allowed:
            _record_endpoint_ai_failure(request.user, AI_RATE_LIMITED)
            response = _error_response(
                AI_RATE_LIMITED,
                "Too many Tambul Assistant requests. Please try again later.",
                status=429,
            )
            response["Retry-After"] = str(retry_after)
            return response

        try:
            result = run_tambul_ai_chat(request.user, message)
        except AIServiceError as exc:
            return _error_response(exc.code, exc.message, status=exc.status)

        return JsonResponse(
            {
                "ok": True,
                "message": result.message,
                "request_id": result.request_id,
            }
        )
