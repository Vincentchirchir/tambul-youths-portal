"""Read-only, offline checks for the minutes production configuration."""

import re

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage, storages
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Validate minutes release settings without database access or R2 requests; never prints credentials."

    def add_arguments(self, parser):
        parser.add_argument("--expected-uploads", choices=("disabled", "enabled"), default="disabled")

    def handle(self, *args, **options):
        errors = []
        if settings.DEBUG or settings.ENVIRONMENT != "production":
            errors.append("Use production settings with DEBUG=False.")
        if settings.MINUTES_R2_UPLOADS_ENABLED != (options["expected_uploads"] == "enabled"):
            errors.append("Minutes upload switch differs from the expected release stage.")
        if not isinstance(settings.MINUTES_MAX_UPLOAD_BYTES, int) or settings.MINUTES_MAX_UPLOAD_BYTES <= 0:
            errors.append("Minutes maximum size must be a positive integer.")
        storage = settings.STORAGES.get("meeting_minutes", {})
        configured = storage.get("OPTIONS", {})
        if storage.get("BACKEND") != "core.r2_storage.PrivateMinutesR2Storage":
            errors.append("Minutes must use the dedicated private backend.")
        if configured.get("bucket_name") != "tambul-portal-media":
            errors.append("Minutes bucket differs from the reviewed target.")
        if not re.fullmatch(r"https://[a-fA-F0-9]{32}\.r2\.cloudflarestorage\.com", configured.get("endpoint_url", "")):
            errors.append("Minutes endpoint must be the reviewed HTTPS account S3 endpoint.")
        if configured.get("region_name") != "auto" or not all(configured.get(name) for name in ("access_key", "secret_key")):
            errors.append("Minutes credentials/region are incomplete.")
        if settings.STORAGES["default"]["BACKEND"] != "django.core.files.storage.FileSystemStorage" or not isinstance(default_storage, FileSystemStorage):
            errors.append("The application's default storage was changed.")
        if settings.STORAGES["staticfiles"]["BACKEND"] != "django.contrib.staticfiles.storage.StaticFilesStorage":
            errors.append("The existing effective static backend was changed.")
        if errors:
            raise CommandError(" ".join(errors))
        try:
            backend = storages["meeting_minutes"]
            if (backend.default_acl is not None or backend.custom_domain is not None
                    or not backend.querystring_auth or backend.file_overwrite
                    or backend.object_parameters.get("CacheControl") != "private, no-store"):
                raise CommandError("Private storage safeguards differ from the reviewed configuration.")
        except CommandError:
            raise
        except Exception:
            raise CommandError("Minutes backend initialization failed; credential values are suppressed.") from None
        self.stdout.write("PASS: production minutes settings and upload stage verified; default/static storage unchanged. No database or network operation performed.")
