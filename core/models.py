import secrets

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.conf import settings
from accounts.models import User
from datetime import date
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.utils import timezone

class Loan(models.Model):
    member=models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    amount=models.DecimalField(max_digits=10, decimal_places=2)
    total_paid_so_far=models.DecimalField(max_digits=10, decimal_places=2, default=0)
    interest=models.DecimalField(max_digits=10, decimal_places=2)
    status=models.CharField(max_length=20, choices= [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending",)
    repayment_status = models.CharField(
        max_length=20,
        choices=[
            ("not_paid", "Not Paid"),
            ("partially_paid", "Partially Paid"),
            ("fully_paid", "Fully Paid"),
            ("late", "Late"),
        ],
        default="not_paid",
    )
    repayment_updated_at = models.DateField(blank=True, null=True)
    loan_date=models.DateField(auto_now_add=True)
    due_date=models.DateField(blank=True, null=True)
    created_at=models.DateTimeField(auto_now_add=True)

    @classmethod
    def overdue_unpaid_queryset(cls, today=None):
        today = today or timezone.localdate()
        return cls.objects.filter(
            status="approved",
            due_date__isnull=False,
            due_date__lt=today,
            repayment_status__in=["not_paid", "partially_paid"],
        )

    @classmethod
    def mark_overdue_as_late(cls, today=None):
        today = today or timezone.localdate()
        return cls.overdue_unpaid_queryset(today=today).update(
            repayment_status="late",
            repayment_updated_at=today,
        )

    def save(self, *args, **kwargs):
        if not self.loan_date:
            self.loan_date = date.today()
        if not self.due_date:
            self.due_date = self.loan_date + relativedelta(months=1)
        self.interest = self.amount * Decimal("0.10")
        previous = None
        if self.pk:
            previous = Loan.objects.filter(pk=self.pk).values(
                "total_paid_so_far",
                "repayment_status",
                "repayment_updated_at",
            ).first()

        total_balance = self.amount + self.interest + self.penalty
        if self.total_paid_so_far < 0:
            self.total_paid_so_far = Decimal("0.00")
        if self.total_paid_so_far > total_balance:
            self.total_paid_so_far = total_balance

        if self.total_paid_so_far == 0:
            derived_status = "not_paid"
        elif self.total_paid_so_far < total_balance:
            derived_status = "partially_paid"
        else:
            derived_status = "fully_paid"

        is_overdue = (
            self.status == "approved"
            and self.due_date
            and timezone.localdate() > self.due_date
        )
        if derived_status != "fully_paid" and is_overdue:
            self.repayment_status = "late"
        else:
            self.repayment_status = derived_status

        payment_changed = not previous or (
            previous["total_paid_so_far"] != self.total_paid_so_far
            or previous["repayment_status"] != self.repayment_status
        )
        if payment_changed and (
            not previous
            or not self.repayment_updated_at
            or self.repayment_updated_at == previous["repayment_updated_at"]
        ):
            self.repayment_updated_at = date.today()
        super().save(*args, **kwargs)

    @property   
    def months_overdue(self):
        today=date.today()
        if self.due_date and today>self.due_date:
            diff=relativedelta(today, self.due_date)
            return diff.months + (diff.years*12)
        return 0

    @property
    def penalty(self):
        if self.repayment_status == "fully_paid":
            return Decimal("0.00")
        return self.amount * Decimal("0.10") * self.months_overdue
    
    @property
    def total_balance(self):
        return self.amount + self.interest + self.penalty
    
    def current_balance(self):
        remaining = self.total_balance - self.total_paid_so_far
        return remaining if remaining > 0 else Decimal("0.00")

    class Meta:
        permissions=[
            ("approve_loan", "Can approve loan"),
            ("reject_loan", "Can reject loan"),
        ]

        ordering=['-created_at']

    def __str__(self):
        return f"{self.member} - {self.amount}"

class Contribution(models.Model):
    member=models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="contributions")
    amount=models.DecimalField(max_digits=10, decimal_places=2)
    month=models.DateField()
    status=models.CharField(max_length=20, choices= [("fully_paid", "Fully Paid"), ("partially_paid", "Partially Paid"), ("late", "Late"), ("not_paid", "Not Yet Paid")], default="not_paid")
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(blank=True, null=True)

    @classmethod
    def overdue_not_paid_queryset(cls, today=None):
        today = today or timezone.localdate()
        month_start = today.replace(day=1)
        overdue_filter = models.Q(month__lt=month_start)
        if today.day > 10:
            overdue_filter |= models.Q(month=month_start)
        return cls.objects.filter(status="not_paid").filter(overdue_filter)

    @classmethod
    def mark_overdue_as_late(cls, today=None):
        return cls.overdue_not_paid_queryset(today=today).update(
            status="late",
            updated_at=timezone.now(),
        )

    def save(self, *args, **kwargs):
        today = timezone.localdate()
        month_start = today.replace(day=1)
        contribution_month_start = self.month.replace(day=1)
        is_overdue = contribution_month_start < month_start or (
            contribution_month_start == month_start and today.day > 10
        )
        if self.status == "not_paid" and is_overdue:
            self.status = "late"
        super().save(*args, **kwargs)

    class Meta:
        unique_together = ("member", "month")
        permissions=[
            ("export_contributions", "Can export contributions"),
            ("edit_contribution_amount", "Can edit contribution amount"),
        ]
        ordering=["-month", "-created_at"]

    def __str__(self):
        return f"{self.member} -{self.amount}"
    

class Welfare(models.Model): 
    WELFARE_DUE_MONTH = 6
    WELFARE_DUE_DAY = 15

    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="welfare_records")
    description = models.TextField(help_text="")
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="")
    date_given = models.DateField(auto_now_add=True)
    approved_by = models.CharField(max_length=100, blank=True, null=True, help_text="")
    updated_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=[
            ("partially_paid", "Partially Paid"),
            ("fully_paid", "Fully Paid"),
            ("late", "Late"),
            ("not_paid", "Not Paid"),
        ],
        default="not_paid",
    )

    @classmethod
    def overdue_unpaid_queryset(cls, today=None):
        today = today or timezone.localdate()
        cutoff = date(today.year, cls.WELFARE_DUE_MONTH, cls.WELFARE_DUE_DAY)
        overdue_filter = models.Q(date_given__year__lt=today.year)
        if today > cutoff:
            overdue_filter |= models.Q(date_given__year=today.year)
        return cls.objects.filter(status__in=["not_paid", "partially_paid"]).filter(
            overdue_filter
        )

    @classmethod
    def mark_overdue_as_late(cls, today=None):
        return cls.overdue_unpaid_queryset(today=today).update(
            status="late",
            updated_at=timezone.now(),
        )

    def save(self, *args, **kwargs):
        today = timezone.localdate()
        if not self.date_given:
            self.date_given = today
        cutoff = date(self.date_given.year, self.WELFARE_DUE_MONTH, self.WELFARE_DUE_DAY)
        if today > cutoff and self.status in {"not_paid", "partially_paid"}:
            self.status = "late"
        super().save(*args, **kwargs)

    class Meta:
        permissions=[
            ("edit_welfare_amount", "Can edit welfare amount"),
        ]
        ordering = ["-date_given"]

    def __str__(self):
        return f"{self.member} - {self.amount} ({self.status})"

