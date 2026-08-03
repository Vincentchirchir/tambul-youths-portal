import secrets
from decimal import Decimal

from django.utils import timezone

from core.models import Contribution, Loan, Welfare
from .models import PaymentIntent


CURRENT_MONTH_CONTRIBUTION_AMOUNT = Decimal("200.00")
LATE_MONTHLY_CONTRIBUTION_AMOUNT = Decimal("250.00")
MONTHLY_CONTRIBUTION_DUE_DAY = 10
CENTS = Decimal("0.01")


def get_bill_type(payment_intent):
    """
    FIXED means the member must pay the expected amount.
    PARTIAL means the member can pay a different amount.
    """
    fixed_payment_types = [
        "monthly_contribution",
        "welfare_contribution",
        "registration_fee",
        "penalty",
    ]

    if payment_intent.payment_type in fixed_payment_types:
        return "FIXED"

    return "PARTIAL"


def amounts_match(payment_intent, paid_amount):
    """
    Only enforce exact amount for FIXED bill types.
    """
    if get_bill_type(payment_intent) == "PARTIAL":
        return True

    return Decimal(payment_intent.amount_expected) == Decimal(paid_amount)


def get_month_start(value=None):
    """
    Return the first day of the month.
    """
    if value is None:
        value = timezone.localdate()
    return value.replace(day=1)


def money(value):
    return Decimal(value or 0).quantize(CENTS)


def expected_monthly_contribution_amount(month, today=None):
    """
    Current month is KES 200 through day 10, otherwise KES 250.
    Previous unpaid months are always treated as late at KES 250.
    """
    today = today or timezone.localdate()
    month = get_month_start(month)
    current_month = get_month_start(today)

    if month > current_month:
        return CURRENT_MONTH_CONTRIBUTION_AMOUNT

    if month == current_month and today.day <= MONTHLY_CONTRIBUTION_DUE_DAY:
        return CURRENT_MONTH_CONTRIBUTION_AMOUNT

    return LATE_MONTHLY_CONTRIBUTION_AMOUNT


def current_month_contribution_penalty(today=None):
    today = today or timezone.localdate()
    if today.day > MONTHLY_CONTRIBUTION_DUE_DAY:
        return money(
            LATE_MONTHLY_CONTRIBUTION_AMOUNT - CURRENT_MONTH_CONTRIBUTION_AMOUNT
        )
    return Decimal("0.00")


def get_monthly_contribution_breakdown(
    member,
    today=None,
    *,
    lock=False,
    create_current=False,
):
    """
    Return unpaid monthly contribution balances up to the current month.
    Existing contribution records are authoritative for older months. The
    current month is included even if the scheduled row has not been generated.
    """
    today = today or timezone.localdate()
    current_month = get_month_start(today)

    contributions = Contribution.objects.filter(
        member=member,
        month__lte=current_month,
    ).exclude(status="fully_paid")

    if lock:
        contributions = contributions.select_for_update()

    contribution_rows = list(contributions.order_by("month", "created_at"))
    has_current_month = Contribution.objects.filter(
        member=member,
        month=current_month,
    ).exists()

    if not has_current_month:
        if create_current:
            current_contribution = Contribution.objects.create(
                member=member,
                month=current_month,
                amount=Decimal("0.00"),
                status="not_paid",
                updated_at=timezone.now(),
            )
        else:
            current_contribution = None

        contribution_rows.append(current_contribution)

    breakdown = []
    for contribution in contribution_rows:
        month = contribution.month if contribution else current_month
        paid_amount = money(contribution.amount) if contribution else Decimal("0.00")
        expected_amount = expected_monthly_contribution_amount(month, today=today)
        balance = money(expected_amount - paid_amount)

        if balance <= Decimal("0.00"):
            continue

        breakdown.append({
            "contribution": contribution,
            "month": month,
            "expected": expected_amount,
            "paid": paid_amount,
            "balance": balance,
        })

    return breakdown


def calculate_monthly_contribution_balance(member, today=None):
    breakdown = get_monthly_contribution_breakdown(member, today=today)
    return money(sum(item["balance"] for item in breakdown))


