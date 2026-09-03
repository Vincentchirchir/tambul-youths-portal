import shutil
import tempfile
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from core.forms import CommitteeLetterForm, DEFAULT_RECIPIENT_ADDRESS, LoanApplicationForm
from core.models import (
    Announcement,
    CommitteeLetter,
    CommitteeLetterAudit,
    Contribution,
    Loan,
    MeetingNote,
    Notification,
    Welfare,
)
from core.models import LetterAuditAction
from core.services.notifications import (
    _to_absolute_link,
    normalize_notification_link,
    send_email_notifications,
)
from core.services.committee_letters import committee_letter_recipient_lines
from core.services.loan_limits import loan_limit_remaining


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


class CommitteeLetterWorkflowTests(TestCase):
    def setUp(self):
        self.media_root = tempfile.mkdtemp()
        self.media_override = override_settings(MEDIA_ROOT=self.media_root)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

        self.creator = User.objects.create_user(
            username="secretary_letters",
            password="pass1234",
            role="secretary",
        )
        self.approver = User.objects.create_user(
            username="chair_letters",
            password="pass1234",
            role="chairperson",
        )

    def _letter(self, status=CommitteeLetter.STATUS_DRAFT):
        return CommitteeLetter.objects.create(
            letter_type="general",
            recipient_name="Recipient Name",
            recipient_organization="Recipient Organization",
            recipient_address="P.O. Box 1",
            subject="Membership confirmation",
            body="This confirms the requested committee correspondence.",
            created_by=self.creator,
            signatory_name="Authorized Official",
            signatory_position="Secretary",
            status=status,
        )

    def test_reference_and_verification_code_are_assigned(self):
        letter = self._letter()
        year = timezone.localdate().year

        self.assertEqual(letter.reference_number, f"THYG/COM/{year}/001")
        self.assertTrue(letter.verification_code.startswith(f"THYG-{year}-001-"))

    @override_settings(
        NOTIFICATIONS_SEND_EMAILS=True,
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SITE_BASE_URL="https://tambul.org",
    )
    def test_submit_notifies_chairperson_and_group_email(self):
        self.approver.email = "chairperson@example.com"
        self.approver.save(update_fields=["email"])
        letter = self._letter()
        self.client.force_login(self.creator)

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("committee-letter-submit", args=[letter.pk])
            )

        self.assertRedirects(
            response,
            reverse("committee-letter-detail", args=[letter.pk]),
            fetch_redirect_response=False,
        )
        letter.refresh_from_db()
        self.assertEqual(letter.status, CommitteeLetter.STATUS_SUBMITTED)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.approver,
                title="Committee Letter Submitted",
                link=f"/committee/letters/{letter.pk}/",
            ).exists()
        )
        email_recipients = {
            recipient
            for email in mail.outbox
            for recipient in email.to
        }
        self.assertIn("chairperson@example.com", email_recipients)
        self.assertIn("tambulhustleyouthgroup@gmail.com", email_recipients)

    def test_approval_generates_locked_record_pdf_and_audit_entries(self):
        letter = self._letter(status=CommitteeLetter.STATUS_SUBMITTED)
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("committee-letter-approve", args=[letter.pk])
        )

        self.assertRedirects(
            response,
            reverse("committee-letter-detail", args=[letter.pk]),
            fetch_redirect_response=False,
        )
        letter.refresh_from_db()
        self.assertEqual(letter.status, CommitteeLetter.STATUS_APPROVED)
        self.assertEqual(letter.approved_by, self.approver)
        self.assertIsNotNone(letter.approved_at)
        self.assertTrue(letter.pdf_file.name.endswith(".pdf"))
        self.assertTrue(letter.pdf_file.storage.exists(letter.pdf_file.name))
        self.assertEqual(
            list(
                CommitteeLetterAudit.objects.filter(letter=letter)
                .order_by("created_at")
                .values_list("action", flat=True)
            ),
            [LetterAuditAction.APPROVED, LetterAuditAction.PDF_GENERATED],
        )

    def test_approved_letter_content_cannot_be_edited(self):
        letter = self._letter(status=CommitteeLetter.STATUS_APPROVED)
        letter.subject = "Changed subject"

        with self.assertRaises(ValidationError):
            letter.save()

    def test_locked_letter_can_create_correction_draft_version(self):
        letter = self._letter(status=CommitteeLetter.STATUS_ISSUED)
        self.client.force_login(self.creator)

        response = self.client.post(
            reverse("committee-letter-status", args=[letter.pk, "correct"])
        )

        self.assertRedirects(
            response,
            reverse("committee-letter-list"),
            fetch_redirect_response=False,
        )
        correction = CommitteeLetter.objects.get(supersedes=letter)
        self.assertEqual(correction.status, CommitteeLetter.STATUS_DRAFT)
        self.assertEqual(correction.version, 2)
        self.assertNotEqual(correction.reference_number, letter.reference_number)
        self.assertTrue(
            CommitteeLetterAudit.objects.filter(
                letter=letter,
                action=LetterAuditAction.CORRECTION_CREATED,
                comment__contains=correction.reference_number,
            ).exists()
        )

    def test_member_cannot_access_committee_letter_list(self):
        member = User.objects.create_user(
            username="ordinary_member",
            password="pass1234",
            role="member",
        )
        self.client.force_login(member)

        response = self.client.get(reverse("committee-letter-list"))

        self.assertEqual(response.status_code, 403)

    def test_submitted_letter_cannot_be_edited(self):
        letter = self._letter(status=CommitteeLetter.STATUS_SUBMITTED)
        self.client.force_login(self.creator)

        response = self.client.get(reverse("committee-letter-edit", args=[letter.pk]))

        self.assertEqual(response.status_code, 403)

    def test_return_requires_review_permission_and_comment(self):
        letter = self._letter(status=CommitteeLetter.STATUS_SUBMITTED)
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse("committee-letter-return", args=[letter.pk]),
            {"comment": "Please correct the address."},
        )

        self.assertRedirects(response, reverse("committee-letter-detail", args=[letter.pk]))
        letter.refresh_from_db()
        self.assertEqual(letter.status, CommitteeLetter.STATUS_RETURNED)
        self.assertEqual(letter.reviewed_by, self.approver)
        self.assertEqual(letter.review_comment, "Please correct the address.")

    def test_only_chairperson_can_approve_or_issue_letters(self):
        admin = User.objects.create_user(
            username="admin_letters",
            password="pass1234",
            role="admin",
        )

        for user in [self.creator, admin]:
            submitted = self._letter(status=CommitteeLetter.STATUS_SUBMITTED)
            approved = self._letter(status=CommitteeLetter.STATUS_APPROVED)
            self.client.force_login(user)

            approve_url = reverse("committee-letter-approve", args=[submitted.pk])
            issue_url = reverse("committee-letter-issue", args=[approved.pk])

            self.assertEqual(self.client.get(approve_url).status_code, 403)
            self.assertEqual(self.client.post(approve_url).status_code, 403)
            self.assertEqual(self.client.get(issue_url).status_code, 403)
            self.assertEqual(self.client.post(issue_url).status_code, 403)

            submitted.refresh_from_db()
            approved.refresh_from_db()
            self.assertEqual(submitted.status, CommitteeLetter.STATUS_SUBMITTED)
            self.assertEqual(approved.status, CommitteeLetter.STATUS_APPROVED)

    def test_draft_preview_has_watermark_and_no_stamp(self):
        letter = self._letter()
        self.client.force_login(self.creator)

        response = self.client.get(reverse("committee-letter-preview", args=[letter.pk]))

        self.assertContains(response, "DRAFT - NOT OFFICIALLY APPROVED")
        self.assertNotContains(response, "/static/images/stamp.jpg")

    def test_approved_preview_has_stamp_date(self):
        letter = self._letter(status=CommitteeLetter.STATUS_APPROVED)
        CommitteeLetter.objects.filter(pk=letter.pk).update(
            approved_by=self.approver,
            approved_at=timezone.make_aware(datetime(2026, 8, 2, 9, 30)),
        )
        letter.refresh_from_db()
        self.client.force_login(self.approver)

        response = self.client.get(reverse("committee-letter-preview", args=[letter.pk]))

        self.assertNotContains(response, "DRAFT - NOT OFFICIALLY APPROVED")
        self.assertContains(response, "official-stamp")
        self.assertContains(response, "02 AUG 2026")

    def test_approved_pdf_download_refreshes_saved_pdf_for_chairperson(self):
        letter = self._letter(status=CommitteeLetter.STATUS_APPROVED)
        old_pdf_name = letter.pdf_file.storage.save(
            "committee_letters/generated/old-layout.pdf",
            ContentFile(b"old layout"),
        )
        CommitteeLetter.objects.filter(pk=letter.pk).update(pdf_file=old_pdf_name)
        letter.refresh_from_db()
        self.client.force_login(self.approver)

        with patch(
            "core.views.generate_committee_letter_pdf",
            return_value=b"fresh layout",
        ):
            response = self.client.get(reverse("committee-letter-pdf", args=[letter.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"fresh layout")

    def test_chairperson_can_explicitly_regenerate_issued_pdf(self):
        letter = self._letter(status=CommitteeLetter.STATUS_ISSUED)
        old_pdf_name = letter.pdf_file.storage.save(
            "committee_letters/generated/old-issued-layout.pdf",
            ContentFile(b"old issued layout"),
        )
        CommitteeLetter.objects.filter(pk=letter.pk).update(pdf_file=old_pdf_name)
        self.client.force_login(self.approver)

        with patch(
            "core.views.generate_committee_letter_pdf",
            return_value=b"fresh issued layout",
        ):
            response = self.client.post(
                reverse("committee-letter-generate-pdf", args=[letter.pk])
            )

        self.assertRedirects(
            response,
            reverse("committee-letter-detail", args=[letter.pk]),
            fetch_redirect_response=False,
        )
        letter.refresh_from_db()
        with letter.pdf_file.open("rb") as refreshed_pdf:
            self.assertEqual(refreshed_pdf.read(), b"fresh issued layout")

    def test_verification_page_shows_valid_metadata_without_body(self):
        letter = self._letter(status=CommitteeLetter.STATUS_ISSUED)

        response = self.client.get(
            reverse("letter-verify-result", args=[letter.verification_code])
        )

        self.assertContains(response, "Valid Official Letter")
        self.assertContains(response, letter.reference_number)
        self.assertContains(response, letter.subject)
        self.assertNotContains(response, letter.body)

    def test_letter_form_uses_member_dropdown_defaults_and_disciplinary_type(self):
        member = User.objects.create_user(
            username="recipient_member",
            first_name="Recipient",
            last_name="Member",
            password="pass1234",
            role="member",
        )

        form = CommitteeLetterForm()

        self.assertIn(
            ("Recipient Member", str(member)),
            list(form.fields["recipient_name"].choices),
        )
        self.assertEqual(
            list(form.fields["recipient_position"].choices),
            [
                ("Member", "Member"),
                ("Committee", "Committee"),
                ("Disciplinary Committee", "Disciplinary Committee"),
            ],
        )
        self.assertEqual(
            form.fields["recipient_address"].initial,
            DEFAULT_RECIPIENT_ADDRESS,
        )
        self.assertIn(
            ("disciplinary_committee", "Disciplinary committee letter"),
            list(form.fields["letter_type"].choices),
        )
        self.assertNotIn("recipient_organization", form.fields)
        self.assertNotIn("signatory", form.fields)

    def test_letter_form_supports_institution_recipients(self):
        form = CommitteeLetterForm(
            data={
                "letter_type": "partnership_letter",
                "letter_date": timezone.localdate().isoformat(),
                "recipient_type": "institution",
                "institution_type": "government",
                "institution_name": "Uasin Gishu County Government",
                "institution_department": "Department of Youth Affairs",
                "attention_name": "Director of Youth Affairs",
                "attention_position": "County Director",
                "institution_address": "P.O. Box 40\nEldoret",
                "institution_email": "director@example.go.ke",
                "institution_phone": "0700000000",
                "salutation": "Dear Sir/Madam,",
                "subject": "Partnership request",
                "body": "We request partnership support.",
                "closing_phrase": "Yours faithfully,",
                "signatory_name": "Authorized Official",
                "signatory_position": "Chairperson",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        letter = form.save(commit=False)

        self.assertEqual(letter.recipient_type, "institution")
        self.assertEqual(letter.recipient_name, "Director of Youth Affairs")
        self.assertEqual(letter.recipient_position, "County Director")
        self.assertEqual(letter.recipient_display_name, "Uasin Gishu County Government")
        self.assertEqual(letter.recipient_display_subtitle, "County Director")

    def test_institution_recipient_lines_use_official_address_block(self):
        letter = CommitteeLetter(
            recipient_type="institution",
            recipient_name="Director of Youth Affairs",
            recipient_position="County Director",
            institution_name="Uasin Gishu County Government",
            institution_department="Department of Youth Affairs",
            attention_name="Director of Youth Affairs",
            attention_position="County Director",
            institution_address="P.O. Box 40\nEldoret",
        )

        self.assertEqual(
            committee_letter_recipient_lines(letter),
            [
                "The County Director",
                "Department of Youth Affairs",
                "Uasin Gishu County Government",
                "P.O. Box 40",
                "Eldoret",
                "Attention: Director of Youth Affairs",
            ],
        )


class MemberDashboardPeriodFilterTests(TestCase):
    def test_selected_year_filters_loan_summary_announcements_and_meeting_notes(self):
        member = User.objects.create_user(
            username="period_member",
            password="pass1234",
            role="member",
        )
        loan_2025 = Loan.objects.create(
            member=member,
            amount=Decimal("500.00"),
            status="approved",
        )
        loan_2026 = Loan.objects.create(
            member=member,
            amount=Decimal("7000.00"),
            status="approved",
        )
        Loan.objects.filter(pk=loan_2025.pk).update(loan_date=date(2025, 2, 1))
        Loan.objects.filter(pk=loan_2026.pk).update(loan_date=date(2026, 2, 1))

        announcement_2025 = Announcement.objects.create(
            title="2025 announcement",
            message="This announcement belongs to 2025.",
        )
        announcement_2026 = Announcement.objects.create(
            title="2026 announcement",
            message="This announcement belongs to 2026.",
        )
        Announcement.objects.filter(pk=announcement_2025.pk).update(
            published_at=timezone.make_aware(datetime(2025, 2, 17, 10, 0))
        )
        Announcement.objects.filter(pk=announcement_2026.pk).update(
            published_at=timezone.make_aware(datetime(2026, 2, 17, 10, 0))
        )

        note_2025 = MeetingNote.objects.create(
            title="2025 minutes",
            description="Minutes for 2025.",
            audience=MeetingNote.AUDIENCE_ALL,
        )
        note_2026 = MeetingNote.objects.create(
            title="2026 minutes",
            description="Minutes for 2026.",
            audience=MeetingNote.AUDIENCE_ALL,
        )
        MeetingNote.objects.filter(pk=note_2025.pk).update(
            created_at=timezone.make_aware(datetime(2025, 3, 1, 10, 0))
        )
        MeetingNote.objects.filter(pk=note_2026.pk).update(
            created_at=timezone.make_aware(datetime(2026, 3, 1, 10, 0))
        )

        self.client.force_login(member)
        response = self.client.get(reverse("member-dashboard"), {"year": "2025"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_loan_count"], 1)
        self.assertEqual(response.context["outstanding_principal"], Decimal("500.00"))
        self.assertEqual([loan.pk for loan in response.context["loans"]], [loan_2025.pk])
        self.assertEqual(
            [announcement.pk for announcement in response.context["latest_announcements"]],
            [announcement_2025.pk],
        )
        self.assertEqual(
            [note.pk for note in response.context["meeting_notes"]],
            [note_2025.pk],
        )


class LoanLimitTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(
            username="loan_limit_member",
            password="pass1234",
            role="member",
        )

    def test_unpaid_principal_controls_remaining_loan_limit(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("2000.00"),
            total_paid_so_far=Decimal("1000.00"),
            status="approved",
        )

        self.assertEqual(loan_limit_remaining(self.member), Decimal("2000.00"))

    def test_fully_paid_and_rejected_loans_do_not_use_limit(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("3000.00"),
            total_paid_so_far=Decimal("3300.00"),
            status="approved",
        )
        Loan.objects.create(
            member=self.member,
            amount=Decimal("3000.00"),
            status="rejected",
        )

        self.assertEqual(loan_limit_remaining(self.member), Decimal("3000.00"))

    def test_dashboard_allows_application_when_limit_remains(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("1000.00"),
            status="approved",
        )
        self.client.force_login(self.member)

        response = self.client.get(reverse("member-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_apply_loan"])
        self.assertEqual(response.context["loan_limit_remaining"], Decimal("2000.00"))
        self.assertContains(response, "Available loan limit: Ksh 2,000")
        self.assertContains(response, reverse("apply-loan"))

    def test_dashboard_hides_apply_button_when_limit_is_reached(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("3000.00"),
            status="approved",
        )
        self.client.force_login(self.member)

        response = self.client.get(reverse("member-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_apply_loan"])
        self.assertNotContains(response, reverse("apply-loan"))
        self.assertContains(response, "You have reached your Ksh 3,000 loan limit")

    def test_apply_loan_page_redirects_when_limit_is_reached(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("3000.00"),
            status="approved",
        )
        self.client.force_login(self.member)

        response = self.client.get(reverse("apply-loan"))

        self.assertRedirects(
            response,
            reverse("member-dashboard"),
            fetch_redirect_response=False,
        )

    def test_loan_application_rejects_amount_above_remaining_limit(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("1000.00"),
            status="approved",
        )
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("apply-loan"),
            {"amount": "3000.00"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "You can only apply for up to Ksh 2,000. Reduce the amount to continue.",
        )
        self.assertEqual(Loan.objects.filter(member=self.member).count(), 1)

    def test_loan_application_allows_smaller_remaining_amount(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("2500.00"),
            status="approved",
        )
        self.client.force_login(self.member)

        response = self.client.post(
            reverse("apply-loan"),
            {"amount": "500.00"},
        )

        self.assertRedirects(
            response,
            reverse("member-dashboard"),
            fetch_redirect_response=False,
        )
        loan = Loan.objects.get(
            member=self.member,
            amount=Decimal("500.00"),
            status="pending",
        )
        self.assertEqual(loan.interest, Decimal("50.0000"))

    def test_form_sets_max_to_remaining_limit(self):
        Loan.objects.create(
            member=self.member,
            amount=Decimal("1000.00"),
            status="approved",
        )

        form = LoanApplicationForm(member=self.member)

        self.assertEqual(form.remaining_loan_limit, Decimal("2000.00"))
        self.assertEqual(form.fields["amount"].widget.attrs["max"], "2000.00")


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
    def test_member_section_filters_by_search_and_role(self):
        committee = User.objects.create_user(
            username="member_filter_committee",
            password="pass1234",
            role="committee",
        )
        target = User.objects.create_user(
            username="target_filter_member",
            first_name="Alice",
            last_name="Kiptoo",
            membership_number="THYG-100",
            password="pass1234",
            role="member",
        )
        User.objects.create_user(
            username="other_filter_secretary",
            first_name="Bob",
            last_name="Kimutai",
            membership_number="THYG-200",
            password="pass1234",
            role="secretary",
        )

        self.client.force_login(committee)
        response = self.client.get(
            reverse("committee-dashboard"),
            {"member_search": "Alice", "member_role": "member"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["members"]), [target])

    def test_loan_section_filters_by_member_status_repayment_and_balance(self):
        committee = User.objects.create_user(
            username="loan_filter_committee",
            password="pass1234",
            role="committee",
        )
        target_member = User.objects.create_user(
            username="alice_loan_filter",
            first_name="Alice",
            last_name="Loan",
            password="pass1234",
            role="member",
        )
        other_member = User.objects.create_user(
            username="bob_loan_filter",
            first_name="Bob",
            last_name="Loan",
            password="pass1234",
            role="member",
        )
        target_loan = Loan.objects.create(
            member=target_member,
            amount=1000,
            total_paid_so_far=0,
            interest=0,
            status="approved",
            due_date=date(2028, 1, 1),
        )
        other_loan = Loan.objects.create(
            member=other_member,
            amount=1000,
            total_paid_so_far=1100,
            interest=0,
            status="approved",
            due_date=date(2028, 1, 1),
        )
        Loan.objects.filter(pk=target_loan.pk).update(loan_date=date(2026, 3, 10))
        Loan.objects.filter(pk=other_loan.pk).update(loan_date=date(2026, 3, 12))

        self.client.force_login(committee)
        response = self.client.get(
            reverse("committee-dashboard"),
            {
                "year": "2026",
                "loan_member": "Alice",
                "loan_status": "approved",
                "loan_repayment_status": "not_paid",
                "loan_has_balance": "yes",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["loans"], [target_loan])
        self.assertEqual(response.context["filtered_loan_disbursed"], target_loan.amount)
        self.assertEqual(response.context["filtered_loan_repaid"], target_loan.total_paid_so_far)
        self.assertEqual(
            response.context["filtered_loan_outstanding"],
            target_loan.current_balance(),
        )

    def test_financial_letter_announcement_and_minute_filters(self):
        committee = User.objects.create_user(
            username="section_filter_committee",
            first_name="Filter",
            last_name="Committee",
            password="pass1234",
            role="committee",
        )
        member = User.objects.create_user(
            username="section_filter_member",
            first_name="Target",
            last_name="Member",
            password="pass1234",
            role="member",
        )
        other_member = User.objects.create_user(
            username="section_filter_other",
            first_name="Other",
            last_name="Member",
            password="pass1234",
            role="member",
        )
        contribution = Contribution.objects.create(
            member=member,
            amount=250,
            month=date(2026, 4, 1),
            status="fully_paid",
        )
        Contribution.objects.create(
            member=other_member,
            amount=100,
            month=date(2026, 4, 1),
            status="not_paid",
        )
        welfare = Welfare.objects.create(
            member=member,
            amount=500,
            description="Target welfare support",
            status="partially_paid",
        )
        Welfare.objects.filter(pk=welfare.pk).update(date_given=date(2026, 4, 8))
        Welfare.objects.create(
            member=other_member,
            amount=200,
            description="Other welfare",
            status="not_paid",
        )
        letter = CommitteeLetter.objects.create(
            letter_type="partnership_letter",
            recipient_type="institution",
            recipient_name="Director",
            recipient_position="Director",
            institution_name="Target Institution",
            institution_address="P.O. Box 1",
            subject="Target partnership",
            body="Partnership request.",
            created_by=committee,
            signatory_name="Authorized Official",
            signatory_position="Chairperson",
            status=CommitteeLetter.STATUS_DRAFT,
        )
        announcement = Announcement.objects.create(
            title="Target announcement",
            message="Filtered announcement body.",
        )
        other_announcement = Announcement.objects.create(
            title="Other announcement",
            message="Other body.",
        )
        Announcement.objects.filter(pk=announcement.pk).update(
            published_at=timezone.make_aware(datetime(2026, 4, 5, 10, 0))
        )
        Announcement.objects.filter(pk=other_announcement.pk).update(
            published_at=timezone.make_aware(datetime(2026, 4, 6, 10, 0))
        )
        minute = MeetingNote.objects.create(
            title="Target minutes",
            description="Filtered meeting note.",
        )
        MeetingNote.objects.create(
            title="Other minutes",
            description="Other meeting note.",
        )
        MeetingNote.objects.filter(pk=minute.pk).update(
            created_at=timezone.make_aware(datetime(2026, 4, 7, 10, 0))
        )

        self.client.force_login(committee)
        response = self.client.get(
            reverse("committee-dashboard"),
            {
                "year": "2026",
                "contribution_member": "Target",
                "contribution_status": "fully_paid",
                "welfare_member": "Target welfare",
                "welfare_status": "late",
                "letter_search": "Target Institution",
                "letter_recipient_type": "institution",
                "announcement_search": "Target announcement",
                "minute_search": "Target minutes",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["contributions"]), [contribution])
        self.assertEqual(response.context["filtered_contribution_total"], contribution.amount)
        self.assertContains(response, "Total Shown")
        self.assertContains(response, "Ksh 250.00")
        self.assertEqual(list(response.context["welfare_records"]), [welfare])
        self.assertEqual(list(response.context["committee_letters"]), [letter])
        self.assertEqual(list(response.context["latest_announcements"]), [announcement])
        self.assertEqual(list(response.context["meeting_notes"]), [minute])

    def test_my_summary_includes_payment_actions(self):
        committee = User.objects.create_user(
            username="payment_committee",
            password="pass1234",
            role="committee",
        )
        self.client.force_login(committee)

        response = self.client.get(reverse("committee-dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'href="{reverse("create_payment")}"')
        self.assertContains(response, f'href="{reverse("payment_history")}"')

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
        self.assertEqual(response.context["filtered_loan_disbursed"], january_loan.amount)
        self.assertEqual(response.context["filtered_loan_repaid"], january_loan.total_paid_so_far)
        self.assertEqual(
            response.context["filtered_loan_outstanding"],
            january_loan.current_balance(),
        )
        self.assertContains(response, "loan-amount-rejected")
        self.assertContains(response, "Shown Approved Total")
        self.assertContains(response, "Ksh 1000.00")
        self.assertContains(response, "Ksh 200.00")
        self.assertContains(response, "Ksh 900.00")
        self.assertNotContains(response, "loan.current_balance")

    def test_selected_year_filters_personal_summary_announcements_and_meeting_notes(self):
        committee = User.objects.create_user(
            username="summary_committee",
            password="pass1234",
            role="committee",
        )
        loan_2025 = Loan.objects.create(
            member=committee,
            amount=Decimal("500.00"),
            status="approved",
            due_date=date(2028, 1, 1),
        )
        loan_2026 = Loan.objects.create(
            member=committee,
            amount=Decimal("7000.00"),
            status="approved",
            due_date=date(2028, 1, 1),
        )
        Loan.objects.filter(pk=loan_2025.pk).update(loan_date=date(2025, 2, 1))
        Loan.objects.filter(pk=loan_2026.pk).update(loan_date=date(2026, 2, 1))

        announcement_2025 = Announcement.objects.create(
            title="Committee 2025 announcement",
            message="This announcement belongs to 2025.",
        )
        announcement_2026 = Announcement.objects.create(
            title="Committee 2026 announcement",
            message="This announcement belongs to 2026.",
        )
        Announcement.objects.filter(pk=announcement_2025.pk).update(
            published_at=timezone.make_aware(datetime(2025, 2, 17, 10, 0))
        )
        Announcement.objects.filter(pk=announcement_2026.pk).update(
            published_at=timezone.make_aware(datetime(2026, 2, 17, 10, 0))
        )

        note_2025 = MeetingNote.objects.create(
            title="Committee 2025 minutes",
            description="Minutes for 2025.",
        )
        note_2026 = MeetingNote.objects.create(
            title="Committee 2026 minutes",
            description="Minutes for 2026.",
        )
        MeetingNote.objects.filter(pk=note_2025.pk).update(
            created_at=timezone.make_aware(datetime(2025, 3, 1, 10, 0))
        )
        MeetingNote.objects.filter(pk=note_2026.pk).update(
            created_at=timezone.make_aware(datetime(2026, 3, 1, 10, 0))
        )

        self.client.force_login(committee)
        response = self.client.get(reverse("committee-dashboard"), {"year": "2025"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["my_active_loan_count"], 1)
        self.assertEqual(response.context["my_outstanding_principal"], Decimal("500.00"))
        self.assertEqual([loan.pk for loan in response.context["my_loans"]], [loan_2025.pk])
        self.assertEqual(
            [announcement.pk for announcement in response.context["latest_announcements"]],
            [announcement_2025.pk],
        )
        self.assertEqual(
            [announcement.pk for announcement in response.context["announcements"]],
            [announcement_2025.pk],
        )
        self.assertEqual(
            [note.pk for note in response.context["meeting_notes"]],
            [note_2025.pk],
        )


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