from django.db import models
from django.conf import settings

class MeetingNote(models.Model):
    AUDIENCE_ALL = "all_members"
    AUDIENCE_COMMITTEE = "committee_only"
    AUDIENCE_CHOICES = [
        (AUDIENCE_ALL, "All members"),
        (AUDIENCE_COMMITTEE, "Committee only"),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    content = models.TextField(blank=True, help_text="Optional full text of minutes")
    file = models.FileField(upload_to="minutes/", blank=True, null=True)
    audience = models.CharField(
        max_length=20,
        choices=AUDIENCE_CHOICES,
        default=AUDIENCE_ALL,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="minutes_posted",
    )

    def __str__(self):
        return self.title



class Announcement(models.Model):
    title = models.CharField(max_length=200)
    message = models.TextField()
    published_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="announcements_posted",
    )
    related_name="announcements"

    class Meta:
        permissions = [
            ("make_announcement", "Can publish group announcements"),
        ]

User = get_user_model()

class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.URLField(blank=True, null=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} → {self.recipient.email}"


class LoanReminderLog(models.Model):
    REMINDER_DUE_SOON = "due_soon"
    REMINDER_OVERDUE = "overdue"
    REMINDER_TYPE_CHOICES = [
        (REMINDER_DUE_SOON, "Due soon"),
        (REMINDER_OVERDUE, "Overdue"),
    ]

    loan = models.ForeignKey(Loan, on_delete=models.CASCADE, related_name="reminder_logs")
    reminder_type = models.CharField(max_length=20, choices=REMINDER_TYPE_CHOICES)
    reminder_date = models.DateField()
    days_offset = models.IntegerField(help_text="Days until/since due date when reminder was sent.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("loan", "reminder_type", "reminder_date")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.loan_id} {self.reminder_type} {self.reminder_date}"


class LetterType(models.TextChoices):
    GENERAL = "general", "General committee letter"
    MEMBERSHIP_CONFIRMATION = "membership_confirmation", "Membership confirmation"
    LOAN_APPROVAL = "loan_approval", "Loan approval"
    LOAN_REPAYMENT_REMINDER = "loan_repayment_reminder", "Loan repayment reminder"
    LOAN_DEMAND = "loan_demand", "Loan demand letter"
    WELFARE_COMMUNICATION = "welfare_communication", "Welfare communication"
    CONTRIBUTION_REMINDER = "contribution_reminder", "Contribution reminder"
    APPOINTMENT_LETTER = "appointment_letter", "Appointment letter"
    REMOVAL_NOTICE = "removal_notice", "Removal notice"
    PARTNERSHIP_LETTER = "partnership_letter", "Partnership letter"
    DISCIPLINARY_COMMITTEE = "disciplinary_committee", "Disciplinary committee letter"


class LetterStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    RETURNED = "returned", "Returned"
    APPROVED = "approved", "Approved"
    ISSUED = "issued", "Issued"
    CANCELLED = "cancelled", "Cancelled"


class LetterAuditAction(models.TextChoices):
    CREATED = "created", "Letter created"
    EDITED = "edited", "Letter edited"
    SUBMITTED = "submitted", "Submitted"
    RETURNED = "returned", "Returned"
    APPROVED = "approved", "Approved"
    PDF_GENERATED = "pdf_generated", "PDF generated"
    ISSUED = "issued", "Issued"
    CANCELLED = "cancelled", "Cancelled"
    CORRECTION_CREATED = "correction_created", "Correction draft created"


class LetterTemplate(models.Model):
    LETTER_TYPE_CHOICES = LetterType.choices

    name = models.CharField(max_length=120)
    letter_type = models.CharField(max_length=40, choices=LETTER_TYPE_CHOICES)
    default_subject = models.CharField(max_length=220, blank=True)
    default_body = models.TextField(blank=True)
    default_closing = models.CharField(max_length=120, blank=True, default="Yours faithfully,")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Signatory(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="letter_signatory_profiles",
    )
    official_name = models.CharField(max_length=160)
    position = models.CharField(max_length=120)
    signature_image = models.ImageField(
        upload_to="protected/signatories/signatures/",
        blank=True,
        null=True,
    )
    stamp_image = models.ImageField(
        upload_to="protected/signatories/stamps/",
        blank=True,
        null=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "signatories"
        ordering = ["position", "official_name"]

    def __str__(self):
        return f"{self.official_name} - {self.position}"


class LetterReferenceSequence(models.Model):
    year = models.PositiveIntegerField(unique=True)
    last_number = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-year"]

    def __str__(self):
        return f"{self.year}: {self.last_number}"

    @classmethod
    def next_reference(cls, year=None):
        year = year or timezone.localdate().year
        with transaction.atomic():
            sequence, _created = cls.objects.select_for_update().get_or_create(
                year=year,
                defaults={"last_number": 0},
            )
            sequence.last_number += 1
            sequence.save(update_fields=["last_number", "updated_at"])
            return f"THYG/COM/{year}/{sequence.last_number:03d}"


class CommitteeLetter(models.Model):
    STATUS_DRAFT = LetterStatus.DRAFT
    STATUS_SUBMITTED = LetterStatus.SUBMITTED
    STATUS_RETURNED = LetterStatus.RETURNED
    STATUS_APPROVED = LetterStatus.APPROVED
    STATUS_ISSUED = LetterStatus.ISSUED
    STATUS_CANCELLED = LetterStatus.CANCELLED
    STATUS_CHOICES = LetterStatus.choices
    LETTER_TYPE_CHOICES = LetterType.choices

    EDITABLE_STATUSES = {LetterStatus.DRAFT, LetterStatus.RETURNED}
    LOCKED_STATUSES = {
        LetterStatus.SUBMITTED,
        LetterStatus.APPROVED,
        LetterStatus.ISSUED,
        LetterStatus.CANCELLED,
    }
    ALLOWED_LOCKED_FIELD_CHANGES = {
        "status",
        "reviewed_by",
        "review_comment",
        "approved_by",
        "approved_at",
        "issued_at",
        "pdf_file",
        "verification_code",
        "updated_at",
    }

    reference_number = models.CharField(max_length=30, unique=True, blank=True)
    letter_type = models.CharField(
        max_length=40,
        choices=LETTER_TYPE_CHOICES,
        default=LetterType.GENERAL,
    )
    letter_date = models.DateField(default=timezone.localdate)
    recipient_name = models.CharField(max_length=160)
    recipient_position = models.CharField(max_length=160, blank=True)
    recipient_organization = models.CharField(max_length=180, blank=True)
    recipient_address = models.TextField(blank=True, default="PO BOX 1109 ELDORET")
    salutation = models.CharField(max_length=120, default="Dear Sir/Madam,")
    subject = models.CharField(max_length=220)
    body = models.TextField()
    closing_phrase = models.CharField(max_length=120, default="Yours faithfully,")
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="corrections",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="committee_letters_created",
    )
    signatory = models.ForeignKey(
        Signatory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="letters",
    )
    signatory_name = models.CharField(max_length=160)
    signatory_position = models.CharField(max_length=120)
    supporting_attachment = models.FileField(
        upload_to="committee_letters/attachments/",
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="committee_letters_reviewed",
    )
    review_comment = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="committee_letters_approved",
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    issued_at = models.DateTimeField(blank=True, null=True)
    pdf_file = models.FileField(
        upload_to="committee_letters/generated/",
        blank=True,
        null=True,
    )
    verification_code = models.CharField(max_length=30, unique=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("approve_committee_letter", "Can approve committee letters"),
            ("issue_committee_letter", "Can issue committee letters"),
        ]

    def __str__(self):
        return self.reference_number or self.subject

    @property
    def is_locked(self):
        return self.status in self.LOCKED_STATUSES

    @property
    def is_approved_for_pdf(self):
        return self.status in {LetterStatus.APPROVED, LetterStatus.ISSUED}

    @property
    def can_edit(self):
        return self.status in self.EDITABLE_STATUSES

    @property
    def official_date(self):
        if self.issued_at:
            return timezone.localtime(self.issued_at).date()
        if self.approved_at:
            return timezone.localtime(self.approved_at).date()
        return self.letter_date

    def clean(self):
        super().clean()
        if not self.pk:
            return

        previous = CommitteeLetter.objects.filter(pk=self.pk).first()
        if not previous or previous.status in self.EDITABLE_STATUSES:
            return

        changed_locked_fields = []
        for field in self._meta.fields:
            if field.name in self.ALLOWED_LOCKED_FIELD_CHANGES:
                continue
            previous_value = getattr(previous, field.name)
            current_value = getattr(self, field.name)
            if isinstance(field, models.FileField):
                previous_value = previous_value.name
                current_value = current_value.name
            if previous_value != current_value:
                changed_locked_fields.append(field.name)

        if changed_locked_fields:
            raise ValidationError(
                "Only draft or returned committee letters can be edited. Create a new correction version when changes are needed."
            )

    def save(self, *args, **kwargs):
        if self.signatory:
            if not self.signatory_name:
                self.signatory_name = self.signatory.official_name
            if not self.signatory_position:
                self.signatory_position = self.signatory.position

        if not self.reference_number:
            self.reference_number = LetterReferenceSequence.next_reference(
                year=self.letter_date.year
            )

        if not self.verification_code:
            self.verification_code = self._build_verification_code()

        self.full_clean()
        super().save(*args, **kwargs)

    def _build_verification_code(self):
        reference = self.reference_number or LetterReferenceSequence.next_reference()
        reference_suffix = reference.replace("THYG/COM/", "THYG-").replace("/", "-")

        while True:
            code = f"{reference_suffix}-{secrets.token_hex(3).upper()}"
            if not CommitteeLetter.objects.filter(verification_code=code).exists():
                return code


class CommitteeLetterAudit(models.Model):
    ACTION_CHOICES = LetterAuditAction.choices

    letter = models.ForeignKey(
        CommitteeLetter,
        on_delete=models.CASCADE,
        related_name="audit_entries",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=40, choices=ACTION_CHOICES)
    status_from = models.CharField(max_length=20, choices=LetterStatus.choices, blank=True)
    status_to = models.CharField(max_length=20, choices=LetterStatus.choices, blank=True)
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.letter.reference_number} - {self.get_action_display()}"
