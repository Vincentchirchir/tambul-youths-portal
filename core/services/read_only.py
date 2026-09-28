from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum

from core.models import Contribution, Loan, MeetingNote, Welfare
from core.permissions import (
    ROLE_ADMIN,
    can_view_committee_minutes,
    can_view_contribution_summaries,
    can_view_group_financial_analytics,
    can_view_group_loan_summaries,
    can_view_individual_outstanding_contributions,
    can_view_member_financial_records,
    can_view_own_financial_information,
    can_view_welfare_summaries,
    ensure_allowed,
)
from payments.services import (
    calculate_monthly_contribution_balance,
    expected_monthly_contribution_amount,
    money,
)

User = get_user_model()


def _require_authenticated(user):
    ensure_allowed(
        bool(getattr(user, "is_authenticated", False)),
        "Authentication is required.",
    )


def _member_display_name(member):
    return member.get_full_name() or member.username


def _iso_date(value):
    return value.isoformat() if value else None


def _money(value):
    return money(Decimal(value or 0))


def _money_str(value):
    return str(_money(value))


def _validate_month(month):
    if month in (None, ""):
        return None
    month = int(month)
    if month < 1 or month > 12:
        raise ValueError("month must be between 1 and 12")
    return month


def _month_start(year, month):
    month = _validate_month(month)
    if year in (None, "") or month is None:
        return None
    return date(int(year), month, 1)


def _next_month(value):
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _month_range(start_year, start_month, end_year, end_month):
    start = _month_start(start_year, start_month)
    end = _month_start(end_year, end_month)
    if start is None or end is None:
        raise ValueError("start and end year/month are required")
    if end < start:
        raise ValueError("end period must not be before start period")

    periods = []
    current = start
    while current <= end:
        periods.append(current)
        current = _next_month(current)
    return periods


def _period_filter(queryset, field_name, year=None, month=None):
    if year not in (None, ""):
        queryset = queryset.filter(**{f"{field_name}__year": int(year)})
    month = _validate_month(month)
    if month is not None:
        queryset = queryset.filter(**{f"{field_name}__month": month})
    return queryset


def _period_range_filter(
    queryset,
    field_name,
    start_year=None,
    start_month=None,
    end_year=None,
    end_month=None,
):
    has_start = start_year not in (None, "") or start_month not in (None, "")
    has_end = end_year not in (None, "") or end_month not in (None, "")
    if not has_start and not has_end:
        return queryset

    periods = _month_range(start_year, start_month, end_year, end_month)
    return queryset.filter(
        **{
            f"{field_name}__gte": periods[0],
            f"{field_name}__lt": _next_month(periods[-1]),
        }
    )


def _sum_decimal(queryset, field_name):
    return _money(queryset.aggregate(total=Sum(field_name))["total"])


def _status_counts(queryset):
    return {
        item["status"]: item["count"]
        for item in queryset.values("status").annotate(count=Count("id"))
    }


def _active_members():
    return User.objects.filter(is_active=True).exclude(role=ROLE_ADMIN)


def _active_member_count():
    return _active_members().count()


def _contribution_row(contribution):
    expected = expected_monthly_contribution_amount(contribution.month)
    balance = max(_money(expected - contribution.amount), Decimal("0.00"))
    return {
        "id": contribution.id,
        "month": _iso_date(contribution.month),
        "amount": _money_str(contribution.amount),
        "expected_amount": _money_str(expected),
        "balance_due": _money_str(balance),
        "status": contribution.status,
        "status_label": contribution.get_status_display(),
    }


def _loan_row(loan):
    return {
        "id": loan.id,
        "amount": _money_str(loan.amount),
        "interest": _money_str(loan.interest),
        "total_paid_so_far": _money_str(loan.total_paid_so_far),
        "current_balance": _money_str(loan.current_balance()),
        "status": loan.status,
        "status_label": loan.get_status_display(),
        "repayment_status": loan.repayment_status,
        "repayment_status_label": loan.get_repayment_status_display(),
        "loan_date": _iso_date(loan.loan_date),
        "due_date": _iso_date(loan.due_date),
    }


