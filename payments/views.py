import json
import uuid
from decimal import Decimal, InvalidOperation
from ipaddress import ip_address, ip_network

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, FormView, ListView

from core.permissions import can_view_payment_intent

from .forms import PaymentIntentForm
from .models import KcbPaymentNotification, PaymentIntent
from .services import (
    amounts_match,
    current_month_contribution_penalty,
    generate_customer_reference,
    get_bill_type,
    get_month_start,
    post_successful_payment,
)


def kcb_error_response(message, status=200):
    return JsonResponse(
        {
            "transactionID": str(uuid.uuid4()),
            "statusCode": "1",
            "statusMessage": message,
        },
        status=status,
    )


def kcb_success_response(message):
    return JsonResponse(
        {
            "transactionID": str(uuid.uuid4()),
            "statusCode": "0",
            "statusMessage": message,
        }
    )


def kcb_validation_error_response(message, status=200):
    return JsonResponse(
        {
            "transactionID": str(uuid.uuid4()),
            "statusCode": "1",
            "statusMessage": message,
            "CustomerName": "",
            "billAmount": "0.00",
            "currency": "KES",
            "billType": "FIXED",
            "creditAccountIdentifier": "",
        },
        status=status,
    )


SENSITIVE_KCB_PAYLOAD_KEYS = {
    "customername",
    "customermobilenumber",
    "customermobile",
    "customerphone",
    "phone",
    "mobile",
    "msisdn",
    "narration",
}
SENSITIVE_KCB_PAYLOAD_PARTS = {
    "authorization",
    "credential",
    "password",
    "secret",
    "signature",
    "token",
}


def _redacted_value(value):
    if value in (None, ""):
        return value
    return "[redacted]"


def _is_sensitive_payload_key(key):
    normalized = str(key).replace("_", "").replace("-", "").lower()
    return normalized in SENSITIVE_KCB_PAYLOAD_KEYS or any(
        part in normalized for part in SENSITIVE_KCB_PAYLOAD_PARTS
    )


