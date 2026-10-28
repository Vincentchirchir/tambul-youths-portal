from django.db import migrations, models

import core.minutes_storage
import core.minutes_validation


class Migration(migrations.Migration):
    dependencies = [("core", "0023_aiauditlog")]

    operations = [
        migrations.AddField(
            model_name="meetingnote",
            name="minutes_pdf",
            field=models.FileField(
                blank=True,
                help_text="New PDF attachment stored privately. Existing legacy attachments are retained.",
                max_length=255,
                storage=core.minutes_storage.MeetingMinutesStorage(),
                upload_to=core.minutes_storage.minutes_upload_path,
                validators=[core.minutes_validation.validate_meeting_pdf],
            ),
        ),
        migrations.AddField(
            model_name="meetingnote",
            name="minutes_original_name",
            field=models.CharField(blank=True, editable=False, max_length=255),
        ),
        migrations.AddField(
            model_name="meetingnote",
            name="minutes_sha256",
            field=models.CharField(blank=True, editable=False, max_length=64),
        ),
        migrations.AddField(
            model_name="meetingnote",
            name="minutes_size",
            field=models.PositiveBigIntegerField(default=0, editable=False),
        ),
    ]
