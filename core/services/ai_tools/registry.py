from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from core.models import MeetingNote

from .audit import audit_meta, build_tool_audit_event, record_tool_audit_event
from .constants import (
    AUTHENTICATION_REQUIRED,
    AUTHORIZATION_ALLOWED,
    AUTHORIZATION_DENIED,
    AUTHORIZATION_NOT_AUTHENTICATED,
    AUTHORIZATION_UNSUPPORTED,
    INTERNAL_ERROR,
    INVALID_ARGUMENT,
    NOT_FOUND,
    PERMISSION_DENIED,
    SENSITIVITY_COMMITTEE_PRIVATE,
    SENSITIVITY_FINANCIAL_AGGREGATE,
    SENSITIVITY_FINANCIAL_PRIVATE,
    SENSITIVITY_LOW,
    UNSUPPORTED_REQUEST,
)
from .schemas import AIToolArgument, AIToolDefinition, AIToolSchema
from . import tools
from .validators import (
    MAX_GROUP_RECORD_LIMIT,
    MAX_MEETING_LIMIT,
    MAX_MEMBER_RECORD_LIMIT,
    ToolValidationError,
)

SCHEMA_VERSION = "1.0"


def _argument(name, type_name, required, description, *, enum=(), maximum=None):
    return AIToolArgument(
        name=name,
        type=type_name,
        required=required,
        description=description,
        enum=tuple(enum),
        maximum=maximum,
    )


def _definition(
    name,
    description,
    arguments,
    returns,
    sensitivity,
    permission,
    handler,
):
    return AIToolDefinition(
        schema=AIToolSchema(
            name=name,
            description=description,
            arguments=tuple(arguments),
            returns=returns,
            sensitivity=sensitivity,
            permission=permission,
        ),
        handler=handler,
        write_capable=False,
    )


