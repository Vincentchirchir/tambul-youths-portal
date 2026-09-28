from decimal import Decimal, InvalidOperation

from core.permissions import (
    can_view_committee_minutes,
    can_view_contribution_summaries,
    can_view_group_financial_analytics,
    can_view_group_loan_summaries,
    can_view_individual_outstanding_contributions,
    can_view_own_financial_information,
    can_view_welfare_summaries,
)
from core.services import read_only

from .validators import (
    MAX_GROUP_RECORD_LIMIT,
    MAX_MEETING_LIMIT,
    MAX_MEMBER_RECORD_LIMIT,
    clean_arguments,
    validate_limit,
    validate_loan_status,
    validate_member_query,
    validate_meeting_audience,
    validate_month,
    validate_query,
    validate_year,
)

CONTRIBUTION_RECORD_FIELDS = (
    "month",
    "amount",
    "expected_amount",
    "balance_due",
    "status",
    "status_label",
)
LOAN_RECORD_FIELDS = (
    "amount",
    "interest",
    "total_paid_so_far",
    "current_balance",
    "status",
    "status_label",
    "repayment_status",
    "repayment_status_label",
    "loan_date",
    "due_date",
)
OUTSTANDING_CONTRIBUTION_FIELDS = (
    "display_name",
    "amount_paid",
    "expected_amount",
    "balance_due",
    "status",
)
MONTHLY_CONTRIBUTION_SUMMARY_FIELDS = (
    "period",
    "active_member_count",
    "expected_total",
    "total_collected",
    "outstanding_total",
    "fully_paid_count",
    "completion_percent",
    "status_counts",
)
MEETING_MINUTE_CONTENT_LIMIT = 500
MEETING_MINUTE_DESCRIPTION_LIMIT = 240


def _select_fields(row, allowed_fields):
    return {field: row[field] for field in allowed_fields if field in row}


def _truncate_text(value, max_length):
    text = (value or "").strip()
    if len(text) <= max_length:
        return text
    return f"{text[:max_length].rstrip()}..."


def _slice_records(records, limit):
    selected = list(records[:limit])
    return {
        "records": selected,
        "record_count": len(records),
        "returned_count": len(selected),
        "truncated": len(records) > len(selected),
    }


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _collection_percentage(collected, expected):
    expected_value = _decimal(expected)
    if expected_value <= Decimal("0.00"):
        return 0.0
    return round(float((_decimal(collected) / expected_value) * 100), 2)


def _capabilities(user):
    return {
        "view_own_financial_information": can_view_own_financial_information(user),
        "view_contribution_summaries": can_view_contribution_summaries(user),
        "view_individual_outstanding_contributions": (
            can_view_individual_outstanding_contributions(user)
        ),
        "view_group_loan_summaries": can_view_group_loan_summaries(user),
        "view_welfare_summaries": can_view_welfare_summaries(user),
        "view_group_financial_analytics": can_view_group_financial_analytics(user),
        "view_committee_minutes": can_view_committee_minutes(user),
    }


def get_current_user_context(user, arguments):
    clean_arguments(arguments, set())
    context = read_only.get_current_user_context(user)
    return {
        "display_name": context["display_name"],
        "role": context["role"],
        "capabilities": _capabilities(user),
    }


def get_my_contribution_status(user, arguments):
    arguments = clean_arguments(arguments, {"year", "month", "limit"})
    year = validate_year(arguments.get("year"))
    month = validate_month(arguments.get("month"))
    limit = validate_limit(
        arguments.get("limit"),
        default=MAX_MEMBER_RECORD_LIMIT,
        maximum=MAX_MEMBER_RECORD_LIMIT,
    )

    status = read_only.get_my_contribution_status(user, year=year, month=month)
    records = [
        _select_fields(record, CONTRIBUTION_RECORD_FIELDS)
        for record in status["records"]
    ]
    record_meta = _slice_records(records, limit)
    return {
        "total_paid": status["total_paid"],
        "balance_due": status["balance_due"],
        "record_count": record_meta["record_count"],
        "returned_count": record_meta["returned_count"],
        "truncated": record_meta["truncated"],
        "records": record_meta["records"],
    }


