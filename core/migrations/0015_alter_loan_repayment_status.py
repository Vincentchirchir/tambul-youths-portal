from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_loanreminderlog"),
    ]

    operations = [
        migrations.AlterField(
            model_name="loan",
            name="repayment_status",
            field=models.CharField(
                choices=[
                    ("not_paid", "Not Paid"),
                    ("partially_paid", "Partially Paid"),
                    ("fully_paid", "Fully Paid"),
                    ("late", "Late"),
                ],
                default="not_paid",
                max_length=20,
            ),
        ),
    ]
