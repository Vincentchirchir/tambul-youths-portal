from decimal import Decimal

from django import forms
from django.utils import timezone

from core.models import Loan
from .models import PaymentIntent
from .services import (
    calculate_monthly_contribution_balance,
    get_loan_payment_balance,
    get_month_start,
)


class PaymentIntentForm(forms.ModelForm):
    amount_expected = forms.DecimalField(
        required=False,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "step": "0.01",
                "min": "1",
                "placeholder": "Enter amount",
            }
        ),
    )

    contribution_month = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Automatically set for monthly contribution payments.",
    )

    related_loan = forms.ModelChoiceField(
        queryset=Loan.objects.none(),
        required=False,
        label="Loan",
        help_text="Required only if this payment is for loan repayment.",
    )

    class Meta:
        model = PaymentIntent
        fields = [
            "payment_type",
            "amount_expected",
            "contribution_month",
            "related_loan",
        ]
        labels = {
            "payment_type": "What are you paying for?",
            "amount_expected": "Amount",
        }

    def __init__(self, *args, **kwargs):
        self.member = kwargs.pop("member", None)
        super().__init__(*args, **kwargs)

        self.monthly_contribution_balance = Decimal("0.00")
        self.loan_balance_by_id = {}

        if self.member:
            active_loans = Loan.objects.filter(
                member=self.member,
                status="approved",
            ).exclude(
                repayment_status="fully_paid",
            )
            self.fields["related_loan"].queryset = active_loans
            self.monthly_contribution_balance = calculate_monthly_contribution_balance(
                self.member
            )
            self.loan_balance_by_id = {
                str(loan.pk): str(get_loan_payment_balance(loan))
                for loan in active_loans
            }

        today = timezone.localdate()
        self.fields["contribution_month"].initial = get_month_start(today)
        if not self.is_bound:
            self.fields["amount_expected"].initial = self.monthly_contribution_balance

        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "payment-field")

    def clean(self):
        cleaned_data = super().clean()

        payment_type = cleaned_data.get("payment_type")
        related_loan = cleaned_data.get("related_loan")
        amount = cleaned_data.get("amount_expected")
        today = timezone.localdate()

        if payment_type == "monthly_contribution":
            amount = calculate_monthly_contribution_balance(self.member, today=today)
            cleaned_data["amount_expected"] = amount
            cleaned_data["contribution_month"] = get_month_start(today)
            cleaned_data["related_loan"] = None

            if amount <= Decimal("0.00"):
                self.add_error(
                    "payment_type",
                    "You do not have a monthly contribution balance due.",
                )

        elif payment_type == "loan_repayment":
            cleaned_data["contribution_month"] = None
            if not related_loan:
                self.add_error(
                    "related_loan",
                    "Select the loan you are repaying.",
                )

            if related_loan and self.member and related_loan.member_id != self.member.id:
                self.add_error(
                    "related_loan",
                    "This loan does not belong to you.",
                )

            if related_loan:
                amount = get_loan_payment_balance(related_loan)
                cleaned_data["amount_expected"] = amount

                if amount <= Decimal("0.00"):
                    self.add_error(
                        "related_loan",
                        "This loan has no balance to repay.",
                    )

        else:
            cleaned_data["contribution_month"] = None
            cleaned_data["related_loan"] = None

            if amount is None:
                self.add_error("amount_expected", "Amount is required.")
            elif amount <= Decimal("0.00"):
                self.add_error(
                    "amount_expected",
                    "Amount must be greater than zero.",
                )

        return cleaned_data
