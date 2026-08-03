from django.conf import settings
from django.db import models
from django.utils import timezone


class PaymentIntent(models.Model):
    PAYMENT_TYPES = [
        ("monthly_contribution", "Monthly Contribution"),
        ("loan_repayment", "Loan Repayment"),
        ("welfare_contribution", "Welfare Contribution"),
        ("registration_fee", "Registration Fee"),
        ("penalty", "Penalty"),
        ("other", "Other"),
    ]

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("validated", "Validated"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("expired", "Expired"),
    ]

    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="payment_intents",
    )

    payment_type = models.CharField(max_length=50, choices=PAYMENT_TYPES)
    amount_expected = models.DecimalField(max_digits=12, decimal_places=2)
    customer_reference = models.CharField(max_length=60, unique=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    related_loan = models.ForeignKey(
        "core.Loan",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_intents",
    )

    contribution_month = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    validated_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.customer_reference} - {self.member} - {self.amount_expected}"

    def mark_validated(self):
        self.status = "validated"
        self.validated_at = timezone.now()
        self.save(update_fields=["status", "validated_at"])

    def mark_paid(self):
        self.status = "paid"
        self.paid_at = timezone.now()
        self.save(update_fields=["status", "paid_at"])


class KcbPaymentNotification(models.Model):
    payment_intent = models.ForeignKey(
        PaymentIntent,
        on_delete=models.PROTECT,
        related_name="kcb_notifications",
        null=True,
        blank=True,
    )
    transaction_reference = models.CharField(max_length=100, unique=True)
    request_id = models.CharField(max_length=100, blank=True, null=True)

    customer_reference = models.CharField(max_length=100)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    customer_mobile_number = models.CharField(max_length=30, blank=True, null=True)

    transaction_amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="KES")

    channel_code = models.CharField(max_length=50, blank=True, null=True)
    narration = models.TextField(blank=True, null=True)
    credit_account_identifier = models.CharField(max_length=100, blank=True, null=True)
    organization_short_code = models.CharField(max_length=100, blank=True, null=True)
    till_number = models.CharField(max_length=100, blank=True, null=True)

    raw_payload = models.JSONField()
    processed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.transaction_reference} - {self.customer_reference}"

    def mark_processed(self):
        self.processed = True
        self.processed_at = timezone.now()
        self.save(update_fields=["processed", "processed_at"])
