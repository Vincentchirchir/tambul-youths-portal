from django.urls import path
from .views import (
    CreatePaymentIntentView,
    KcbBillNotificationView,
    KcbBillValidationView,
    MyPaymentHistoryView,
    PaymentInstructionsView,
)

urlpatterns = [
    path(
        "kcb/bill-validation/",
        KcbBillValidationView.as_view(),
        name="kcb_bill_validation",
    ),
    path(
        "kcb/bill-notification/",
        KcbBillNotificationView.as_view(),
        name="kcb_bill_notification",
    ),
    path(
        "pay/",
        CreatePaymentIntentView.as_view(),
        name="create_payment",
    ),
    path(
        "payments/<int:pk>/instructions/",
        PaymentInstructionsView.as_view(),
        name="payment_instructions",
    ),
    path(
        "payments/history/",
        MyPaymentHistoryView.as_view(),
        name="payment_history",
    ),
]
