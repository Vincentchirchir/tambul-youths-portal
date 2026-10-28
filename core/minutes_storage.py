"""Explicit private storage for minutes; unrelated FileFields keep their backend."""

import logging
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import Storage, storages
from django.utils.deconstruct import deconstructible


logger = logging.getLogger(__name__)


class MinutesStorageUnavailable(OSError):
    """The configured minutes backend cannot complete an operation."""


def is_missing_file(error):
    code = str((getattr(error, "response", {}) or {}).get("Error", {}).get("Code", ""))
    return isinstance(error, FileNotFoundError) or code in {"404", "NoSuchKey", "NotFound"}


def minutes_upload_path(instance, filename):
    return f"minutes/{uuid4().hex}.pdf"


@deconstructible
class MeetingMinutesStorage(Storage):
    """Resolve the named backend lazily, including in historical migrations.

    Reads remain enabled when new R2 uploads are disabled during a rollback.
    No public URL is exposed: application routes enforce the meeting audience.
    """

    def _call(self, operation, *args, **kwargs):
        try:
            return getattr(storages["meeting_minutes"], operation)(*args, **kwargs)
        except Exception as error:
            if is_missing_file(error):
                raise FileNotFoundError("Meeting attachment is unavailable.") from error
            logger.warning("Minutes storage %s failed (%s)", operation, type(error).__name__)
            raise MinutesStorageUnavailable("Meeting attachment storage is temporarily unavailable.") from error

    def _open(self, name, mode="rb"):
        return self._call("open", name, mode)

    def save(self, name, content, max_length=None):
        if not settings.MINUTES_R2_UPLOADS_ENABLED:
            # Storage.save() normally probes exists() before calling _save().
            # A disabled write must fail before any backend/network operation.
            raise MinutesStorageUnavailable("New minutes PDF uploads are not enabled.")
        return super().save(name, content, max_length=max_length)

    def _save(self, name, content):
        if not settings.MINUTES_R2_UPLOADS_ENABLED:
            raise MinutesStorageUnavailable("New minutes PDF uploads are not enabled.")
        return self._call("save", name, content)

    def exists(self, name):
        return self._call("exists", name)

    def size(self, name):
        return self._call("size", name)

    def url(self, name):
        raise NotImplementedError("Use the authorized meeting-minutes download route.")


meeting_minutes_storage = MeetingMinutesStorage()