def _normalise_member_query(query):
    return " ".join((query or "").split())


def _member_matches_for_query(query):
    query = _normalise_member_query(query)
    if not query:
        return User.objects.none()

    members = _active_members()
    terms = query.split()

    exact_query = (
        Q(username__iexact=query)
        | Q(first_name__iexact=query)
        | Q(last_name__iexact=query)
        | Q(membership_number__iexact=query)
    )
    if len(terms) >= 2:
        first = terms[0]
        last = " ".join(terms[1:])
        exact_query |= Q(first_name__iexact=first, last_name__iexact=last)
        exact_query |= Q(first_name__iexact=last, last_name__iexact=first)

    exact_matches = members.filter(exact_query).order_by(
        "first_name",
        "last_name",
        "username",
    )
    if exact_matches.exists():
        return exact_matches

    fuzzy_query = Q()
    for term in terms:
        fuzzy_query &= (
            Q(first_name__icontains=term)
            | Q(last_name__icontains=term)
            | Q(username__icontains=term)
            | Q(membership_number__icontains=term)
        )
    return members.filter(fuzzy_query).order_by(
        "first_name",
        "last_name",
        "username",
    )


def _contribution_summary_for_period(period_start):
    contributions = Contribution.objects.filter(month=period_start)
    active_member_count = _active_member_count()
    expected_amount = expected_monthly_contribution_amount(period_start)
    expected_total = _money(expected_amount * active_member_count)
    total_collected = _sum_decimal(contributions, "amount")
    fully_paid_count = contributions.filter(status="fully_paid").count()
    outstanding_total = max(expected_total - total_collected, Decimal("0.00"))
    completion_percent = (
        round((fully_paid_count / active_member_count) * 100, 2)
        if active_member_count
        else 0.0
    )

    return {
        "period": period_start.isoformat(),
        "active_member_count": active_member_count,
        "expected_total": _money_str(expected_total),
        "total_collected": _money_str(total_collected),
        "outstanding_total": _money_str(outstanding_total),
        "fully_paid_count": fully_paid_count,
        "completion_percent": completion_percent,
        "status_counts": _status_counts(contributions),
    }


def get_current_user_context(user):
    _require_authenticated(user)
    return {
        "user_id": user.id,
        "role": getattr(user, "role", ""),
        "display_name": _member_display_name(user),
    }


def get_my_contribution_status(user, year=None, month=None):
    _require_authenticated(user)
    ensure_allowed(can_view_own_financial_information(user))

    contributions = _period_filter(
        Contribution.objects.filter(member=user),
        "month",
        year,
        month,
    ).order_by("-month", "-created_at")
    records = [_contribution_row(contribution) for contribution in contributions]

    period_start = _month_start(year, month)
    if period_start:
        contribution = Contribution.objects.filter(member=user, month=period_start).first()
        paid_amount = _money(contribution.amount if contribution else Decimal("0.00"))
        expected_amount = expected_monthly_contribution_amount(period_start)
        balance_due = max(_money(expected_amount - paid_amount), Decimal("0.00"))
    else:
        balance_due = calculate_monthly_contribution_balance(user)

    return {
        "member_id": user.id,
        "total_paid": _money_str(_sum_decimal(contributions, "amount")),
        "balance_due": _money_str(balance_due),
        "records": records,
    }


def get_my_loan_summary(user):
    _require_authenticated(user)
    ensure_allowed(can_view_own_financial_information(user))

    loans = Loan.objects.filter(member=user).order_by("-created_at")
    active_loans = loans.filter(status__in=["pending", "approved"])
    outstanding_balance = sum(
        (loan.current_balance() for loan in active_loans),
        Decimal("0.00"),
    )
    return {
        "member_id": user.id,
        "active_loan_count": active_loans.count(),
        "total_principal": _money_str(_sum_decimal(active_loans, "amount")),
        "total_paid": _money_str(_sum_decimal(active_loans, "total_paid_so_far")),
        "outstanding_balance": _money_str(outstanding_balance),
        "status_counts": _status_counts(loans),
        "loans": [_loan_row(loan) for loan in loans],
    }


