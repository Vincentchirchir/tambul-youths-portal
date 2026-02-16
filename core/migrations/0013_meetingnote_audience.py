from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_contribution_updated_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="meetingnote",
            name="audience",
            field=models.CharField(
                choices=[
                    ("all_members", "All members"),
                    ("committee_only", "Committee only"),
                ],
                default="all_members",
                max_length=20,
            ),
        ),
    ]
