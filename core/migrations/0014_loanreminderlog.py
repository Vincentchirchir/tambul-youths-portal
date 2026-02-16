from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_meetingnote_audience"),
    ]

    operations = [
        migrations.CreateModel(
            name="LoanReminderLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "reminder_type",
                    models.CharField(
                        choices=[("due_soon", "Due soon"), ("overdue", "Overdue")],
                        max_length=20,
                    ),
                ),
                ("reminder_date", models.DateField()),
                ("days_offset", models.IntegerField(help_text="Days until/since due date when reminder was sent.")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "loan",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reminder_logs",
                        to="core.loan",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "unique_together": {("loan", "reminder_type", "reminder_date")},
            },
        ),
    ]
