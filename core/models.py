from django.db import models
from django.conf import settings
from accounts.models import User
from datetime import date
from decimal import Decimal
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model

class Loan(models.Model):
    member=models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    amount=models.DecimalField(max_digits=10, decimal_places=2)
    interest=models.DecimalField(max_digits=10, decimal_places=2)
    status=models.CharField(max_length=20, choices= [("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected")], default="pending",)
    repayment_status = models.CharField(max_length=20,choices=[("not_paid", "Not Paid"),("partially_paid", "Partially Paid"),("fully_paid", "Fully Paid"),],default="not_paid",)
    repayment_updated_at = models.DateField(blank=True, null=True)
    loan_date=models.DateField(auto_now_add=True)
    due_date=models.DateField(blank=True, null=True)
    created_at=models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.loan_date:
            self.loan_date = date.today()
        if not self.due_date:
            self.due_date = self.loan_date + relativedelta(months=1)
        self.interest = self.amount * Decimal("0.10")
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
        return self.total_balance

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
    status=models.CharField(max_length=20, choices= [("fully_paid", "Fully Paid"), ("partially_paid", "Partially Paid"), ("late", "Late"), ("not_paid", "Not Yet Paid")], default="not_paid",)
    created_at=models.DateTimeField(auto_now_add=True)

    class Meta:
        permissions=[
            ("export_contributions", "Can export contributions")
        ]

        ordering=["-month", "-created_at"]

    def __str__(self):
        return f"{self.member} -{self.amount}"
    

class Welfare(models.Model): 
    member = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="welfare_records")
    description = models.TextField(help_text="")
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="")
    date_given = models.DateField(auto_now_add=True)
    approved_by = models.CharField(max_length=100, blank=True, null=True, help_text="")
    status = models.CharField(
        max_length=20,
        choices=[
            ("partially paid", "Partially Paid"),
            ("fully paid", "Fully Paid"),
            ("not paid", "Not Paid"),
        ],
        default="not paid",
    )

    class Meta:
        ordering = ["-date_given"]

    def __str__(self):
        return f"{self.member} - {self.amount} ({self.status})"

from django.db import models
from django.conf import settings

class MeetingNote(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    content = models.TextField(blank=True, help_text="Optional full text of minutes")
    file = models.FileField(upload_to="minutes/", blank=True, null=True)
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