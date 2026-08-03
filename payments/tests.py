import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Contribution, Loan, Welfare
from .models import KcbPaymentNotification, PaymentIntent
from .services import calculate_monthly_contribution_balance


@override_settings(KCB_GROUP_ACCOUNT_NUMBER="1234567890")
class KcbCallbackTests(TestCase):
    def setUp(self):
        self.member = get_user_model().objects.create_user(
            username="member1",
            password="password",
            first_name="Jane",
            last_name="Member",
        )

    def create_intent(
        self,
        customer_reference,
        payment_type="monthly_contribution",
        amount=Decimal("100.00"),
        related_loan=None,
        contribution_month=None,
    ):
        return PaymentIntent.objects.create(
            member=self.member,
            payment_type=payment_type,
            amount_expected=amount,
            customer_reference=customer_reference,
            related_loan=related_loan,
            contribution_month=contribution_month,
        )

    def post_json(self, url_name, payload):
        return self.client.post(
            reverse(url_name),
            data=json.dumps(payload),
            content_type="application/json",
        )

    def post_notification(
        self,
        transaction_reference,
        customer_reference,
        transaction_amount,
    ):
        return self.post_json("kcb_bill_notification", {
            "transactionReference": transaction_reference,
            "customerReference": customer_reference,
            "transactionAmount": transaction_amount,
            "requestId": f"REQ-{transaction_reference}",
            "customerName": "Jane Member",
            "customerMobileNumber": "254700000000",
        })

    def test_validation_returns_fixed_bill_type_for_fixed_payment(self):
        self.create_intent("MONTHLY001", payment_type="monthly_contribution")

        response = self.post_json("kcb_bill_validation", {
            "customerReference": "MONTHLY001",
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["statusCode"], "0")
        self.assertEqual(data["CustomerName"], "Jane Member")
        self.assertEqual(data["billType"], "FIXED")
        self.assertEqual(data["creditAccountIdentifier"], "1234567890")
        intent = PaymentIntent.objects.get(customer_reference="MONTHLY001")
        self.assertEqual(intent.status, "validated")
        self.assertIsNotNone(intent.validated_at)

    def test_validation_accepts_already_validated_payment_reference(self):
        intent = self.create_intent("MONTHLY004", payment_type="monthly_contribution")
        intent.mark_validated()

        response = self.post_json("kcb_bill_validation", {
            "customerReference": "MONTHLY004",
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["statusCode"], "0")
        self.assertEqual(data["billType"], "FIXED")

    def test_validation_returns_partial_bill_type_for_loan_repayment(self):
        self.create_intent(
            "LOAN001",
            payment_type="loan_repayment",
            amount=Decimal("1000.00"),
        )

        response = self.post_json("kcb_bill_validation", {
            "customerReference": "LOAN001",
        })

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["statusCode"], "0")
        self.assertEqual(data["billType"], "PARTIAL")

    def test_member_can_create_payment_reference_from_form(self):
        self.client.force_login(self.member)
        month = timezone.localdate().replace(day=1)
        expected_amount = calculate_monthly_contribution_balance(self.member)

        response = self.client.post(reverse("create_payment"), data={
            "payment_type": "monthly_contribution",
            "amount_expected": "1.00",
            "contribution_month": month.isoformat(),
        })

        payment = PaymentIntent.objects.get(
            member=self.member,
            payment_type="monthly_contribution",
        )
        self.assertRedirects(
            response,
            reverse("payment_instructions", kwargs={"pk": payment.pk}),
        )
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.amount_expected, expected_amount)
        self.assertEqual(payment.contribution_month, month)
        self.assertTrue(
            payment.customer_reference.startswith(
                f"MC-{self.member.id}-{month.strftime('%Y%m')}-"
            )
        )

    def test_monthly_contribution_balance_is_200_through_day_10(self):
        balance = calculate_monthly_contribution_balance(
            self.member,
            today=date(2026, 7, 10),
        )

        self.assertEqual(balance, Decimal("200.00"))

    def test_monthly_contribution_balance_is_250_after_day_10(self):
        balance = calculate_monthly_contribution_balance(
            self.member,
            today=date(2026, 7, 11),
        )

        self.assertEqual(balance, Decimal("250.00"))

    def test_monthly_contribution_balance_includes_defaulted_months(self):
        Contribution.objects.create(
            member=self.member,
            month=date(2026, 5, 1),
            amount=Decimal("0.00"),
            status="late",
        )
        Contribution.objects.create(
            member=self.member,
            month=date(2026, 6, 1),
            amount=Decimal("100.00"),
            status="partially_paid",
        )

        balance = calculate_monthly_contribution_balance(
            self.member,
            today=date(2026, 7, 8),
        )

        self.assertEqual(balance, Decimal("600.00"))

    def test_loan_repayment_form_uses_current_loan_balance(self):
        loan = Loan.objects.create(
            member=self.member,
            amount=Decimal("1000.00"),
            interest=Decimal("0.00"),
            total_paid_so_far=Decimal("200.00"),
            status="approved",
            due_date=date(2099, 1, 1),
        )
        expected_balance = loan.current_balance()
        self.client.force_login(self.member)

        response = self.client.post(reverse("create_payment"), data={
            "payment_type": "loan_repayment",
            "amount_expected": "1.00",
            "related_loan": loan.pk,
        })

        payment = PaymentIntent.objects.get(
            member=self.member,
            payment_type="loan_repayment",
        )
        self.assertRedirects(
            response,
            reverse("payment_instructions", kwargs={"pk": payment.pk}),
        )
        self.assertEqual(payment.amount_expected, expected_balance)

    @patch("payments.views.timezone.localdate")
    def test_create_payment_page_sets_current_date_penalty_and_field_order(
        self,
        localdate,
    ):
        localdate.return_value = date(2026, 7, 11)
        self.client.force_login(self.member)

        response = self.client.get(reverse("create_payment"))
        content = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["monthly_contribution_penalty"],
            Decimal("50.00"),
        )
        self.assertContains(response, 'data-payment-group="payment-date"')
        self.assertContains(response, 'value="2026-07-11"')
        self.assertContains(response, 'data-payment-group="penalty"')
        self.assertContains(response, 'value="50.00"')
        self.assertLess(
            content.index('data-payment-group="loan"'),
            content.index('data-payment-group="amount"'),
        )

    def test_member_payment_history_only_lists_own_payments(self):
        other_member = get_user_model().objects.create_user(
            username="member2",
            password="password",
        )
        self.create_intent("OWN001", payment_type="other")
        PaymentIntent.objects.create(
            member=other_member,
            payment_type="other",
            amount_expected=Decimal("50.00"),
            customer_reference="OTHER001",
        )
        self.client.force_login(self.member)

        response = self.client.get(reverse("payment_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "OWN001")
        self.assertNotContains(response, "OTHER001")

    def test_member_cannot_view_another_members_payment_instructions(self):
        other_member = get_user_model().objects.create_user(
            username="member3",
            password="password",
        )
        other_payment = PaymentIntent.objects.create(
            member=other_member,
            payment_type="other",
            amount_expected=Decimal("50.00"),
            customer_reference="OTHER002",
        )
        self.client.force_login(self.member)

        response = self.client.get(
            reverse("payment_instructions", kwargs={"pk": other_payment.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_partial_notification_allows_amount_below_expected_amount(self):
        loan = Loan.objects.create(
            member=self.member,
            amount=Decimal("1000.00"),
            interest=Decimal("0.00"),
        )
        intent = self.create_intent(
            "LOAN002",
            payment_type="loan_repayment",
            amount=Decimal("1000.00"),
            related_loan=loan,
        )

        response = self.post_notification("TXN001", "LOAN002", "250.0")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["statusCode"], "0")
        intent.refresh_from_db()
        self.assertEqual(intent.status, "paid")
        self.assertIsNotNone(intent.paid_at)
        notification = KcbPaymentNotification.objects.get(
            transaction_reference="TXN001",
        )
        self.assertEqual(notification.transaction_amount, Decimal("250.00"))
        self.assertEqual(notification.customer_mobile_number, "254700000000")
        self.assertTrue(notification.processed)
        self.assertIsNotNone(notification.processed_at)
        loan.refresh_from_db()
        self.assertEqual(loan.total_paid_so_far, Decimal("250.00"))
        self.assertEqual(loan.repayment_status, "partially_paid")

    def test_fixed_notification_rejects_mismatched_amount(self):
        intent = self.create_intent(
            "MONTHLY002",
            payment_type="monthly_contribution",
            amount=Decimal("100.00"),
        )

        response = self.post_notification("TXN002", "MONTHLY002", "80.0")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["statusCode"], "1")
        self.assertEqual(response.json()["statusMessage"], "Amount mismatch")
        intent.refresh_from_db()
        self.assertEqual(intent.status, "pending")
        self.assertFalse(
            KcbPaymentNotification.objects.filter(
                transaction_reference="TXN002",
            ).exists()
        )

    def test_fixed_notification_accepts_decimal_equivalent_amount(self):
        month = timezone.localdate().replace(day=1)
        expected_amount = calculate_monthly_contribution_balance(self.member)
        intent = self.create_intent(
            "MONTHLY003",
            payment_type="monthly_contribution",
            amount=expected_amount,
            contribution_month=month,
        )

        response = self.post_notification("TXN003", "MONTHLY003", str(expected_amount))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["statusCode"], "0")
        intent.refresh_from_db()
        self.assertEqual(intent.status, "paid")
        contribution = Contribution.objects.get(member=self.member, month=month)
        self.assertEqual(contribution.amount, expected_amount)
        self.assertEqual(contribution.status, "fully_paid")

    def test_paid_fixed_intent_with_new_transaction_reference_does_not_post_twice(self):
        month = timezone.localdate().replace(day=1)
        expected_amount = calculate_monthly_contribution_balance(self.member)
        intent = self.create_intent(
            "MONTHLY005",
            payment_type="monthly_contribution",
            amount=expected_amount,
            contribution_month=month,
        )

        first_response = self.post_notification("TXN006", "MONTHLY005", str(expected_amount))
        second_response = self.post_notification("TXN007", "MONTHLY005", str(expected_amount))

        self.assertEqual(first_response.json()["statusCode"], "0")
        self.assertEqual(second_response.json()["statusCode"], "0")
        intent.refresh_from_db()
        self.assertEqual(intent.status, "paid")
        contribution = Contribution.objects.get(member=self.member, month=month)
        self.assertEqual(contribution.amount, expected_amount)
        self.assertEqual(
            KcbPaymentNotification.objects.filter(
                customer_reference="MONTHLY005",
                processed=True,
            ).count(),
            2,
        )

    def test_welfare_notification_creates_welfare_record(self):
        intent = self.create_intent(
            "WELFARE001",
            payment_type="welfare_contribution",
            amount=Decimal("300.00"),
        )

        response = self.post_notification("TXN005", "WELFARE001", "300.00")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["statusCode"], "0")
        intent.refresh_from_db()
        self.assertEqual(intent.status, "paid")
        welfare = Welfare.objects.get(member=self.member, amount=Decimal("300.00"))
        self.assertEqual(welfare.status, "fully_paid")
        self.assertIn("TXN005", welfare.description)

    def test_duplicate_transaction_reference_is_not_processed_twice(self):
        self.create_intent(
            "OTHER003",
            payment_type="other",
            amount=Decimal("1000.00"),
        )
        first_response = self.post_notification("TXN004", "OTHER003", "100.00")
        second_response = self.post_notification("TXN004", "OTHER003", "100.00")

        self.assertEqual(first_response.json()["statusCode"], "0")
        self.assertEqual(second_response.json()["statusCode"], "0")
        self.assertEqual(
            second_response.json()["statusMessage"],
            "Duplicate notification already received",
        )
        self.assertEqual(
            KcbPaymentNotification.objects.filter(
                transaction_reference="TXN004",
            ).count(),
            1,
        )