def get_my_loan_summary(user, arguments):
    arguments = clean_arguments(arguments, {"limit"})
    limit = validate_limit(
        arguments.get("limit"),
        default=10,
        maximum=10,
    )

    summary = read_only.get_my_loan_summary(user)
    loans = [_select_fields(loan, LOAN_RECORD_FIELDS) for loan in summary["loans"]]
    loan_meta = _slice_records(loans, limit)
    return {
        "active_loan_count": summary["active_loan_count"],
        "total_principal": summary["total_principal"],
        "total_paid": summary["total_paid"],
        "outstanding_balance": summary["outstanding_balance"],
        "status_counts": summary["status_counts"],
        "loan_record_count": loan_meta["record_count"],
        "returned_count": loan_meta["returned_count"],
        "truncated": loan_meta["truncated"],
        "loans": loan_meta["records"],
    }


def get_monthly_contribution_summary(user, arguments):
    arguments = clean_arguments(arguments, {"year", "month"})
    year = validate_year(arguments.get("year"), required=True)
    month = validate_month(arguments.get("month"), required=True)

    summary = read_only.get_monthly_contribution_summary(user, year, month)
    paid_member_count = summary["fully_paid_count"]
    unpaid_or_late_count = max(summary["active_member_count"] - paid_member_count, 0)
    return {
        "period": summary["period"],
        "active_member_count": summary["active_member_count"],
        "expected_amount": summary["expected_total"],
        "collected_amount": summary["total_collected"],
        "outstanding_amount": summary["outstanding_total"],
        "collection_percentage": _collection_percentage(
            summary["total_collected"],
            summary["expected_total"],
        ),
        "paid_member_count": paid_member_count,
        "unpaid_or_late_count": unpaid_or_late_count,
        "status_counts": summary["status_counts"],
    }


def get_outstanding_contributions(user, arguments):
    arguments = clean_arguments(arguments, {"year", "month", "limit"})
    year = validate_year(arguments.get("year"), required=True)
    month = validate_month(arguments.get("month"), required=True)
    limit = validate_limit(
        arguments.get("limit"),
        default=25,
        maximum=MAX_GROUP_RECORD_LIMIT,
    )

    summary = read_only.get_outstanding_contributions(user, year, month)
    members = [
        _select_fields(member, OUTSTANDING_CONTRIBUTION_FIELDS)
        for member in summary["members"]
    ]
    member_meta = _slice_records(members, limit)
    return {
        "period": summary["period"],
        "outstanding_count": summary["outstanding_count"],
        "returned_count": member_meta["returned_count"],
        "truncated": member_meta["truncated"],
        "members": member_meta["records"],
    }


def get_contribution_summary_range(user, arguments):
    arguments = clean_arguments(
        arguments,
        {"start_year", "start_month", "end_year", "end_month"},
    )
    start_year = validate_year(arguments.get("start_year"), required=True)
    start_month = validate_month(arguments.get("start_month"), required=True)
    end_year = validate_year(arguments.get("end_year"), required=True)
    end_month = validate_month(arguments.get("end_month"), required=True)

    return read_only.get_contribution_summary_range(
        user,
        start_year,
        start_month,
        end_year,
        end_month,
    )


def get_contribution_trends(user, arguments):
    arguments = clean_arguments(
        arguments,
        {"start_year", "start_month", "end_year", "end_month"},
    )
    start_year = validate_year(arguments.get("start_year"), required=True)
    start_month = validate_month(arguments.get("start_month"), required=True)
    end_year = validate_year(arguments.get("end_year"), required=True)
    end_month = validate_month(arguments.get("end_month"), required=True)

    trends = read_only.get_contribution_trends(
        user,
        start_year,
        start_month,
        end_year,
        end_month,
    )
    return {
        **{key: trends[key] for key in ("start_period", "end_period", "period_count")},
        "periods": [
            _select_fields(period, MONTHLY_CONTRIBUTION_SUMMARY_FIELDS)
            for period in trends["periods"]
        ],
    }