def get_member_financial_summary(user, member=None, year=None, month=None):
    _require_authenticated(user)
    member = member or user
    ensure_allowed(
        can_view_member_financial_records(user, member),
        "You are not authorized to view this member's financial records.",
    )

    contributions = _period_filter(
        Contribution.objects.filter(member=member),
        "month",
        year,
        month,
    )
    loans = _period_filter(
        Loan.objects.filter(member=member),
        "loan_date",
        year,
        month,
    )
    welfare_records = _period_filter(
        Welfare.objects.filter(member=member),
        "date_given",
        year,
        month,
    )
    active_loans = loans.filter(status__in=["pending", "approved"])
    outstanding_loan_balance = sum(
        (loan.current_balance() for loan in active_loans),
        Decimal("0.00"),
    )

    return {
        "member": {
            "id": member.id,
            "display_name": _member_display_name(member),
        },
        "contributions": {
            "total_paid": _money_str(_sum_decimal(contributions, "amount")),
            "status_counts": _status_counts(contributions),
        },
        "loans": {
            "active_count": active_loans.count(),
            "total_principal": _money_str(_sum_decimal(active_loans, "amount")),
            "total_paid": _money_str(_sum_decimal(active_loans, "total_paid_so_far")),
            "outstanding_balance": _money_str(outstanding_loan_balance),
            "status_counts": _status_counts(loans),
        },
        "welfare": {
            "total_amount": _money_str(_sum_decimal(welfare_records, "amount")),
            "status_counts": _status_counts(welfare_records),
        },
    }


def get_member_financial_snapshot(user, member_query, year=None, month=None):
    _require_authenticated(user)
    matches = _member_matches_for_query(member_query)
    match_count = matches.count()

    if match_count == 0:
        return {
            "resolution_status": "not_found",
            "member_query": _normalise_member_query(member_query),
            "message": "No matching active member was found.",
        }
    if match_count > 1:
        return {
            "resolution_status": "ambiguous",
            "member_query": _normalise_member_query(member_query),
            "match_count": match_count,
            "message": "More than one active member matches. Ask using the full name.",
        }

    member = matches.get()
    ensure_allowed(
        can_view_member_financial_records(user, member),
        "You are not authorized to view this member's financial records.",
    )
    summary = get_member_financial_summary(user, member=member, year=year, month=month)
    return {
        "resolution_status": "resolved",
        "member": {
            "display_name": summary["member"]["display_name"],
            "role": member.role,
            "is_active": member.is_active,
        },
        "period": {
            "year": year,
            "month": month,
        },
        "contributions": summary["contributions"],
        "loans": summary["loans"],
        "welfare": summary["welfare"],
    }


def get_monthly_contribution_summary(user, year, month):
    _require_authenticated(user)
    ensure_allowed(
        can_view_contribution_summaries(user),
        "You are not authorized to view contribution summaries.",
    )

    period_start = _month_start(year, month)
    if period_start is None:
        raise ValueError("year and month are required")

    return _contribution_summary_for_period(period_start)


