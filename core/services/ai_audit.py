from dataclasses import asdict, dataclass

from django.utils import timezone


@dataclass(frozen=True)
class AIToolAuditEvent:
    user_id: int | None
    user_role: str
    timestamp: str
    tool_name: str
    authorization_result: str
    sensitive_data_accessed: bool
    success: bool
    error_category: str = ""

    def to_dict(self):
        return asdict(self)


PROPOSED_AI_AUDIT_MODEL_FIELDS = {
    "user": "ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=SET_NULL)",
    "user_role": "CharField(max_length=32)",
    "tool_name": "CharField(max_length=128)",
    "authorization_result": "CharField(max_length=32)",
    "sensitive_data_accessed": "BooleanField(default=False)",
    "success": "BooleanField(default=False)",
    "error_category": "CharField(max_length=64, blank=True)",
    "created_at": "DateTimeField(auto_now_add=True)",
}


def build_ai_tool_audit_event(
    user,
    tool_name,
    authorization_result,
    *,
    sensitive_data_accessed=False,
    success=False,
    error_category="",
):
    return AIToolAuditEvent(
        user_id=getattr(user, "id", None),
        user_role=getattr(user, "role", ""),
        timestamp=timezone.now().isoformat(),
        tool_name=tool_name,
        authorization_result=authorization_result,
        sensitive_data_accessed=bool(sensitive_data_accessed),
        success=bool(success),
        error_category=error_category,
    )