def get_member_financial_snapshot(user, arguments):
    arguments = clean_arguments(arguments, {"member_query", "year", "month"})
    member_query = validate_member_query(arguments.get("member_query"))
    year = validate_year(arguments.get("year"))
    month = validate_month(arguments.get("month"))

    return read_only.get_member_financial_snapshot(
        user,
        member_query=member_query,
        year=year,
        month=month,
    )


def get_loan_summary(user, arguments):
    arguments = clean_arguments(arguments, {"status", "year", "month"})
    status = validate_loan_status(arguments.get("status"))
    year = validate_year(arguments.get("year"))
    month = validate_month(arguments.get("month"))

    return read_only.get_loan_summary(user, status=status, year=year, month=month)


def get_welfare_summary(user, arguments):
    arguments = clean_arguments(arguments, {"year", "month"})
    year = validate_year(arguments.get("year"))
    month = validate_month(arguments.get("month"))

    return read_only.get_welfare_summary(user, year=year, month=month)


def get_meeting_minutes(user, arguments):
    arguments = clean_arguments(arguments, {"query", "audience", "limit"})
    query = validate_query(arguments.get("query"))
    audience = validate_meeting_audience(arguments.get("audience"))
    limit = validate_limit(
        arguments.get("limit"),
        default=MAX_MEETING_LIMIT,
        maximum=MAX_MEETING_LIMIT,
    )

    minutes = read_only.get_meeting_minutes(
        user,
        query=query,
        audience=audience,
        limit=limit + 1,
    )
    returned_minutes = minutes[:limit]
    truncated = len(minutes) > len(returned_minutes)
    return {
        "record_count": len(returned_minutes),
        "returned_count": len(returned_minutes),
        "truncated": truncated,
        "limit": limit,
        "minutes": [
            {
                "title": minute["title"],
                "description": _truncate_text(
                    minute["description"],
                    MEETING_MINUTE_DESCRIPTION_LIMIT,
                ),
                "content_excerpt": _truncate_text(
                    minute["content"],
                    MEETING_MINUTE_CONTENT_LIMIT,
                ),
                "audience": minute["audience"],
                "created_at": minute["created_at"],
            }
            for minute in returned_minutes
        ],
    }


def get_financial_summary(user, arguments):
    arguments = clean_arguments(
        arguments,
        {
            "year",
            "month",
            "start_year",
            "start_month",
            "end_year",
            "end_month",
        },
    )
    year = validate_year(arguments.get("year"))
    month = validate_month(arguments.get("month"))
    has_range = any(
        arguments.get(name) not in (None, "")
        for name in ("start_year", "start_month", "end_year", "end_month")
    )
    start_year = validate_year(arguments.get("start_year"), required=has_range)
    start_month = validate_month(arguments.get("start_month"), required=has_range)
    end_year = validate_year(arguments.get("end_year"), required=has_range)
    end_month = validate_month(arguments.get("end_month"), required=has_range)

    return read_only.get_financial_summary(
        user,
        year=year,
        month=month,
        start_year=start_year,
        start_month=start_month,
        end_year=end_year,
        end_month=end_month,
    )


def draft_member_reminder_context(user, arguments):
    arguments = clean_arguments(arguments, {"year", "month", "limit"})
    year = validate_year(arguments.get("year"), required=True)
    month = validate_month(arguments.get("month"), required=True)
    limit = validate_limit(
        arguments.get("limit"),
        default=25,
        maximum=MAX_GROUP_RECORD_LIMIT,
    )

    outstanding = read_only.get_outstanding_contributions(user, year, month)
    reminders = [
        {
            "member_display_name": member["display_name"],
            "contribution_period": outstanding["period"],
            "expected_amount": member["expected_amount"],
            "amount_paid": member["amount_paid"],
            "balance_due": member["balance_due"],
            "status": member["status"],
        }
        for member in outstanding["members"]
    ]
    reminder_meta = _slice_records(reminders, limit)
    return {
        "period": outstanding["period"],
        "reminder_count": outstanding["outstanding_count"],
        "returned_count": reminder_meta["returned_count"],
        "truncated": reminder_meta["truncated"],
        "reminders": reminder_meta["records"],
    }