def get_loan_payment_balance(loan):
    if loan is None:
        return Decimal("0.00")
    return money(loan.current_balance())


def generate_customer_reference(
    member,
    payment_type,
    related_loan=None,
    contribution_month=None,
):
    """
    Generate a short unique reference for KCB payment validation.
    """
    today = timezone.localdate()

    if payment_type == "monthly_contribution":
        month_value = contribution_month or today
        prefix = f"MC-{member.id}-{month_value.strftime('%Y%m')}"

    elif payment_type == "loan_repayment":
        loan_id = related_loan.id if related_loan else "NA"
        prefix = f"LR-{loan_id}"

    elif payment_type == "welfare_contribution":
        prefix = f"WF-{member.id}-{today.year}"

    elif payment_type == "registration_fee":
        prefix = f"REG-{member.id}"

    elif payment_type == "penalty":
        prefix = f"PN-{member.id}"

    else:
        prefix = f"PAY-{member.id}"

    for _ in range(10):
        suffix = secrets.token_hex(2).upper()
        reference = f"{prefix}-{suffix}"

        if not PaymentIntent.objects.filter(customer_reference=reference).exists():
            return reference

    raise ValueError("Could not generate a unique payment reference.")


def post_successful_payment(payment_intent, notification):
    """
    Posts a confirmed KCB payment to the correct app section.
    This function is called only after KCB Bill-Notification confirms that
    the account has been credited.
    """
    paid_amount = Decimal(notification.transaction_amount)

    if payment_intent.payment_type == "monthly_contribution":
        post_monthly_contribution(payment_intent, paid_amount)
        return

    if payment_intent.payment_type == "loan_repayment":
        post_loan_repayment(payment_intent, paid_amount)
        return

    if payment_intent.payment_type == "welfare_contribution":
        post_welfare_contribution(payment_intent, paid_amount, notification)
        return

    if payment_intent.payment_type == "registration_fee":
        return

    if payment_intent.payment_type == "penalty":
        return


def post_monthly_contribution(payment_intent, paid_amount):
    """
    Apply a monthly contribution payment to the oldest unpaid month first.
    The current Contribution model stores amount as total paid for that month.
    """
    remaining = money(paid_amount)
    intent_date = timezone.localtime(payment_intent.created_at).date()
    breakdown = get_monthly_contribution_breakdown(
        payment_intent.member,
        today=intent_date,
        lock=True,
        create_current=True,
    )

    for item in breakdown:
        if remaining <= Decimal("0.00"):
            break

        contribution = item["contribution"]
        if contribution is None:
            continue

        amount_to_apply = min(remaining, item["balance"])
        new_amount = money(Decimal(contribution.amount) + amount_to_apply)
        expected_amount = item["expected"]

        contribution.amount = new_amount
        if new_amount >= expected_amount:
            contribution.status = "fully_paid"
        elif new_amount > Decimal("0.00"):
            contribution.status = "partially_paid"
        else:
            contribution.status = "not_paid"
        contribution.updated_at = timezone.now()
        contribution.save(update_fields=["amount", "status", "updated_at"])

        remaining = money(remaining - amount_to_apply)


def post_loan_repayment(payment_intent, paid_amount):
    """
    Add payment to Loan.total_paid_so_far.
    Loan.save() recalculates repayment_status automatically.
    """
    if payment_intent.related_loan_id is None:
        raise ValueError("Loan repayment payment_intent has no related_loan.")

    loan = Loan.objects.select_for_update().get(pk=payment_intent.related_loan_id)
    if loan.member_id != payment_intent.member_id:
        raise ValueError("Loan does not belong to this payment member.")

    loan.total_paid_so_far = Decimal(loan.total_paid_so_far) + paid_amount
    loan.save()


def post_welfare_contribution(payment_intent, paid_amount, notification):
    """
    Create a Welfare record for the paid welfare contribution.
    A dedicated welfare payment ledger can replace this later.
    """
    Welfare.objects.create(
        member=payment_intent.member,
        description=(
            "Welfare contribution via KCB. "
            f"Ref: {notification.transaction_reference}"
        ),
        amount=paid_amount,
        status="fully_paid",
        updated_at=timezone.now(),
    )
