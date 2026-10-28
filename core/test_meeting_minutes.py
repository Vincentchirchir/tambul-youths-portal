import copy
import hashlib
from io import BytesIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from asgiref.sync import async_to_sync
from botocore.exceptions import ClientError
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, default_storage, storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import AsyncRequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from pypdf import PdfWriter

from .forms import MeetingNoteForm
from .minutes_storage import MinutesStorageUnavailable, meeting_minutes_storage
from .minutes_views import attachment_response
from .models import MeetingNote, Signatory, CommitteeLetter
from .r2_storage import PrivateMinutesR2Storage
from accounts.models import User


def pdf_bytes(encrypted=False):
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    if encrypted:
        writer.encrypt("test-password")
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def upload(data=None, name="minutes.pdf", content_type="application/pdf"):
    return SimpleUploadedFile(name, pdf_bytes() if data is None else data, content_type=content_type)


@override_settings(
    MINUTES_R2_UPLOADS_ENABLED=True,
    NOTIFICATIONS_SEND_EMAILS=False,
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class MeetingMinutesTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tambul-minutes-test-")
        self.addCleanup(self.directory.cleanup)
        self.legacy_root = Path(self.directory.name) / "legacy"
        self.private_root = Path(self.directory.name) / "private"
        configured = copy.deepcopy(settings.STORAGES)
        configured["meeting_minutes"] = {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(self.private_root)},
        }
        self.storage_override = override_settings(MEDIA_ROOT=str(self.legacy_root), STORAGES=configured)
        self.storage_override.enable()
        self.addCleanup(self.storage_override.disable)
        self.member = get_user_model().objects.create_user(username="minutes_member", role="member")
        self.secretary = get_user_model().objects.create_user(username="minutes_secretary", role="secretary")
        self.committee = get_user_model().objects.create_user(username="minutes_committee", role="committee")
        self.payload = pdf_bytes()

    def note(self, audience="all_members", remote=False, attachment=True):
        note = MeetingNote.objects.create(title="Meeting minutes", content="Written summary", audience=audience)
        if attachment:
            field = note.minutes_pdf if remote else note.file
            field.save("meeting.pdf", ContentFile(self.payload), save=True)
        return note

    def post(self, document=None, **data):
        payload = {"title": "Uploaded meeting", "audience": "all_members", **data}
        if document is not None:
            payload["minutes_pdf"] = document
        return self.client.post(reverse("post-meeting-note"), payload)

    def body(self, response):
        # Django's test-client iterator closes its response while preserving the
        # enclosing TestCase transaction. Closing it twice would close that DB.
        return b"".join(response.streaming_content)

    def test_secretary_upload_saves_exact_private_bytes_and_metadata(self):
        self.client.force_login(self.secretary)
        response = self.post(upload(self.payload))
        self.assertEqual(response.status_code, 302)
        note = MeetingNote.objects.get()
        self.assertFalse(note.file)
        self.assertEqual(note.posted_by, self.secretary)
        self.assertEqual(note.minutes_original_name, "minutes.pdf")
        self.assertEqual(note.minutes_size, len(self.payload))
        self.assertEqual(note.minutes_sha256, hashlib.sha256(self.payload).hexdigest())
        self.assertEqual((self.private_root / note.minutes_pdf.name).read_bytes(), self.payload)
        self.assertFalse(self.legacy_root.exists())
        self.client.force_login(self.member)
        downloaded = self.client.get(reverse("meeting-minutes-download", args=[note.pk]))
        self.assertEqual(self.body(downloaded), self.payload)

    def test_anonymous_and_member_uploads_are_denied_without_saving_files(self):
        self.assertEqual(self.post(upload()).status_code, 302)
        self.client.force_login(self.member)
        self.assertEqual(self.post(upload()).status_code, 403)
        self.assertFalse(MeetingNote.objects.exists())
        self.assertFalse(self.private_root.exists())

    @override_settings(MINUTES_R2_UPLOADS_ENABLED=False)
    def test_disabled_uploads_fail_closed_but_text_minutes_still_work(self):
        self.client.force_login(self.secretary)
        self.assertEqual(self.post(upload()).status_code, 200)
        self.assertFalse(MeetingNote.objects.exists())
        self.assertEqual(self.post(content="Text minutes").status_code, 302)
        self.assertEqual(MeetingNote.objects.get().content, "Text minutes")

    def test_invalid_uploads_are_rejected_without_files_or_records(self):
        self.client.force_login(self.secretary)
        documents = [
            upload(b"<html>unsafe</html>", "minutes.html", "text/html"),
            upload(b"<html>renamed</html>"),
            upload(b"%PDF-1.7\nnot a complete PDF"),
            upload(b""),
            upload(pdf_bytes(encrypted=True)),
        ]
        for document in documents:
            with self.subTest(name=document.name, size=document.size):
                response = self.post(document)
                self.assertEqual(response.status_code, 200)
                self.assertIn("minutes_pdf", response.context["form"].errors)
        self.assertFalse(MeetingNote.objects.exists())
        self.assertFalse(self.private_root.exists())

    @override_settings(MINUTES_MAX_UPLOAD_BYTES=100)
    def test_size_limit_is_enforced(self):
        self.client.force_login(self.secretary)
        self.assertIn("minutes_pdf", self.post(upload()).context["form"].errors)
        self.assertFalse(MeetingNote.objects.exists())

    def test_case_insensitive_extension_and_generic_mime_are_supported(self):
        self.client.force_login(self.secretary)
        response = self.post(upload(name="MINUTES.PDF", content_type="application/octet-stream"))
        self.assertEqual(response.status_code, 302)

    def test_control_characters_in_display_filename_cannot_break_download_headers(self):
        self.client.force_login(self.secretary)
        # Multipart parsing already rejects raw CRLF in filename headers. Also
        # verify the shared form's handling of a supplied UploadedFile directly.
        form = MeetingNoteForm(
            {"title": "Safe filename", "audience": "all_members"},
            {"minutes_pdf": upload(name="minutes\r\n.pdf")},
        )
        self.assertTrue(form.is_valid(), form.errors)
        note = form.save()
        self.assertEqual(note.minutes_original_name, "minutes.pdf")
        response = self.client.get(reverse("meeting-minutes-download", args=[note.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.body(response), self.payload)

    def test_valid_pdf_form_validation_restores_file_position(self):
        document = upload(self.payload)
        form = MeetingNoteForm({"title": "Valid", "audience": "all_members"}, {"minutes_pdf": document})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(document.tell(), 0)

    def test_duplicate_original_names_never_overwrite_an_attachment(self):
        self.client.force_login(self.secretary)
        self.post(upload(self.payload))
        self.post(upload(self.payload))
        names = list(MeetingNote.objects.values_list("minutes_pdf", flat=True))
        self.assertEqual(len(set(names)), 2)
        for name in names:
            self.assertEqual((self.private_root / name).read_bytes(), self.payload)

    def test_storage_write_failure_rolls_back_record_and_notification(self):
        self.client.force_login(self.secretary)
        backend = storages["meeting_minutes"]
        with patch.object(backend, "save", side_effect=OSError("offline")), patch("core.signals.notify_users") as notify:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post(upload())
        self.assertEqual(response.status_code, 503)
        self.assertFalse(MeetingNote.objects.exists())
        notify.assert_not_called()

    def test_anonymous_download_requires_login_without_accessing_storage(self):
        note = self.note()
        with patch.object(FileSystemStorage, "open", side_effect=AssertionError("unauthorized storage read")):
            response = self.client.get(reverse("meeting-minutes-download", args=[note.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("private", response["Cache-Control"])

    def test_committee_audience_is_enforced_on_direct_and_legacy_urls(self):
        note = self.note(audience="committee_only")
        urls = [reverse("meeting-minutes-download", args=[note.pk]), "/media/" + note.file.name]
        self.client.force_login(self.member)
        for url in urls:
            with patch.object(FileSystemStorage, "open", side_effect=AssertionError("unauthorized storage read")):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.committee)
        for url in urls:
            self.assertEqual(self.body(self.client.get(url)), self.payload)

    @override_settings(DEBUG=False)
    def test_existing_legacy_pdf_downloads_in_production_without_changing_reference(self):
        note = self.note()
        old_name = note.file.name
        self.client.force_login(self.member)
        for url in (reverse("meeting-minutes-download", args=[note.pk]), "/media/" + old_name):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertIn("attachment", response["Content-Disposition"])
            self.assertIn("no-store", response["Cache-Control"])
            self.assertEqual(response["X-Frame-Options"], "DENY")
            self.assertEqual(self.body(response), self.payload)
        note.refresh_from_db()
        self.assertEqual(note.file.name, old_name)
        self.assertEqual((self.legacy_root / old_name).read_bytes(), self.payload)

    def test_r2_attachment_takes_precedence_and_legacy_original_is_preserved(self):
        note = self.note()
        legacy = note.file.name
        note.minutes_pdf.save("new.pdf", ContentFile(self.payload), save=True)
        self.client.force_login(self.member)
        response = self.client.get("/media/" + legacy)
        self.assertEqual(self.body(response), self.payload)
        self.assertTrue((self.legacy_root / legacy).exists())

    def test_legacy_non_pdf_attachment_remains_downloadable_without_revalidation(self):
        note = self.note(attachment=False)
        data = b"Existing written attachment"
        note.file.save("existing.txt", ContentFile(data), save=True)
        self.client.force_login(self.member)
        response = self.client.get(reverse("meeting-minutes-download", args=[note.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertEqual(self.body(response), data)

    def test_disabling_new_uploads_does_not_disable_existing_private_downloads(self):
        note = self.note(remote=True)
        self.client.force_login(self.member)
        with override_settings(MINUTES_R2_UPLOADS_ENABLED=False):
            self.assertEqual(self.body(self.client.get(reverse("meeting-minutes-download", args=[note.pk]))), self.payload)

    def test_missing_files_return_controlled_private_404_without_deleting_records(self):
        self.client.force_login(self.member)
        for remote in (False, True):
            note = self.note(remote=remote)
            attachment = note.minutes_pdf if remote else note.file
            # Only test-owned files beneath the temporary test directory.
            attachment.storage.delete(attachment.name) if not remote else storages["meeting_minutes"].delete(attachment.name)
            response = self.client.get(reverse("meeting-minutes-download", args=[note.pk]))
            self.assertEqual(response.status_code, 404)
            self.assertIn("no-store", response["Cache-Control"])
            self.assertTrue(MeetingNote.objects.filter(pk=note.pk).exists())

    def test_no_attachment_and_unknown_meeting_return_private_404(self):
        note = self.note(attachment=False)
        self.client.force_login(self.member)
        for pk in (note.pk, note.pk + 100):
            response = self.client.get(reverse("meeting-minutes-download", args=[pk]))
            self.assertEqual(response.status_code, 404)
            self.assertIn("no-store", response["Cache-Control"])

    def test_legacy_route_does_not_expose_unreferenced_files_or_other_media(self):
        self.client.force_login(self.member)
        path = self.legacy_root / "minutes/unreferenced.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(self.payload)
        for other in ("profile_photos/private.jpg", "protected/signatories/signatures/private.png"):
            asset = self.legacy_root / other
            asset.parent.mkdir(parents=True, exist_ok=True)
            asset.write_bytes(b"private image")
        # core.urls contains the production application routes, with no debug
        # static() helper cached from development URLconf initialization.
        with override_settings(DEBUG=False, ROOT_URLCONF="core.urls"):
            for url in ("/media/minutes/unreferenced.pdf", "/media/minutes/%00.pdf", "/media/minutes/../secret.pdf", "/media/minutes/bad%5Cname.pdf", "/media/profile_photos/private.jpg", "/media/protected/signatories/signatures/private.png"):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_storage_read_failures_and_access_denial_return_503(self):
        note = self.note(remote=True)
        self.client.force_login(self.member)
        for error in (OSError("offline"), ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")):
            with patch.object(storages["meeting_minutes"], "open", side_effect=error):
                response = self.client.get(reverse("meeting-minutes-download", args=[note.pk]))
            self.assertEqual(response.status_code, 503)
            self.assertIn("no-store", response["Cache-Control"])

    def test_missing_remote_object_is_distinguished_from_access_denial(self):
        note = self.note(remote=True)
        self.client.force_login(self.member)
        error = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        with patch.object(storages["meeting_minutes"], "open", side_effect=error):
            self.assertEqual(self.client.get(reverse("meeting-minutes-download", args=[note.pk])).status_code, 404)

    def test_detail_page_uses_authorized_link_and_always_shows_written_content(self):
        note = self.note()
        self.client.force_login(self.member)
        response = self.client.get(reverse("meeting-minutes-detail", args=[note.pk]))
        self.assertContains(response, reverse("meeting-minutes-download", args=[note.pk]))
        self.assertContains(response, "Written summary")
        self.assertNotContains(response, "/media/minutes/")
        self.assertNotContains(response, "<iframe")

    def test_both_dashboards_use_authorized_download_links(self):
        note = self.note()
        for user, route in ((self.member, "member-dashboard"), (self.committee, "committee-dashboard")):
            self.client.force_login(user)
            response = self.client.get(reverse(route))
            self.assertContains(response, reverse("meeting-minutes-download", args=[note.pk]))
            self.assertNotContains(response, "/media/minutes/")

    def test_notifications_use_stable_routes_after_commit_and_restrict_recipients(self):
        self.client.force_login(self.secretary)
        with patch("core.signals.notify_users") as notify:
            with self.captureOnCommitCallbacks(execute=True):
                self.post(upload(), audience="committee_only")
                notify.assert_not_called()
        note = MeetingNote.objects.get()
        notify.assert_called_once()
        kwargs = notify.call_args.kwargs
        self.assertEqual(kwargs["link"], reverse("meeting-minutes-detail", args=[note.pk]))
        self.assertNotIn(self.member, list(kwargs["recipients"]))
        self.assertIn(self.committee, list(kwargs["recipients"]))

    def test_notification_delivery_failure_does_not_fail_a_saved_upload(self):
        self.client.force_login(self.secretary)
        with patch("core.signals.notify_users", side_effect=OSError("notification outage")):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post(upload())
        self.assertEqual(response.status_code, 302)
        note = MeetingNote.objects.get()
        self.assertTrue(note.minutes_pdf.storage.exists(note.minutes_pdf.name))

    def test_admin_displays_only_authorized_file_link_and_preserves_legacy_file(self):
        note = self.note(remote=True)
        self.secretary.is_staff = self.secretary.is_superuser = True
        self.secretary.save()
        self.client.force_login(self.secretary)
        response = self.client.get(reverse("admin:core_meetingnote_change", args=[note.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("meeting-minutes-download", args=[note.pk]))
        self.assertNotContains(response, "cloudflarestorage.com")

    def test_admin_upload_failure_rolls_back_the_meeting(self):
        self.secretary.is_staff = self.secretary.is_superuser = True
        self.secretary.save()
        self.client.force_login(self.secretary)
        with patch.object(storages["meeting_minutes"], "save", side_effect=OSError("offline")):
            response = self.client.post(reverse("admin:core_meetingnote_add"), {
                "title": "Admin PDF", "audience": "all_members", "minutes_pdf": upload(), "_save": "Save",
            })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(MeetingNote.objects.exists())

    def test_unconfigured_r2_only_affects_the_minutes_feature(self):
        note = MeetingNote.objects.create(title="Private reference", minutes_pdf="minutes/existing.pdf")
        configured = copy.deepcopy(settings.STORAGES)
        configured["meeting_minutes"] = {"BACKEND": "core.r2_storage.PrivateMinutesR2Storage"}
        self.client.force_login(self.member)
        with override_settings(STORAGES=configured):
            self.assertEqual(self.client.get(reverse("meeting-minutes-download", args=[note.pk])).status_code, 503)
            self.assertEqual(self.client.get(reverse("member-dashboard")).status_code, 200)
            self.assertIsInstance(default_storage, FileSystemStorage)

    def test_minutes_storage_changes_do_not_change_other_file_backends(self):
        self.assertIsInstance(default_storage, FileSystemStorage)
        for model, name in ((User, "profile_photo"), (Signatory, "signature_image"), (Signatory, "stamp_image"), (CommitteeLetter, "pdf_file"), (CommitteeLetter, "supporting_attachment"), (MeetingNote, "file")):
            self.assertIsInstance(model._meta.get_field(name).storage, FileSystemStorage)
        self.assertEqual(settings.STORAGES["staticfiles"]["BACKEND"], "django.contrib.staticfiles.storage.StaticFilesStorage")

    def test_asgi_response_uses_async_chunks_and_returns_identical_bytes(self):
        note = self.note()
        request = AsyncRequestFactory().get(reverse("meeting-minutes-download", args=[note.pk]))
        request.user = self.member
        response = attachment_response(request, note)
        self.assertTrue(response.is_async)

        async def consume():
            return b"".join([chunk async for chunk in response.streaming_content])

        self.assertEqual(async_to_sync(consume)(), self.payload)
        # This response was constructed directly, outside the test client.
        with patch("django.core.signals.request_finished.send"):
            response.close()


class PrivateR2ConfigurationTests(SimpleTestCase):
    def backend(self):
        return PrivateMinutesR2Storage(
            bucket_name="test-minutes", endpoint_url="https://example.r2.cloudflarestorage.com",
            access_key="test", secret_key="test", region_name="auto",
        )

    def test_missing_credentials_fail_without_network_or_impacting_default_storage(self):
        with self.assertRaisesMessage(Exception, "not configured"):
            PrivateMinutesR2Storage()
        self.assertIsInstance(default_storage, FileSystemStorage)

    def test_private_backend_enforces_private_headers_bounded_timeouts_and_no_overwrites(self):
        backend = self.backend()
        self.assertIsNone(backend.default_acl)
        self.assertIsNone(backend.custom_domain)
        self.assertFalse(backend.file_overwrite)
        self.assertEqual(backend.object_parameters["CacheControl"], "private, no-store")
        self.assertEqual(backend.client_config.connect_timeout, 5)
        self.assertEqual(backend.client_config.read_timeout, 15)

    def test_storage_never_exposes_a_raw_url(self):
        with self.assertRaises(NotImplementedError):
            meeting_minutes_storage.url("minutes/example.pdf")

    @override_settings(MINUTES_R2_UPLOADS_ENABLED=False)
    def test_disabled_storage_writes_do_not_probe_the_backend(self):
        with patch.object(meeting_minutes_storage, "_call", side_effect=AssertionError("backend accessed")):
            with self.assertRaises(MinutesStorageUnavailable):
                meeting_minutes_storage.save("minutes/no.pdf", ContentFile(pdf_bytes()))

    def test_sdk_upload_sends_private_headers_without_acl(self):
        backend = self.backend()
        data = pdf_bytes()
        key = "minutes/checked.pdf"
        client = backend.connection.meta.client
        with Stubber(client) as stub:
            stub.add_client_error("head_object", service_error_code="404", http_status_code=404,
                                  expected_params={"Bucket": "test-minutes", "Key": key})
            stub.add_response("put_object", {}, expected_params={
                "Bucket": "test-minutes", "Key": key, "Body": ANY,
                "ContentType": "application/pdf", "CacheControl": "private, no-store",
                "ChecksumAlgorithm": ANY,
            })
            self.assertEqual(backend.save(key, ContentFile(data)), key)
            stub.assert_no_pending_responses()

    def test_sdk_download_returns_exact_pdf_bytes_without_network(self):
        backend = self.backend()
        data = pdf_bytes()
        key = "minutes/checked.pdf"
        client = backend.connection.meta.client
        with Stubber(client) as stub:
            stub.add_response("head_object", {"ContentLength": len(data)}, {"Bucket": "test-minutes", "Key": key})
            stub.add_response("head_object", {"ContentLength": len(data)}, {"Bucket": "test-minutes", "Key": key})
            stub.add_response("get_object", {
                "Body": StreamingBody(BytesIO(data), len(data)), "ContentLength": len(data),
            }, {"Bucket": "test-minutes", "Key": key})
            with backend.open(key, "rb") as document:
                self.assertEqual(document.read(), data)
            stub.assert_no_pending_responses()