def redact_kcb_payload(payload):
    if isinstance(payload, dict):
        return {
            key: _redacted_value(value)
            if _is_sensitive_payload_key(key)
            else redact_kcb_payload(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_kcb_payload(item) for item in payload]
    return payload


def _callback_client_ip(request):
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _client_ip_allowed(client_ip, allowed_entries):
    if not allowed_entries:
        return True
    try:
        address = ip_address(client_ip)
    except ValueError:
        return False

    for entry in allowed_entries:
        try:
            if address in ip_network(entry, strict=False):
                return True
        except ValueError:
            if client_ip == entry:
                return True
    return False


def kcb_callback_is_authorized(request):
    required_secret = getattr(settings, "KCB_CALLBACK_SECRET", "")
    if required_secret:
        provided_secret = request.headers.get("X-KCB-Callback-Secret", "")
        if not constant_time_compare(provided_secret, required_secret):
            return False

    allowed_ips = getattr(settings, "KCB_CALLBACK_ALLOWED_IPS", [])
    return _client_ip_allowed(_callback_client_ip(request), allowed_ips)


@method_decorator(csrf_exempt, name="dispatch")
class KcbBillValidationView(View):
    """
    KCB calls this endpoint before accepting/processing a payment.
    This validates the payment reference only.
    It does NOT mark the payment as paid.
    """

    def post(self, request, *args, **kwargs):
        if not kcb_callback_is_authorized(request):
            return kcb_validation_error_response("Unauthorized callback", status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return kcb_validation_error_response("Invalid JSON", status=400)

        if not isinstance(payload, dict):
            return kcb_validation_error_response("Invalid JSON", status=400)

        customer_reference = payload.get("customerReference")

        if not customer_reference:
            return kcb_validation_error_response("Missing customerReference")

        try:
            payment_intent = PaymentIntent.objects.get(
                customer_reference=customer_reference,
                status__in=["pending", "validated"],
            )
        except PaymentIntent.DoesNotExist:
            return kcb_validation_error_response(
                "Invalid or expired payment reference",
            )

        if payment_intent.status == "pending":
            payment_intent.mark_validated()

        member_name = (
            payment_intent.member.get_full_name()
            or payment_intent.member.username
        )

        return JsonResponse(
            {
                "transactionID": str(uuid.uuid4()),
                "statusCode": "0",
                "statusMessage": "Success",
                "CustomerName": member_name,
                "billAmount": str(payment_intent.amount_expected),
                "currency": "KES",
                "billType": get_bill_type(payment_intent),
                "creditAccountIdentifier": settings.KCB_GROUP_ACCOUNT_NUMBER,
            }
        )

    def get(self, request, *args, **kwargs):
        return JsonResponse({
            "error": "Method not allowed. Use POST."
        }, status=405)


@method_decorator(csrf_exempt, name="dispatch")
class KcbBillNotificationView(View):
    """
    KCB calls this after the account has been credited.
    This marks the payment as paid and posts it to the right section.
    """

    def post(self, request, *args, **kwargs):
        if not kcb_callback_is_authorized(request):
            return kcb_error_response("Unauthorized callback", status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            return kcb_error_response("Invalid JSON", status=400)

        if not isinstance(payload, dict):
            return kcb_error_response("Invalid JSON", status=400)

        transaction_reference = payload.get("transactionReference")
        request_id = payload.get("requestId")
        customer_reference = payload.get("customerReference")
        transaction_amount = payload.get("transactionAmount")

        if not transaction_reference or not customer_reference:
            return kcb_error_response(
                "Missing transactionReference or customerReference",
            )

        try:
            paid_amount = Decimal(str(transaction_amount))
        except (InvalidOperation, TypeError, ValueError):
            return kcb_error_response("Invalid transaction amount")
        if not paid_amount.is_finite() or paid_amount <= Decimal("0"):
            return kcb_error_response("Invalid transaction amount")

        try:
            with transaction.atomic():
                payment_intent = PaymentIntent.objects.select_for_update().get(
                    customer_reference=customer_reference
                )

                if KcbPaymentNotification.objects.filter(
                    transaction_reference=transaction_reference
                ).exists():
                    return kcb_success_response(
                        "Duplicate notification already received"
                    )

                if not amounts_match(payment_intent, paid_amount):
                    return kcb_error_response("Amount mismatch")

                notification = KcbPaymentNotification.objects.create(
                    payment_intent=payment_intent,
                    transaction_reference=transaction_reference,
                    request_id=request_id,
                    customer_reference=customer_reference,
                    customer_name=payload.get("customerName"),
                    customer_mobile_number=payload.get("customerMobileNumber"),
                    transaction_amount=paid_amount,
                    currency=payload.get("currency", "KES"),
                    channel_code=payload.get("channelCode"),
                    narration=payload.get("narration"),
                    credit_account_identifier=payload.get("creditAccountIdentifier"),
                    organization_short_code=payload.get("organizationShortCode"),
                    till_number=payload.get("tillNumber"),
                    raw_payload=redact_kcb_payload(payload),
                )

                if payment_intent.status != "paid":
                    payment_intent.mark_paid()
                    post_successful_payment(payment_intent, notification)

                notification.mark_processed()

        except PaymentIntent.DoesNotExist:
            return kcb_error_response("Payment reference not found")

        except IntegrityError:
            return kcb_success_response("Duplicate notification already processed")

        return kcb_success_response("Notification received")

    def get(self, request, *args, **kwargs):
        return JsonResponse({
            "error": "Method not allowed. Use POST."
        }, status=405)


class CreatePaymentIntentView(LoginRequiredMixin, FormView):
    template_name = "payments/create_payment.html"
    form_class = PaymentIntentForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["member"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        payment_date = timezone.localdate()
        contribution_month = get_month_start(payment_date)
        monthly_contribution_penalty = current_month_contribution_penalty(
            payment_date
        )
        context["payment_amount_config"] = {
            "contributionMonth": contribution_month.isoformat(),
            "monthlyContributionBalance": str(form.monthly_contribution_balance),
            "monthlyContributionPenalty": str(monthly_contribution_penalty),
            "paymentDate": payment_date.isoformat(),
            "loanBalances": form.loan_balance_by_id,
        }
        context["payment_date"] = payment_date
        context["contribution_month"] = contribution_month
        context["monthly_contribution_penalty"] = monthly_contribution_penalty
        return context

    def form_valid(self, form):
        payment_intent = form.save(commit=False)
        payment_intent.member = self.request.user
        payment_intent.customer_reference = generate_customer_reference(
            member=self.request.user,
            payment_type=payment_intent.payment_type,
            related_loan=payment_intent.related_loan,
            contribution_month=payment_intent.contribution_month,
        )
        payment_intent.status = "pending"
        payment_intent.save()

        messages.success(
            self.request,
            "Payment reference created. Use the instructions below to complete payment.",
        )
        return redirect("payment_instructions", pk=payment_intent.pk)


class PaymentInstructionsView(LoginRequiredMixin, DetailView):
    model = PaymentIntent
    template_name = "payments/payment_instructions.html"
    context_object_name = "payment"

    def get_queryset(self):
        return PaymentIntent.objects.select_related("member")

    def get_object(self, queryset=None):
        payment_intent = super().get_object(queryset)
        if not can_view_payment_intent(self.request.user, payment_intent):
            raise Http404
        return payment_intent

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["paybill_number"] = "522533"
        return context


class MyPaymentHistoryView(LoginRequiredMixin, ListView):
    model = PaymentIntent
    template_name = "payments/payment_history.html"
    context_object_name = "payments"
    paginate_by = 20

    def get_queryset(self):
        return PaymentIntent.objects.filter(
            member=self.request.user
        ).order_by("-created_at")
