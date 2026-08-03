from django.core.exceptions import PermissionDenied, ValidationError
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from core.models import CommitteeLetter, CommitteeLetterAudit, LetterAuditAction
from core.services.notifications import notify_users, send_email_to_addresses


COMMITTEE_ROLES = {
    "chairperson",
    "vice-chairperson",
    "treasurer",
    "secretary",
    "vice-secretary",
    "welfare",
    "coordinator",
    "admin",
    "committee",
}

REVIEW_ROLES = {
    "chairperson",
    "vice-chairperson",
    "secretary",
    "vice-secretary",
    "admin",
}

APPROVER_ROLES = {
    "chairperson",
}

ISSUER_ROLES = {
    "chairperson",
}

LETTER_SUBMISSION_EMAIL = "tambulhustleyouthgroup@gmail.com"


def is_committee_user(user):
    return user.is_authenticated and getattr(user, "role", "") in COMMITTEE_ROLES


def can_edit_letter(user, letter):
    if not is_committee_user(user) or not letter.can_edit:
        return False
    return (
        letter.created_by_id == user.id
        or getattr(user, "role", "") in {"chairperson", "secretary", "admin"}
    )


def can_review_letters(user):
    return getattr(user, "role", "") in REVIEW_ROLES


def can_approve_letters(user):
    return getattr(user, "role", "") in APPROVER_ROLES


def can_issue_letters(user):
    return getattr(user, "role", "") in ISSUER_ROLES


def ensure_allowed(allowed, message="You are not authorized to perform this action."):
    if not allowed:
        raise PermissionDenied(message)


def record_audit(letter, actor, action, status_from="", status_to="", comment=""):
    return CommitteeLetterAudit.objects.create(
        letter=letter,
        actor=actor,
        action=action,
        status_from=status_from or "",
        status_to=status_to or "",
        comment=comment or "",
    )


def notify_letter_submitted(letter, actor):
    User = get_user_model()
    chairpersons = list(User.objects.filter(role="chairperson", is_active=True))
    title = "Committee Letter Submitted"
    actor_name = actor.get_full_name() or actor.username
    message = (
        f"{actor_name} submitted letter {letter.reference_number} "
        f"for chairperson approval."
    )
    link = f"/committee/letters/{letter.pk}/"

    notify_users(
        recipients=chairpersons,
        title=title,
        message=message,
        link=link,
        send_email=True,
    )

    chairperson_emails = {
        (chairperson.email or "").strip().lower()
        for chairperson in chairpersons
        if chairperson.email
    }
    notification_email = (
        getattr(
            settings,
            "COMMITTEE_LETTER_SUBMISSION_EMAIL",
            LETTER_SUBMISSION_EMAIL,
        )
        or ""
    ).strip()
    if notification_email and notification_email.lower() not in chairperson_emails:
        send_email_to_addresses(
            [notification_email],
            subject=title,
            message=message,
            link=link,
        )


@transaction.atomic
def submit_letter(letter, actor):
    ensure_allowed(can_edit_letter(actor, letter))
    if letter.status != CommitteeLetter.STATUS_DRAFT and letter.status != CommitteeLetter.STATUS_RETURNED:
        raise ValidationError("Only draft or returned letters can be submitted.")
    before = letter.status
    letter.status = CommitteeLetter.STATUS_SUBMITTED
    letter.review_comment = ""
    letter.save(update_fields=["status", "review_comment", "updated_at"])
    record_audit(letter, actor, LetterAuditAction.SUBMITTED, before, letter.status)
    transaction.on_commit(lambda: notify_letter_submitted(letter, actor))
    return letter


@transaction.atomic
def return_letter(letter, actor, comment):
    ensure_allowed(can_review_letters(actor))
    if letter.status != CommitteeLetter.STATUS_SUBMITTED:
        raise ValidationError("Only submitted letters can be returned for correction.")
    before = letter.status
    letter.status = CommitteeLetter.STATUS_RETURNED
    letter.reviewed_by = actor
    letter.review_comment = comment
    letter.save(update_fields=["status", "reviewed_by", "review_comment", "updated_at"])
    record_audit(letter, actor, LetterAuditAction.RETURNED, before, letter.status, comment)
    return letter


@transaction.atomic
def approve_letter(letter, actor, pdf_bytes=None, filename=None):
    ensure_allowed(can_approve_letters(actor))
    if letter.status != CommitteeLetter.STATUS_SUBMITTED:
        raise ValidationError("Only submitted letters can be approved.")
    before = letter.status
    letter.status = CommitteeLetter.STATUS_APPROVED
    letter.approved_by = actor
    letter.approved_at = timezone.now()
    letter.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    record_audit(letter, actor, LetterAuditAction.APPROVED, before, letter.status)
    return letter


@transaction.atomic
def issue_letter(letter, actor):
    ensure_allowed(can_issue_letters(actor))
    if letter.status != CommitteeLetter.STATUS_APPROVED:
        raise ValidationError("Only approved letters can be issued.")
    before = letter.status
    letter.status = CommitteeLetter.STATUS_ISSUED
    letter.issued_at = timezone.now()
    letter.save(update_fields=["status", "issued_at", "updated_at"])
    record_audit(letter, actor, LetterAuditAction.ISSUED, before, letter.status)
    return letter


@transaction.atomic
def cancel_letter(letter, actor, comment=""):
    ensure_allowed(can_review_letters(actor))
    if letter.status in {CommitteeLetter.STATUS_ISSUED, CommitteeLetter.STATUS_CANCELLED}:
        raise ValidationError("Issued or already cancelled letters cannot be cancelled.")
    before = letter.status
    letter.status = CommitteeLetter.STATUS_CANCELLED
    letter.save(update_fields=["status", "updated_at"])
    record_audit(letter, actor, LetterAuditAction.CANCELLED, before, letter.status, comment)
    return letter


@transaction.atomic
def create_correction_draft(letter, actor):
    ensure_allowed(is_committee_user(actor))
    if letter.status not in {CommitteeLetter.STATUS_APPROVED, CommitteeLetter.STATUS_ISSUED}:
        raise ValidationError("Only approved or issued letters need correction versions.")

    correction = CommitteeLetter.objects.create(
        letter_type=letter.letter_type,
        letter_date=timezone.localdate(),
        recipient_type=letter.recipient_type,
        recipient_name=letter.recipient_name,
        recipient_position=letter.recipient_position,
        recipient_organization="",
        recipient_address=letter.recipient_address,
        institution_type=letter.institution_type,
        institution_name=letter.institution_name,
        institution_department=letter.institution_department,
        attention_name=letter.attention_name,
        attention_position=letter.attention_position,
        institution_address=letter.institution_address,
        institution_email=letter.institution_email,
        institution_phone=letter.institution_phone,
        salutation=letter.salutation,
        subject=letter.subject,
        body=letter.body,
        closing_phrase=letter.closing_phrase,
        supersedes=letter,
        created_by=actor,
        signatory=letter.signatory,
        signatory_name=letter.signatory_name,
        signatory_position=letter.signatory_position,
        version=letter.version + 1,
    )
    record_audit(
        letter,
        actor,
        LetterAuditAction.CORRECTION_CREATED,
        letter.status,
        letter.status,
        comment=f"New version: {correction.reference_number}",
    )
    record_audit(
        correction,
        actor,
        LetterAuditAction.CREATED,
        status_to=correction.status,
        comment=f"Supersedes {letter.reference_number}",
    )
    return correction