def get_contribution_summary_range(user, start_year, start_month, end_year, end_month):
    _require_authenticated(user)
    ensure_allowed(
        can_view_contribution_summaries(user),
        "You are not authorized to view contribution summaries.",
    )

    periods = _month_range(start_year, start_month, end_year, end_month)
    monthly = [_contribution_summary_for_period(period) for period in periods]
    expected_total = sum(
        (_money(item["expected_total"]) for item in monthly),
        Decimal("0.00"),
    )
    collected_total = sum(
        (_money(item["total_collected"]) for item in monthly),
        Decimal("0.00"),
    )
    outstanding_total = max(expected_total - collected_total, Decimal("0.00"))
    fully_paid_member_periods = sum(item["fully_paid_count"] for item in monthly)
    expected_member_periods = sum(item["active_member_count"] for item in monthly)
    unpaid_or_late_member_periods = max(
        expected_member_periods - fully_paid_member_periods,
        0,
    )
    collection_percentage = (
        round(float((collected_total / expected_total) * 100), 2)
        if expected_total > 0
        else 0.0
    )

    return {
        "start_period": periods[0].isoformat(),
        "end_period": periods[-1].isoformat(),
        "period_count": len(periods),
        "expected_amount": _money_str(expected_total),
        "collected_amount": _money_str(collected_total),
        "outstanding_amount": _money_str(outstanding_total),
        "collection_percentage": collection_percentage,
        "expected_member_periods": expected_member_periods,
        "fully_paid_member_periods": fully_paid_member_periods,
        "unpaid_or_late_member_periods": unpaid_or_late_member_periods,
    }


def get_contribution_trends(user, start_year, start_month, end_year, end_month):
    _require_authenticated(user)
    ensure_allowed(
        can_view_contribution_summaries(user),
        "You are not authorized to view contribution trends.",
    )

    periods = _month_range(start_year, start_month, end_year, end_month)
    return {
        "start_period": periods[0].isoformat(),
        "end_period": periods[-1].isoformat(),
        "period_count": len(periods),
        "periods": [
            _contribution_summary_for_period(period)
            for period in periods
        ],
    }


def get_outstanding_contributions(user, year, month):
    _require_authenticated(user)
    ensure_allowed(
        can_view_individual_outstanding_contributions(user),
        "You are not authorized to view individual outstanding contributions.",
    )

    period_start = _month_start(year, month)
    if period_start is None:
        raise ValueError("year and month are required")

    expected_amount = expected_monthly_contribution_amount(period_start)
    contributions = {
        contribution.member_id: contribution
        for contribution in Contribution.objects.filter(month=period_start)
    }

    outstanding = []
    for member in _active_members().order_by("first_name", "last_name", "username"):
        contribution = contributions.get(member.id)
        paid_amount = _money(contribution.amount if contribution else Decimal("0.00"))
        balance_due = max(_money(expected_amount - paid_amount), Decimal("0.00"))
        if balance_due <= Decimal("0.00"):
            continue
        outstanding.append(
            {
                "member_id": member.id,
                "display_name": _member_display_name(member),
                "contribution_id": contribution.id if contribution else None,
                "amount_paid": _money_str(paid_amount),
                "expected_amount": _money_str(expected_amount),
                "balance_due": _money_str(balance_due),
                "status": contribution.status if contribution else "not_paid",
            }
        )

    return {
        "period": period_start.isoformat(),
        "outstanding_count": len(outstanding),
        "members": outstanding,
    }


def get_loan_summary(user, status=None, year=None, month=None):
    _require_authenticated(user)
    ensure_allowed(
        can_view_group_loan_summaries(user),
        "You are not authorized to view loan summaries.",
    )

    loans = _period_filter(Loan.objects.all(), "loan_date", year, month)
    if status:
        loans = loans.filter(status=status)
    approved_loans = loans.filter(status="approved")
    outstanding_balance = sum(
        (loan.current_balance() for loan in approved_loans),
        Decimal("0.00"),
    )

    return {
        "loan_count": loans.count(),
        "status_counts": _status_counts(loans),
        "total_disbursed": _money_str(_sum_decimal(approved_loans, "amount")),
        "total_repaid": _money_str(_sum_decimal(approved_loans, "total_paid_so_far")),
        "outstanding_balance": _money_str(outstanding_balance),
    }


