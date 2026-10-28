"""Imported only when minutes storage is used, never by unrelated uploads."""

from botocore.config import Config
from django.core.exceptions import ImproperlyConfigured
from storages.backends.s3 import S3Storage


class PrivateMinutesR2Storage(S3Storage):
    def __init__(self, **options):
        required = ("bucket_name", "endpoint_url", "access_key", "secret_key")
        if any(not options.get(key) for key in required):
            raise ImproperlyConfigured("The private minutes R2 backend is not configured.")
        options.update(
            default_acl=None,
            custom_domain=None,
            querystring_auth=True,
            file_overwrite=False,
            object_parameters={"CacheControl": "private, no-store"},
            client_config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 1},
            ),
            max_memory_size=1024 * 1024,
        )
        super().__init__(**options)
