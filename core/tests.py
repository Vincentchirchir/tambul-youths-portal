from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import Contribution, Loan, LoanPayment, Notification, Welfare


class ContributionAmountUpdateTests(TestCase):
    def setUp(self):
        self.treasurer = User.objects.create_user(
            username="treasurer1",
            password="pass1234",
            role="treasurer",
        )
        self.member = User.objects.create_user(
            username="member1",
            password="pass1234",
            role="member",
        )
        today = timezone.localdate()
        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)

        self.contribution = Contribution.objects.create(
            member=self.member,
            amount=0,
            month=next_month,
            status="not_paid",
        )
        self.url = reverse("update-contrib-amount", args=[self.contribution.pk])

    def test_amount_200_or_more_sets_fully_paid(self):
        self.client.force_login(self.treasurer)

        response = self.client.post(self.url, {"amount": "200"})

        self.assertRedirects(response, reverse("committee-dashboard"))
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.status, "fully_paid")

    def test_amount_below_200_and_above_zero_sets_partially_paid(self):
        self.client.force_login(self.treasurer)

        response = self.client.post(self.url, {"amount": "199.99"})

        self.assertRedirects(response, reverse("committee-dashboard"))
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.status, "partially_paid")

    def test_amount_zero_sets_not_paid(self):
        self.client.force_login(self.treasurer)

        response = self.client.post(self.url, {"amount": "0"})

        self.assertRedirects(response, reverse("committee-dashboard"))
        self.contribution.refresh_from_db()
        self.assertEqual(self.contribution.status, "not_paid")


class ContributionLateStatusRulesTests(TestCase):
    @patch("core.models.timezone.localdate")
    def test_not_paid_current_month_becomes_late_after_10th(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 16)
        member = User.objects.create_user(
            username="member2",
            password="pass1234",
            role="member",
        )
        contribution = Contribution.objects.create(
            member=member,
            amount=0,
            month=date(2026, 2, 1),
            status="not_paid",
        )
        self.assertEqual(contribution.status, "late")

    @patch("core.models.timezone.localdate")
    def test_not_paid_current_month_stays_not_paid_on_or_before_10th(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 10)
        member = User.objects.create_user(
            username="member3",
            password="pass1234",
            role="member",
        )
        contribution = Contribution.objects.create(
            member=member,
            amount=0,
            month=date(2026, 2, 1),
            status="not_paid",
        )
        self.assertEqual(contribution.status, "not_paid")

    @patch("core.models.timezone.localdate")
    def test_mark_overdue_as_late_updates_existing_not_paid_records(self, mock_localdate):
        mock_localdate.return_value = date(2026, 1, 5)
        member = User.objects.create_user(
            username="member4",
            password="pass1234",
            role="member",
        )
        contribution = Contribution.objects.create(
            member=member,
            amount=0,
            month=date(2026, 1, 1),
            status="not_paid",
        )
        self.assertEqual(contribution.status, "not_paid")

        Contribution.mark_overdue_as_late(today=date(2026, 2, 16))
        contribution.refresh_from_db()

        self.assertEqual(contribution.status, "late")


class LoanLateStatusRulesTests(TestCase):
    @patch("core.models.timezone.localdate")
    def test_overdue_approved_unpaid_loan_becomes_late_on_save(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 16)
        member = User.objects.create_user(
            username="member5",
            password="pass1234",
            role="member",
        )
        loan = Loan.objects.create(
            member=member,
            amount=1000,
            total_paid_so_far=0,
            interest=0,
            status="approved",
            due_date=date(2026, 2, 1),
            repayment_status="not_paid",
        )

        self.assertEqual(loan.repayment_status, "late")

    @patch("core.models.timezone.localdate")
    def test_mark_overdue_as_late_updates_existing_overdue_loans(self, mock_localdate):
        mock_localdate.return_value = date(2026, 1, 10)
        member = User.objects.create_user(
            username="member6",
            password="pass1234",
            role="member",
        )
        loan = Loan.objects.create(
            member=member,
            amount=1000,
            total_paid_so_far=0,
            interest=0,
            status="approved",
            due_date=date(2026, 2, 1),
            repayment_status="not_paid",
        )
        self.assertEqual(loan.repayment_status, "not_paid")

        Loan.mark_overdue_as_late(today=date(2026, 2, 16))
        loan.refresh_from_db()

        self.assertEqual(loan.repayment_status, "late")


