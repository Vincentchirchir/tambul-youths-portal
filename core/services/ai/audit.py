import logging

from core.models import AIAuditLog

logger = logging.getLogger(__name__)


def _usage_value(usage, *names):
    if not usage:
        return None
    for name in names:
        if isinstance(usage, dict):
            value = usage.get(name)
        else:
            value = getattr(usage, name, None)
        if value is not None:
            return int(value)
    return None


def extract_token_usage(response):
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    return {
        "input_tokens": _usage_value(usage, "input_tokens", "prompt_tokens"),
        "output_tokens": _usage_value(usage, "output_tokens", "completion_tokens"),
        "total_tokens": _usage_value(usage, "total_tokens"),
    }


def combine_token_usage(current, addition):
    combined = dict(current)
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = addition.get(key)
        if value is None:
            continue
        combined[key] = (combined.get(key) or 0) + value
    return combined


def record_ai_audit_log(
    *,
    request_id,
    user,
    event_type,
    success,
    tool_name="",
    sensitivity="",
    authorization_result="",
    error_category="",
    provider_response_id="",
    token_usage=None,
    duration_ms=None,
):
    token_usage = token_usage or {}
    try:
        return AIAuditLog.objects.create(
            request_id=request_id,
            event_type=event_type,
            user=user if getattr(user, "is_authenticated", False) else None,
            user_role=getattr(user, "role", ""),
            tool_name=tool_name or "",
            sensitivity=sensitivity or "",
            authorization_result=authorization_result or "",
            success=bool(success),
            error_category=error_category or "",
            provider_response_id=provider_response_id or "",
            input_tokens=token_usage.get("input_tokens"),
            output_tokens=token_usage.get("output_tokens"),
            total_tokens=token_usage.get("total_tokens"),
            duration_ms=duration_ms,
        )
    except Exception:
        logger.exception(
            "Failed to persist AI audit log",
            extra={
                "request_id": str(request_id),
                "event_type": event_type,
                "tool_name": tool_name,
                "error_category": error_category,
            },
        )
        return None
