from decimal import Decimal

from core.models import Loan


LOAN_LIMIT_AMOUNT = Decimal("3000.00")
MIN_LOAN_AMOUNT = Decimal("100.00")
LOAN_AMOUNT_STEP = Decimal("100.00")
CENTS = Decimal("0.01")

ACTIVE_LOAN_STATUSES = ("pending", "approved")
ACTIVE_REPAYMENT_STATUSES = ("not_paid", "partially_paid", "late")


def money(value):
    return Decimal(value or 0).quantize(CENTS)


def format_ksh(value):
    amount = money(value)
    if amount == amount.to_integral_value():
        return f"Ksh {amount:,.0f}"
    return f"Ksh {amount:,.2f}"


def active_unpaid_loans_for_member(member):
    if not getattr(member, "pk", None):
        return Loan.objects.none()

    return Loan.objects.filter(
        member=member,
        status__in=ACTIVE_LOAN_STATUSES,
        repayment_status__in=ACTIVE_REPAYMENT_STATUSES,
    )


def principal_used_by_loan(loan):
    if (
        loan.status not in ACTIVE_LOAN_STATUSES
        or loan.repayment_status not in ACTIVE_REPAYMENT_STATUSES
    ):
        return Decimal("0.00")

    amount = money(loan.amount)
    paid = max(money(loan.total_paid_so_far), Decimal("0.00"))
    principal_paid = min(paid, amount)
    return money(max(amount - principal_paid, Decimal("0.00")))


def loan_limit_used(member):
    used = sum(
        (principal_used_by_loan(loan) for loan in active_unpaid_loans_for_member(member)),
        Decimal("0.00"),
    )
    return money(used)


def loan_limit_remaining(member):
    return money(max(LOAN_LIMIT_AMOUNT - loan_limit_used(member), Decimal("0.00")))


def can_apply_for_loan(member):
    return loan_limit_remaining(member) >= MIN_LOAN_AMOUNT


def loan_limit_block_message():
    return (
        f"You have reached your {format_ksh(LOAN_LIMIT_AMOUNT)} loan limit. "
        "Clear or reduce your active or unpaid loan before applying again."
    )


def loan_amount_exceeds_message(remaining):
    return (
        f"You can only apply for up to {format_ksh(remaining)}. "
        "Reduce the amount to continue."
    )


def loan_limit_context(member):
    used = loan_limit_used(member)
    remaining = money(max(LOAN_LIMIT_AMOUNT - used, Decimal("0.00")))
    return {
        "loan_limit_amount": LOAN_LIMIT_AMOUNT,
        "loan_limit_used": used,
        "loan_limit_remaining": remaining,
        "loan_limit_amount_display": format_ksh(LOAN_LIMIT_AMOUNT),
        "loan_limit_used_display": format_ksh(used),
        "loan_limit_remaining_display": format_ksh(remaining),
        "can_apply_loan": remaining >= MIN_LOAN_AMOUNT,
    }