class LoanPaymentTests(TestCase):
    def test_total_paid_update_records_dated_payment_delta(self):
        chairperson = User.objects.create_user(
            username="loan_chair",
            password="pass1234",
            role="chairperson",
        )
        member = User.objects.create_user(
            username="loan_member",
            password="pass1234",
            role="member",
        )
        loan = Loan.objects.create(
            member=member,
            amount=1000,
            total_paid_so_far=0,
            interest=0,
            status="approved",
            due_date=date(2028, 1, 1),
        )
        payment_date = timezone.localdate()

        self.client.force_login(chairperson)
        response = self.client.post(
            reverse("update-loan-total-paid", args=[loan.pk]),
            {
                "total_paid_so_far": "300.00",
                "payment_date": payment_date.isoformat(),
            },
        )

        self.assertRedirects(response, reverse("committee-dashboard"))
        loan.refresh_from_db()
        payment = LoanPayment.objects.get(loan=loan)

        self.assertEqual(loan.total_paid_so_far, Decimal("300.00"))
        self.assertEqual(payment.amount, Decimal("300.00"))
        self.assertEqual(payment.payment_date, payment_date)
        self.assertEqual(payment.recorded_by, chairperson)

    def test_selected_year_opening_balance_carries_previous_unpaid_loan(self):
        committee = User.objects.create_user(
            username="carry_committee",
            password="pass1234",
            role="committee",
        )
        member = User.objects.create_user(
            username="carry_member",
            password="pass1234",
            role="member",
        )
        loan = Loan.objects.create(
            member=member,
            amount=1000,
            total_paid_so_far=300,
            interest=0,
            status="approved",
            due_date=date(2028, 1, 1),
        )
        Loan.objects.filter(pk=loan.pk).update(
            loan_date=date(2026, 6, 1),
            due_date=date(2028, 1, 1),
            repayment_status="partially_paid",
        )
        loan.refresh_from_db()
        LoanPayment.objects.create(
            loan=loan,
            amount=300,
            payment_date=date(2026, 12, 20),
            recorded_by=committee,
        )

        self.client.force_login(committee)
        response = self.client.get(reverse("committee-dashboard"), {"year": "2027"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["opening_loan_balance"], Decimal("800.00"))
        self.assertEqual(response.context["total_loan_disbursed"], 0)
        self.assertEqual(response.context["total_loan_repaid"], 0)


class CommitteeDashboardAnalyticsTests(TestCase):
    def test_top_loan_applicants_include_only_approved_loans(self):
        committee = User.objects.create_user(
            username="committee1",
            password="pass1234",
            role="committee",
        )
        approved_member = User.objects.create_user(
            username="approved_member",
            password="pass1234",
            role="member",
        )
        rejected_member = User.objects.create_user(
            username="rejected_member",
            password="pass1234",
            role="member",
        )

        Loan.objects.create(
            member=approved_member,
            amount=1000,
            total_paid_so_far=0,
            interest=0,
            status="approved",
        )
        Loan.objects.create(
            member=rejected_member,
            amount=9000,
            total_paid_so_far=0,
            interest=0,
            status="rejected",
        )

        self.client.force_login(committee)
        response = self.client.get(reverse("committee-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["top_contributors"], ["approved_member"])
        self.assertEqual(response.context["top_contrib_values"], [1000.0])


class NotificationReadAllTests(TestCase):
    def test_mark_all_read_updates_only_current_users_notifications(self):
        member = User.objects.create_user(
            username="notification_member",
            password="pass1234",
            role="member",
        )
        other_member = User.objects.create_user(
            username="other_notification_member",
            password="pass1234",
            role="member",
        )
        unread = Notification.objects.create(
            recipient=member,
            title="Unread",
            message="Unread notification",
        )
        already_read = Notification.objects.create(
            recipient=member,
            title="Read",
            message="Read notification",
            is_read=True,
        )
        other_unread = Notification.objects.create(
            recipient=other_member,
            title="Other unread",
            message="Other notification",
        )

        self.client.force_login(member)
        response = self.client.post(
            reverse("mark_notifications_read_all"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["unread_count"], 0)

        unread.refresh_from_db()
        already_read.refresh_from_db()
        other_unread.refresh_from_db()

        self.assertTrue(unread.is_read)
        self.assertTrue(already_read.is_read)
        self.assertFalse(other_unread.is_read)


class WelfareLateStatusRulesTests(TestCase):
    @patch("core.models.timezone.localdate")
    def test_after_june_15_unpaid_welfare_becomes_late_on_save(self, mock_localdate):
        mock_localdate.return_value = date(2026, 6, 16)
        member = User.objects.create_user(
            username="member7",
            password="pass1234",
            role="member",
        )
        welfare = Welfare.objects.create(
            member=member,
            description="Medical support",
            amount=500,
            status="not_paid",
        )
        self.assertEqual(welfare.status, "late")

    @patch("core.models.timezone.localdate")
    def test_before_june_15_current_year_welfare_stays_not_paid(self, mock_localdate):
        mock_localdate.return_value = date(2026, 6, 15)
        member = User.objects.create_user(
            username="member8",
            password="pass1234",
            role="member",
        )
        welfare = Welfare.objects.create(
            member=member,
            description="School fees support",
            amount=800,
            status="not_paid",
        )
        self.assertEqual(welfare.status, "not_paid")

    @patch("core.models.timezone.localdate")
    def test_mark_overdue_as_late_updates_existing_records(self, mock_localdate):
        mock_localdate.return_value = date(2026, 6, 10)
        member = User.objects.create_user(
            username="member9",
            password="pass1234",
            role="member",
        )
        welfare = Welfare.objects.create(
            member=member,
            description="Emergency support",
            amount=900,
            status="partially_paid",
        )
        self.assertEqual(welfare.status, "partially_paid")

        Welfare.mark_overdue_as_late(today=date(2026, 6, 16))
        welfare.refresh_from_db()

        self.assertEqual(welfare.status, "late")
