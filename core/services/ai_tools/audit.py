import logging

from core.services.ai_audit import build_ai_tool_audit_event

from .constants import SENSITIVE_CLASSIFICATIONS

logger = logging.getLogger(__name__)


def build_tool_audit_event(
    user,
    tool_name,
    authorization_result,
    *,
    sensitivity,
    success=False,
    error_category="",
):
    event = build_ai_tool_audit_event(
        user,
        tool_name,
        authorization_result,
        sensitive_data_accessed=sensitivity in SENSITIVE_CLASSIFICATIONS,
        success=success,
        error_category=error_category,
    ).to_dict()
    event["sensitivity"] = sensitivity
    return event


def record_tool_audit_event(event):
    logger.info(
        "AI tool invocation",
        extra={"ai_tool_audit": event},
    )


def audit_meta(event):
    return {
        "authorization_result": event["authorization_result"],
        "success": event["success"],
        "error_category": event["error_category"],
    }
