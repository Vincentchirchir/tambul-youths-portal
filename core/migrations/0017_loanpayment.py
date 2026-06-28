import datetime

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def backfill_existing_payments(apps, schema_editor):
    Loan = apps.get_model("core", "Loan")
    LoanPayment = apps.get_model("core", "LoanPayment")

    for loan in Loan.objects.filter(total_paid_so_far__gt=0):
        payment_date = loan.repayment_updated_at or loan.loan_date or datetime.date.today()
        LoanPayment.objects.create(
            loan=loan,
            amount=loan.total_paid_so_far,
            payment_date=payment_date,
            note="Initial payment history backfill",
        )


def remove_backfilled_payments(apps, schema_editor):
    LoanPayment = apps.get_model("core", "LoanPayment")
    LoanPayment.objects.filter(note="Initial payment history backfill").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_announcement_created_by"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="LoanPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=10)),
                ("payment_date", models.DateField(default=django.utils.timezone.localdate)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "loan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payments",
                        to="core.loan",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="loan_payments_recorded",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-payment_date", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="loanpayment",
            index=models.Index(fields=["payment_date"], name="core_loanpa_payment_4b4d00_idx"),
        ),
        migrations.AddIndex(
            model_name="loanpayment",
            index=models.Index(fields=["loan", "payment_date"], name="core_loanpa_loan_id_18dfa5_idx"),
        ),
        migrations.RunPython(backfill_existing_payments, remove_backfilled_payments),
    ]