def get_welfare_summary(user, year=None, month=None):
    _require_authenticated(user)
    ensure_allowed(
        can_view_welfare_summaries(user),
        "You are not authorized to view welfare summaries.",
    )

    welfare_records = _period_filter(Welfare.objects.all(), "date_given", year, month)
    return {
        "record_count": welfare_records.count(),
        "total_amount": _money_str(_sum_decimal(welfare_records, "amount")),
        "status_counts": _status_counts(welfare_records),
    }


def get_meeting_minutes(user, query=None, audience=None, limit=None):
    _require_authenticated(user)

    notes = MeetingNote.objects.all()
    if audience == MeetingNote.AUDIENCE_COMMITTEE:
        ensure_allowed(
            can_view_committee_minutes(user),
            "You are not authorized to view committee-only meeting minutes.",
        )
        notes = notes.filter(audience=MeetingNote.AUDIENCE_COMMITTEE)
    elif audience == MeetingNote.AUDIENCE_ALL:
        notes = notes.filter(audience=MeetingNote.AUDIENCE_ALL)
    elif not can_view_committee_minutes(user):
        notes = notes.filter(audience=MeetingNote.AUDIENCE_ALL)

    if query:
        notes = notes.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(content__icontains=query)
        )

    ordered_notes = notes.order_by("-created_at")
    if limit is not None:
        limit = int(limit)
        if limit < 1:
            raise ValueError("limit must be positive")
        ordered_notes = ordered_notes[:limit]

    return [
        {
            "id": note.id,
            "title": note.title,
            "description": note.description,
            "content": note.content,
            "audience": note.audience,
            "created_at": note.created_at.isoformat(),
            "posted_by_id": note.posted_by_id,
        }
        for note in ordered_notes
    ]


def get_financial_summary(
    user,
    year=None,
    month=None,
    start_year=None,
    start_month=None,
    end_year=None,
    end_month=None,
):
    _require_authenticated(user)
    ensure_allowed(
        can_view_group_financial_analytics(user),
        "You are not authorized to view group financial analytics.",
    )

    if start_year or start_month or end_year or end_month:
        periods = _month_range(start_year, start_month, end_year, end_month)
        period = {
            "start_period": periods[0].isoformat(),
            "end_period": periods[-1].isoformat(),
            "period_count": len(periods),
        }
        contributions = _period_range_filter(
            Contribution.objects.all(),
            "month",
            start_year,
            start_month,
            end_year,
            end_month,
        )
        loans = _period_range_filter(
            Loan.objects.all(),
            "loan_date",
            start_year,
            start_month,
            end_year,
            end_month,
        )
        welfare_records = _period_range_filter(
            Welfare.objects.all(),
            "date_given",
            start_year,
            start_month,
            end_year,
            end_month,
        )
    else:
        period = {
            "year": year,
            "month": month,
        }
        contributions = _period_filter(Contribution.objects.all(), "month", year, month)
        loans = _period_filter(Loan.objects.all(), "loan_date", year, month)
        welfare_records = _period_filter(Welfare.objects.all(), "date_given", year, month)
    approved_loans = loans.filter(status="approved")
    outstanding_balance = sum(
        (loan.current_balance() for loan in approved_loans),
        Decimal("0.00"),
    )

    return {
        "period": period,
        "contributions": {
            "total_paid": _money_str(_sum_decimal(contributions, "amount")),
            "record_count": contributions.count(),
            "status_counts": _status_counts(contributions),
        },
        "loans": {
            "total_disbursed": _money_str(_sum_decimal(approved_loans, "amount")),
            "total_repaid": _money_str(_sum_decimal(approved_loans, "total_paid_so_far")),
            "outstanding_balance": _money_str(outstanding_balance),
            "loan_count": loans.count(),
            "status_counts": _status_counts(loans),
        },
        "welfare": {
            "total_amount": _money_str(_sum_decimal(welfare_records, "amount")),
            "record_count": welfare_records.count(),
            "status_counts": _status_counts(welfare_records),
        },
    }
