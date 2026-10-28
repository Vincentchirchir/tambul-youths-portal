"""Shared portal/admin validation for newly supplied PDFs."""

from django.conf import settings
from django.core.exceptions import ValidationError


def validate_meeting_pdf(upload):
    # Unchanged existing FieldFiles are references, not new uploads to revalidate.
    if getattr(upload, "_committed", False):
        return
    if not upload.name.lower().endswith(".pdf"):
        raise ValidationError("Upload a PDF document with a .pdf extension.")
    if not upload.size or upload.size > settings.MINUTES_MAX_UPLOAD_BYTES:
        limit = settings.MINUTES_MAX_UPLOAD_BYTES // (1024 * 1024)
        raise ValidationError(f"Upload a nonempty PDF no larger than {limit} MiB.")
    position = upload.tell()
    try:
        upload.seek(0)
        if upload.read(5) != b"%PDF-":
            raise ValidationError("The attachment does not contain a valid PDF header.")
        upload.seek(0)
        from pypdf import PdfReader

        reader = PdfReader(upload, strict=True)
        if reader.is_encrypted:
            raise ValidationError("Password-protected PDFs are not supported. Upload an unencrypted copy.")
        if not reader.pages:
            raise ValidationError("The PDF must contain at least one readable page.")
    except ValidationError:
        raise
    except Exception as error:
        raise ValidationError("The PDF could not be read. Export it again and retry.") from error
    finally:
        upload.seek(position)
