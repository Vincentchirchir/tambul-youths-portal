from datetime import date
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import Contribution, Loan, Welfare


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
