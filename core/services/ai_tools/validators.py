from django.utils import timezone

from core.models import Loan, MeetingNote

MIN_AI_TOOL_YEAR = 2000
MAX_QUERY_LENGTH = 120
MAX_MEMBER_QUERY_LENGTH = 80
MAX_MEETING_LIMIT = 10
MAX_MEMBER_RECORD_LIMIT = 36
MAX_GROUP_RECORD_LIMIT = 50


class ToolValidationError(ValueError):
    pass


def clean_arguments(arguments, allowed_names):
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise ToolValidationError("arguments must be an object")

    unknown = sorted(set(arguments) - set(allowed_names))
    if unknown:
        raise ToolValidationError(f"unsupported argument(s): {', '.join(unknown)}")
    return arguments


def validate_year(value, *, required=False):
    if value in (None, ""):
        if required:
            raise ToolValidationError("year is required")
        return None
    if isinstance(value, bool):
        raise ToolValidationError("year must be an integer")
    try:
        year = int(value)
    except (TypeError, ValueError):
        raise ToolValidationError("year must be an integer") from None

    max_year = timezone.localdate().year + 1
    if year < MIN_AI_TOOL_YEAR or year > max_year:
        raise ToolValidationError(
            f"year must be between {MIN_AI_TOOL_YEAR} and {max_year}"
        )
    return year


def validate_month(value, *, required=False):
    if value in (None, ""):
        if required:
            raise ToolValidationError("month is required")
        return None
    if isinstance(value, bool):
        raise ToolValidationError("month must be an integer")
    try:
        month = int(value)
    except (TypeError, ValueError):
        raise ToolValidationError("month must be an integer") from None
    if month < 1 or month > 12:
        raise ToolValidationError("month must be between 1 and 12")
    return month


def validate_limit(value, *, default, maximum):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ToolValidationError("limit must be an integer")
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise ToolValidationError("limit must be an integer") from None
    if limit < 1:
        raise ToolValidationError("limit must be positive")
    if limit > maximum:
        raise ToolValidationError(f"limit must be {maximum} or less")
    return limit


def validate_query(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ToolValidationError("query must be text")
    query = value.strip()
    if len(query) > MAX_QUERY_LENGTH:
        raise ToolValidationError(
            f"query must be {MAX_QUERY_LENGTH} characters or less"
        )
    return query or None


def validate_member_query(value):
    if value in (None, ""):
        raise ToolValidationError("member_query is required")
    if not isinstance(value, str):
        raise ToolValidationError("member_query must be text")
    query = " ".join(value.strip().split())
    if not query:
        raise ToolValidationError("member_query is required")
    if len(query) > MAX_MEMBER_QUERY_LENGTH:
        raise ToolValidationError(
            f"member_query must be {MAX_MEMBER_QUERY_LENGTH} characters or less"
        )
    return query


def validate_loan_status(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ToolValidationError("status must be text")
    status = value.strip()
    allowed = {choice for choice, _label in Loan._meta.get_field("status").choices}
    if status not in allowed:
        raise ToolValidationError("status is not supported")
    return status


def validate_meeting_audience(value):
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ToolValidationError("audience must be text")
    audience = value.strip()
    allowed = {choice for choice, _label in MeetingNote.AUDIENCE_CHOICES}
    if audience not in allowed:
        raise ToolValidationError("audience is not supported")
    return audience
