"""Permission-checked downloads for R2 and legacy minutes, with no public media root."""

import logging
from contextlib import suppress
from pathlib import PurePosixPath

from asgiref.sync import sync_to_async
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.cache import never_cache

from .minutes_storage import is_missing_file
from .models import MeetingNote
from .permissions import can_view_meeting_note


logger = logging.getLogger(__name__)


def minutes_attachment(note):
    for attachment in (note.minutes_pdf, note.file):
        if attachment and attachment.name != "None":
            return attachment
    return None


def attachment_response(request, note):
    if not can_view_meeting_note(request.user, note):
        return HttpResponseForbidden("You do not have permission to view this meeting note.")
    attachment = minutes_attachment(note)
    if attachment is None:
        raise Http404("No attachment was provided for these minutes.")
    document = None
    try:
        document = attachment.open("rb")
        # Force lazy remote reads before sending headers, so outages return 503.
        document.read(1)
        document.seek(0)
        filename = (
            note.minutes_original_name if attachment is note.minutes_pdf else ""
        ) or PurePosixPath(attachment.name).name
        response = FileResponse(
            document,
            as_attachment=True,
            filename=filename,
            content_type="application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream",
        )
        if hasattr(request, "scope"):
            # FileResponse's synchronous iterator is otherwise consumed in full
            # by Django's ASGI adapter. Keep delivery in bounded chunks instead.
            async def chunks():
                try:
                    while True:
                        chunk = await sync_to_async(document.read)(64 * 1024)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    await sync_to_async(document.close)()
            response.streaming_content = chunks()
        return response
    except Exception as error:
        if document is not None:
            with suppress(Exception):
                document.close()
        if is_missing_file(error):
            logger.warning("Minutes attachment missing for meeting %s", note.pk)
            raise Http404("This attachment is unavailable. Please contact the Secretary.") from error
        logger.warning("Minutes download failed for meeting %s (%s)", note.pk, type(error).__name__)
        return HttpResponse("Document storage is temporarily unavailable. Please try again later.", status=503)


@method_decorator(never_cache, name="dispatch")
class MeetingMinutesDownloadView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        except Http404:
            return HttpResponse("This attachment is unavailable. Please contact the Secretary.", status=404)

    def get(self, request, pk):
        return attachment_response(request, get_object_or_404(MeetingNote, pk=pk))


class LegacyMeetingMinutesDownloadView(MeetingMinutesDownloadView):
    def get(self, request, filename):
        path = PurePosixPath(filename)
        if (path.is_absolute() or ".." in path.parts or "\\" in filename
                or any(ord(character) < 32 or ord(character) == 127 for character in filename)):
            raise Http404
        notes = MeetingNote.objects.filter(file="minutes/" + filename).order_by("pk")
        found = False
        for note in notes:
            found = True
            if can_view_meeting_note(request.user, note):
                return attachment_response(request, note)
        if found:
            return HttpResponseForbidden("You do not have permission to view this meeting note.")
        raise Http404