_TOOL_DEFINITIONS = (
    _definition(
        "get_current_user_context",
        "Use for safe authenticated-user context and capability flags; never for private record values.",
        (),
        {
            "display_name": "string",
            "role": "string",
            "capabilities": "object<boolean>",
        },
        SENSITIVITY_LOW,
        "authenticated user",
        tools.get_current_user_context,
    ),
    _definition(
        "get_my_contribution_status",
        "Must use whenever the user asks about their own contribution payment status, balance, or history.",
        (
            _argument("year", "integer", False, "Contribution year filter."),
            _argument("month", "integer", False, "Contribution month filter, 1-12."),
            _argument(
                "limit",
                "integer",
                False,
                "Maximum contribution records to return.",
                maximum=MAX_MEMBER_RECORD_LIMIT,
            ),
        ),
        {
            "total_paid": "money string",
            "balance_due": "money string",
            "record_count": "integer",
            "returned_count": "integer",
            "truncated": "boolean",
            "records": [
                {
                    "month": "date string",
                    "amount": "money string",
                    "expected_amount": "money string",
                    "balance_due": "money string",
                    "status": "string",
                    "status_label": "string",
                }
            ],
        },
        SENSITIVITY_FINANCIAL_PRIVATE,
        "own financial information",
        tools.get_my_contribution_status,
    ),
    _definition(
        "get_my_loan_summary",
        "Must use whenever the user asks about their own loan status, current loan balance, repayments, or loan history.",
        (
            _argument(
                "limit",
                "integer",
                False,
                "Maximum loan records to return.",
                maximum=10,
            ),
        ),
        {
            "active_loan_count": "integer",
            "total_principal": "money string",
            "total_paid": "money string",
            "outstanding_balance": "money string",
            "status_counts": "object<integer>",
            "loan_record_count": "integer",
            "returned_count": "integer",
            "truncated": "boolean",
            "loans": [
                {
                    "amount": "money string",
                    "interest": "money string",
                    "total_paid_so_far": "money string",
                    "current_balance": "money string",
                    "status": "string",
                    "status_label": "string",
                    "repayment_status": "string",
                    "repayment_status_label": "string",
                    "loan_date": "date string",
                    "due_date": "date string",
                }
            ],
        },
        SENSITIVITY_FINANCIAL_PRIVATE,
        "own financial information",
        tools.get_my_loan_summary,
    ),
    _definition(
        "get_monthly_contribution_summary",
        "Must use whenever an authorized official asks for monthly group contribution performance, totals, counts, or collection rate.",
        (
            _argument("year", "integer", True, "Contribution year."),
            _argument("month", "integer", True, "Contribution month, 1-12."),
        ),
        {
            "period": "date string",
            "active_member_count": "integer",
            "expected_amount": "money string",
            "collected_amount": "money string",
            "outstanding_amount": "money string",
            "collection_percentage": "number",
            "paid_member_count": "integer",
            "unpaid_or_late_count": "integer",
            "status_counts": "object<integer>",
        },
        SENSITIVITY_FINANCIAL_AGGREGATE,
        "contribution summary role",
        tools.get_monthly_contribution_summary,
    ),
    _definition(
        "get_outstanding_contributions",
        "Must use whenever an authorized official asks who has outstanding, unpaid, late, or partially paid contributions for a month.",
        (
            _argument("year", "integer", True, "Contribution year."),
            _argument("month", "integer", True, "Contribution month, 1-12."),
            _argument(
                "limit",
                "integer",
                False,
                "Maximum member rows to return.",
                maximum=MAX_GROUP_RECORD_LIMIT,
            ),
        ),
        {
            "period": "date string",
            "outstanding_count": "integer",
            "returned_count": "integer",
            "truncated": "boolean",
            "members": [
                {
                    "display_name": "string",
                    "amount_paid": "money string",
                    "expected_amount": "money string",
                    "balance_due": "money string",
                    "status": "string",
                }
            ],
        },
        SENSITIVITY_FINANCIAL_PRIVATE,
        "individual outstanding contribution role",
        tools.get_outstanding_contributions,
    ),
    _definition(
        "get_contribution_summary_range",
        "Must use whenever an authorized committee user asks for group contribution totals across a date range, year-to-date period, or January-to-now period.",
        (
            _argument("start_year", "integer", True, "Start contribution year."),
            _argument("start_month", "integer", True, "Start contribution month, 1-12."),
            _argument("end_year", "integer", True, "End contribution year."),
            _argument("end_month", "integer", True, "End contribution month, 1-12."),
        ),
        {
            "start_period": "date string",
            "end_period": "date string",
            "period_count": "integer",
            "expected_amount": "money string",
            "collected_amount": "money string",
            "outstanding_amount": "money string",
            "collection_percentage": "number",
            "expected_member_periods": "integer",
            "fully_paid_member_periods": "integer",
            "unpaid_or_late_member_periods": "integer",
        },
        SENSITIVITY_FINANCIAL_AGGREGATE,
        "committee contribution summary role",
        tools.get_contribution_summary_range,
    ),
    _definition(
        "get_contribution_trends",
        "Must use whenever an authorized committee user asks for monthly contribution trends, comparisons, best month, worst month, or month-to-month contribution performance.",
        (
            _argument("start_year", "integer", True, "Start contribution year."),
            _argument("start_month", "integer", True, "Start contribution month, 1-12."),
            _argument("end_year", "integer", True, "End contribution year."),
            _argument("end_month", "integer", True, "End contribution month, 1-12."),
        ),
        {
            "start_period": "date string",
            "end_period": "date string",
            "period_count": "integer",
            "periods": [
                {
                    "period": "date string",
                    "active_member_count": "integer",
                    "expected_total": "money string",
                    "total_collected": "money string",
                    "outstanding_total": "money string",
                    "fully_paid_count": "integer",
                    "completion_percent": "number",
                    "status_counts": "object<integer>",
                }
            ],
        },
        SENSITIVITY_FINANCIAL_AGGREGATE,
        "committee contribution summary role",
        tools.get_contribution_trends,
    ),
    _definition(
        "get_member_financial_snapshot",
        "Must use whenever an authorized committee user asks about a specific member's contributions, loan balance, outstanding amounts, welfare status, or financial summary by name.",
        (
            _argument(
                "member_query",
                "string",
                True,
                "Member name, username, or membership number from the user's request.",
                maximum=80,
            ),
            _argument("year", "integer", False, "Optional year filter."),
            _argument("month", "integer", False, "Optional month filter, 1-12."),
        ),
        {
            "resolution_status": "resolved | ambiguous | not_found",
            "member": {
                "display_name": "string",
                "role": "string",
                "is_active": "boolean",
            },
            "period": "object",
            "contributions": "object",
            "loans": "object",
            "welfare": "object",
            "message": "string",
            "match_count": "integer",
        },
        SENSITIVITY_FINANCIAL_PRIVATE,
        "committee member financial record role",
        tools.get_member_financial_snapshot,
    ),
    _definition(
        "get_loan_summary",
        "Must use whenever an authorized official asks for group-level loan analytics, totals, balances, or status counts.",
        (
            _argument(
                "status",
                "string",
                False,
                "Optional loan status filter.",
                enum=("pending", "approved", "rejected"),
            ),
            _argument("year", "integer", False, "Loan year filter."),
            _argument("month", "integer", False, "Loan month filter, 1-12."),
        ),
        {
            "loan_count": "integer",
            "status_counts": "object<integer>",
            "total_disbursed": "money string",
            "total_repaid": "money string",
            "outstanding_balance": "money string",
        },
        SENSITIVITY_FINANCIAL_AGGREGATE,
        "group loan summary role",
        tools.get_loan_summary,
    ),
    _definition(
        "get_welfare_summary",
        "Must use whenever an authorized official asks for welfare statistics, totals, status counts, or period activity.",
        (
            _argument("year", "integer", False, "Welfare year filter."),
            _argument("month", "integer", False, "Welfare month filter, 1-12."),
        ),
        {
            "record_count": "integer",
            "total_amount": "money string",
            "status_counts": "object<integer>",
        },
        SENSITIVITY_FINANCIAL_AGGREGATE,
        "welfare summary role",
        tools.get_welfare_summary,
    ),
    _definition(
        "get_meeting_minutes",
        "Must use whenever the user asks about actual Tambul meeting minutes, meeting notes, or latest meeting content they can access.",
        (
            _argument(
                "query",
                "string",
                False,
                "Optional text search over existing meeting-minute fields.",
                maximum=120,
            ),
            _argument(
                "audience",
                "string",
                False,
                "Optional audience filter.",
                enum=(
                    MeetingNote.AUDIENCE_ALL,
                    MeetingNote.AUDIENCE_COMMITTEE,
                ),
            ),
            _argument(
                "limit",
                "integer",
                False,
                "Maximum meeting-minute records to return.",
                maximum=MAX_MEETING_LIMIT,
            ),
        ),
        {
            "record_count": "integer",
            "returned_count": "integer",
            "truncated": "boolean",
            "limit": "integer",
            "minutes": [
                {
                    "title": "string",
                    "description": "string",
                    "content_excerpt": "string",
                    "audience": "string",
                    "created_at": "datetime string",
                }
            ],
        },
        SENSITIVITY_COMMITTEE_PRIVATE,
        "authenticated general minutes; committee role for committee-only minutes",
        tools.get_meeting_minutes,
    ),
    _definition(
        "get_financial_summary",
        "Must use whenever an authorized committee user asks for a group financial overview across contributions, loans, and welfare for a month, year, or date range.",
        (
            _argument("year", "integer", False, "Financial summary year filter."),
            _argument("month", "integer", False, "Financial summary month filter, 1-12."),
            _argument("start_year", "integer", False, "Range start year."),
            _argument("start_month", "integer", False, "Range start month, 1-12."),
            _argument("end_year", "integer", False, "Range end year."),
            _argument("end_month", "integer", False, "Range end month, 1-12."),
        ),
        {
            "period": "object",
            "contributions": "object",
            "loans": "object",
            "welfare": "object",
        },
        SENSITIVITY_FINANCIAL_AGGREGATE,
        "group financial analytics role",
        tools.get_financial_summary,
    ),
    _definition(
        "draft_member_reminder_context",
        "Must use whenever an authorized official asks for contribution reminder context; returns data only and does not draft or send messages.",
        (
            _argument("year", "integer", True, "Contribution year."),
            _argument("month", "integer", True, "Contribution month, 1-12."),
            _argument(
                "limit",
                "integer",
                False,
                "Maximum reminder rows to return.",
                maximum=MAX_GROUP_RECORD_LIMIT,
            ),
        ),
        {
            "period": "date string",
            "reminder_count": "integer",
            "returned_count": "integer",
            "truncated": "boolean",
            "reminders": [
                {
                    "member_display_name": "string",
                    "contribution_period": "date string",
                    "expected_amount": "money string",
                    "amount_paid": "money string",
                    "balance_due": "money string",
                    "status": "string",
                }
            ],
        },
        SENSITIVITY_FINANCIAL_PRIVATE,
        "individual outstanding contribution role",
        tools.draft_member_reminder_context,
    ),
)

