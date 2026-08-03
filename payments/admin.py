from django.contrib import admin

from .models import KcbPaymentNotification, PaymentIntent


@admin.register(PaymentIntent)
class PaymentIntentAdmin(admin.ModelAdmin):
    list_display = (
        "customer_reference",
        "member",
        "payment_type",
        "amount_expected",
        "status",
        "created_at",
        "validated_at",
        "paid_at",
    )
    list_filter = ("payment_type", "status", "created_at")
    search_fields = (
        "customer_reference",
        "member__username",
        "member__email",
        "member__first_name",
        "member__last_name",
    )
    readonly_fields = ("created_at", "validated_at", "paid_at")


@admin.register(KcbPaymentNotification)
class KcbPaymentNotificationAdmin(admin.ModelAdmin):
    list_display = (
        "transaction_reference",
        "customer_reference",
        "transaction_amount",
        "currency",
        "processed",
        "created_at",
        "processed_at",
    )
    list_filter = ("processed", "currency", "created_at")
    search_fields = (
        "transaction_reference",
        "customer_reference",
        "customer_name",
        "customer_mobile_number",
    )
    readonly_fields = ("created_at", "processed_at", "raw_payload")
