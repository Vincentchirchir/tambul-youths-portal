from django.db import models
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

        if not previous or (
            previous["total_paid_so_far"] != self.total_paid_so_far
            or previous["repayment_status"] != self.repayment_status
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
        unique_together = ('member', 'month')
        ordering = ['-month']


    class Meta:
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
