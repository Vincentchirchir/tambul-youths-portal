from datetime import date
from unittest.mock import patch

from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.models import Contribution, Loan, Notification, Welfare
from core.services.notifications import (
    _to_absolute_link,
    normalize_notification_link,
    send_email_notifications,
)


class NotificationLinkNormalizationTests(SimpleTestCase):
    def test_legacy_render_link_normalizes_to_relative_path(self):
        link = "https://tambulyouths.onrender.com/member-dashboard?tab=loans#latest"

        normalized = normalize_notification_link(link)

        self.assertEqual(normalized, "/member-dashboard?tab=loans#latest")

    def test_external_link_is_preserved(self):
        link = "https://example.com/document.pdf"

        normalized = normalize_notification_link(link)

        self.assertEqual(normalized, link)

    @override_settings(SITE_BASE_URL="https://tambulyouths.onrender.com")
    def test_stale_site_base_url_uses_canonical_domain_for_emails(self):
        absolute_link = _to_absolute_link("/member-dashboard")

        self.assertEqual(absolute_link, "https://tambul.org/member-dashboard")

    @override_settings(
        NOTIFICATIONS_SEND_EMAILS=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="https://tambulyouths.onrender.com",
    )
    def test_notification_email_body_uses_tambul_org_link(self):
        recipient = User(email="member@example.com")

        send_email_notifications(
            recipients=[recipient],
            subject="Contribution Status Updated",
            message="Your contribution status was updated.",
            link="/member-dashboard",
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("https://tambul.org/member-dashboard", mail.outbox[0].body)


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


class CommitteeDashboardPeriodFilterTests(TestCase):
    def test_loan_totals_use_visible_table_balance_source_for_selected_month(self):
        committee = User.objects.create_user(
            username="period_committee",
            password="pass1234",
            role="committee",
        )
        january_member = User.objects.create_user(
            username="january_member",
            password="pass1234",
            role="member",
        )
        february_member = User.objects.create_user(
            username="february_member",
            password="pass1234",
            role="member",
        )
        rejected_member = User.objects.create_user(
            username="rejected_period_member",
            password="pass1234",
            role="member",
        )
        january_loan = Loan.objects.create(
            member=january_member,
            amount=1000,
            total_paid_so_far=200,
            interest=0,
            status="approved",
            due_date=date(2028, 1, 1),
        )
        february_loan = Loan.objects.create(
            member=february_member,
            amount=2000,
            total_paid_so_far=0,
            interest=0,
            status="approved",
            due_date=date(2028, 1, 1),
        )
        rejected_loan = Loan.objects.create(
            member=rejected_member,
            amount=5000,
            total_paid_so_far=0,
            interest=0,
            status="rejected",
            due_date=date(2028, 1, 1),
        )
        Loan.objects.filter(pk=january_loan.pk).update(loan_date=date(2026, 1, 15))
        Loan.objects.filter(pk=february_loan.pk).update(loan_date=date(2026, 2, 15))
        Loan.objects.filter(pk=rejected_loan.pk).update(loan_date=date(2026, 1, 20))
        january_loan.refresh_from_db()
        rejected_loan.refresh_from_db()

        self.client.force_login(committee)
        response = self.client.get(
            reverse("committee-dashboard"),
            {"year": "2026", "month": "1"},
        )

        loans = list(response.context["loans"])

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period_label"], "January 2026")
        self.assertEqual(response.context["total_loans"], 1)
        self.assertEqual(response.context["total_loan_disbursed"], 1000)
        self.assertEqual(response.context["total_loan_repaid"], 200)
        self.assertEqual(response.context["total_loan_outstanding"], january_loan.current_balance())
        self.assertEqual(set(loans), {january_loan, rejected_loan})
        self.assertContains(response, "loan-amount-rejected")
        self.assertContains(response, "Total Approved")
        self.assertContains(response, "Ksh 1000.00")
        self.assertContains(response, "Ksh 200.00")
        self.assertContains(response, "Ksh 900.00")


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