AI_TOOL_REGISTRY = {definition.name: definition for definition in _TOOL_DEFINITIONS}


def _base_meta(sensitivity):
    return {
        "schema_version": SCHEMA_VERSION,
        "sensitivity": sensitivity,
    }


def _safe_error(tool_name, code, message, sensitivity, event):
    return {
        "ok": False,
        "tool": tool_name,
        "error": {
            "code": code,
            "message": message,
        },
        "meta": {
            **_base_meta(sensitivity),
            "audit": audit_meta(event),
        },
    }


def _safe_success(tool_name, data, sensitivity, event):
    return {
        "ok": True,
        "tool": tool_name,
        "data": data,
        "meta": {
            **_base_meta(sensitivity),
            "audit": audit_meta(event),
        },
    }


def get_ai_tool_schema(tool_name=None):
    if tool_name is None:
        return {
            name: definition.schema.to_dict()
            for name, definition in AI_TOOL_REGISTRY.items()
        }
    definition = AI_TOOL_REGISTRY.get(tool_name)
    if definition is None:
        return None
    return definition.schema.to_dict()


def invoke_ai_tool(tool_name, user, arguments=None):
    definition = AI_TOOL_REGISTRY.get(tool_name)
    if definition is None:
        event = build_tool_audit_event(
            user,
            tool_name,
            AUTHORIZATION_UNSUPPORTED,
            sensitivity=SENSITIVITY_LOW,
            success=False,
            error_category=UNSUPPORTED_REQUEST,
        )
        record_tool_audit_event(event)
        return _safe_error(
            tool_name,
            UNSUPPORTED_REQUEST,
            "Tool is not registered.",
            SENSITIVITY_LOW,
            event,
        )

    sensitivity = definition.sensitivity
    try:
        data = definition.handler(user, arguments or {})
    except ToolValidationError as exc:
        event = build_tool_audit_event(
            user,
            tool_name,
            AUTHORIZATION_DENIED,
            sensitivity=sensitivity,
            success=False,
            error_category=INVALID_ARGUMENT,
        )
        record_tool_audit_event(event)
        return _safe_error(tool_name, INVALID_ARGUMENT, str(exc), sensitivity, event)
    except PermissionDenied:
        is_authenticated = bool(getattr(user, "is_authenticated", False))
        code = PERMISSION_DENIED if is_authenticated else AUTHENTICATION_REQUIRED
        authorization_result = (
            AUTHORIZATION_DENIED
            if is_authenticated
            else AUTHORIZATION_NOT_AUTHENTICATED
        )
        message = (
            "You are not authorized to use this tool."
            if is_authenticated
            else "Authentication is required."
        )
        event = build_tool_audit_event(
            user,
            tool_name,
            authorization_result,
            sensitivity=sensitivity,
            success=False,
            error_category=code,
        )
        record_tool_audit_event(event)
        return _safe_error(tool_name, code, message, sensitivity, event)
    except (LookupError, ObjectDoesNotExist):
        event = build_tool_audit_event(
            user,
            tool_name,
            AUTHORIZATION_ALLOWED,
            sensitivity=sensitivity,
            success=False,
            error_category=NOT_FOUND,
        )
        record_tool_audit_event(event)
        return _safe_error(
            tool_name,
            NOT_FOUND,
            "The requested record was not found.",
            sensitivity,
            event,
        )
    except Exception:
        event = build_tool_audit_event(
            user,
            tool_name,
            AUTHORIZATION_ALLOWED,
            sensitivity=sensitivity,
            success=False,
            error_category=INTERNAL_ERROR,
        )
        record_tool_audit_event(event)
        return _safe_error(
            tool_name,
            INTERNAL_ERROR,
            "The tool could not complete the request.",
            sensitivity,
            event,
        )

    event = build_tool_audit_event(
        user,
        tool_name,
        AUTHORIZATION_ALLOWED,
        sensitivity=sensitivity,
        success=True,
    )
    record_tool_audit_event(event)
    return _safe_success(tool_name, data, sensitivity, event)
