"""Migration/release regression tests use local PDFs and fake R2 only."""

import copy
import hashlib
from io import BytesIO, StringIO
import json
from pathlib import Path
import tarfile
import tempfile
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from django.core.files.storage import FileSystemStorage
from django.test import SimpleTestCase, TestCase, override_settings

from .minutes_migration import (
    BUCKET, MigrationError, VerifiedBackup, copy_to_r2, digest_json, make_manifest,
    object_metadata, validate_manifest, write_json_exclusive, make_reconciled_manifest,
    manifest_documents, make_receipt, validate_receipt, plan_database_links,
)
from .models import MeetingNote, Notification
from .test_meeting_minutes import pdf_bytes


class BackupFixture:
    def create_backup(self):
        self.directory = tempfile.TemporaryDirectory(prefix="tambul-migration-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        names = ["minutes/first.pdf", "minutes/SECOND..pdf"]
        data = pdf_bytes()
        self.source = {name: data for name in names}
        inventory = {"files": {name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name in names}}
        for name in ("inventory-before.json", "inventory-after.json"):
            (self.root / name).write_text(json.dumps(inventory))
        with tarfile.open(self.root / "media.tar", "w") as archive:
            for name in names:
                info = tarfile.TarInfo("media/" + name)
                info.size = len(data)
                archive.addfile(info, BytesIO(data))
        (self.root / "verified.json").write_text(json.dumps({
            "status": "VERIFIED", "files": 2, "archive_sha256": "a" * 64,
        }))
        return VerifiedBackup(self.root)


class FakeR2:
    def __init__(self):
        self.objects = {"minutes/existing.pdf": {"Body": b"preserve"}}
        self.puts = []

    def put_object(self, **arguments):
        self.puts.append(arguments)
        if arguments["Key"] in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}, "ResponseMetadata": {"HTTPStatusCode": 412}}, "PutObject")
        self.objects[arguments["Key"]] = arguments

    def head_object(self, **arguments):
        item = self.objects[arguments["Key"]]
        return {"ContentLength": len(item["Body"]), "Metadata": item["Metadata"], "CacheControl": item["CacheControl"], "ContentType": item["ContentType"]}

    def get_object(self, **arguments):
        return {"Body": BytesIO(self.objects[arguments["Key"]]["Body"])}


class BackupMigrationTests(BackupFixture, SimpleTestCase):
    def setUp(self):
        self.backup = self.create_backup()
        self.records = [{
            "id": number, "file": name, "audience": audience,
            "created_at": "2026-10-25T12:00:00+00:00", "posted_by_id": None,
        } for number, name, audience in ((1, "minutes/first.pdf", "all_members"), (2, "minutes/SECOND..pdf", "committee_only"))]

    def test_verified_archive_inventory_preserves_names_and_checksums_without_extracting(self):
        inventory = self.backup.inventory()
        self.assertEqual(sum(item["size"] for item in inventory["pdfs"]), sum(map(len, self.source.values())))
        self.assertEqual({item["legacy_name"] for item in inventory["pdfs"]}, set(self.source))
        self.assertFalse((self.root / "media").exists())

    def test_copy_is_idempotent_preserves_originals_audiences_and_never_overwrites(self):
        manifest = make_manifest(self.backup, self.records)
        client = FakeR2()
        original = (self.root / "media.tar").read_bytes()
        receipt = copy_to_r2(client, self.backup, manifest)
        self.assertEqual(copy_to_r2(client, self.backup, manifest), receipt)
        self.assertEqual(client.objects["minutes/existing.pdf"]["Body"], b"preserve")
        self.assertEqual((self.root / "media.tar").read_bytes(), original)
        for entry in manifest["entries"]:
            self.assertTrue(entry["r2_key"].endswith("/" + entry["original_name"]))
            self.assertEqual(entry["audience"], self.records[entry["meeting_id"] - 1]["audience"])
        self.assertTrue(all(item["IfNoneMatch"] == "*" and "ACL" not in item for item in client.puts))
        for filename in ("manifest.json", "receipt.json"):
            self.assertIn(f"_migrations/{manifest['migration_id']}/{filename}", client.objects)

    def test_unverified_inventory_changed_sources_and_path_traversal_are_rejected(self):
        (self.root / "verified.json").write_text('{"status":"UNVERIFIED"}')
        with self.assertRaises(MigrationError):
            VerifiedBackup(self.root)
        for bad in ("minutes/../outside.pdf", "/minutes/outside.pdf", "minutes/evil\\outside.pdf"):
            records = copy.deepcopy(self.records)
            records[0]["file"] = bad
            with self.subTest(path=bad), self.assertRaises(MigrationError):
                make_manifest(self.backup, records)

    def test_export_requires_real_unique_ids_valid_audiences_and_identity_guards(self):
        for name, bad in (("id", None), ("audience", "unknown"), ("created_at", "2026-10-25")):
            records = copy.deepcopy(self.records)
            records[0][name] = bad
            with self.subTest(name=name), self.assertRaises(MigrationError):
                make_manifest(self.backup, records)
        records = copy.deepcopy(self.records)
        records[1]["id"] = records[0]["id"]
        with self.assertRaises(MigrationError):
            make_manifest(self.backup, records)

    def test_all_sources_are_checked_before_first_put(self):
        manifest = make_manifest(self.backup, self.records)
        self.backup.files["minutes/SECOND..pdf"]["sha256"] = "0" * 64
        client = FakeR2()
        with self.assertRaises(MigrationError):
            copy_to_r2(client, self.backup, manifest)
        self.assertFalse(client.puts)

    def test_corrupt_remote_collision_is_never_replaced_or_deleted(self):
        manifest = make_manifest(self.backup, self.records)
        client = FakeR2()
        item = manifest["entries"][0]
        client.objects[item["r2_key"]] = {
            "Body": b"different", "Metadata": object_metadata(manifest, item),
            "ContentType": "application/pdf", "CacheControl": "private, no-store",
        }
        with self.assertRaises(MigrationError):
            copy_to_r2(client, self.backup, manifest)
        self.assertEqual(client.objects[item["r2_key"]]["Body"], b"different")

    def test_storage_failure_retains_completed_objects_and_source_documents(self):
        manifest = make_manifest(self.backup, self.records)
        client = FakeR2()
        actual = client.put_object

        def second_fails(**arguments):
            if len(client.puts) == 1:
                raise OSError("no network")
            actual(**arguments)

        with patch.object(client, "put_object", side_effect=second_fails), self.assertRaises(OSError):
            copy_to_r2(client, self.backup, manifest)
        self.assertIn(manifest["entries"][0]["r2_key"], client.objects)
        self.assertTrue((self.root / "media.tar").exists())

    def test_manifest_and_receipt_files_are_private_and_cannot_be_overwritten(self):
        path = self.root / "manifest.json"
        write_json_exclusive(path, {"reviewed": True})
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            write_json_exclusive(path, {"reviewed": False})


@override_settings(MINUTES_R2_UPLOADS_ENABLED=False, NOTIFICATIONS_SEND_EMAILS=False)
class LinkMinutesTests(BackupFixture, TestCase):
    def setUp(self):
        self.backup = self.create_backup()
        self.meetings = [MeetingNote.objects.create(title="First", file="minutes/first.pdf"), MeetingNote.objects.create(title="Second", file="minutes/SECOND..pdf", audience="committee_only")]
        self.records = [{"id": note.pk, "file": note.file.name, "audience": note.audience, "created_at": note.created_at.isoformat(), "posted_by_id": note.posted_by_id} for note in self.meetings]
        self.manifest = make_manifest(self.backup, self.records)
        self.manifest_path = self.root / "manifest.json"
        self.receipt_path = self.root / "receipt.json"
        write_json_exclusive(self.manifest_path, self.manifest)
        write_json_exclusive(self.receipt_path, {"schema": 1, "bucket": BUCKET, "manifest_sha256": digest_json(self.manifest), "verified_count": 2})
        # Patch the named storage's open method, retaining the real configured
        # backend identity/bucket checks instead of replacing application storage.
        self.storage = patch("core.management.commands.link_minutes_r2.storages", {"meeting_minutes": self})
        self.storage.start()
        self.addCleanup(self.storage.stop)
        config = copy.deepcopy(settings.STORAGES)
        config["meeting_minutes"] = {"BACKEND": "core.r2_storage.PrivateMinutesR2Storage", "OPTIONS": {"bucket_name": BUCKET}}
        override = override_settings(STORAGES=config)
        override.enable()
        self.addCleanup(override.disable)

    def open(self, key, mode):
        if key == f"_migrations/{self.manifest['migration_id']}/manifest.json":
            return BytesIO(json.dumps(self.manifest).encode())
        if key == f"_migrations/{self.manifest['migration_id']}/receipt.json":
            return BytesIO(self.receipt_path.read_bytes())
        item = next(item for item in self.manifest["entries"] if item["r2_key"] == key)
        return BytesIO(self.source[item["legacy_name"]])

    def run_command(self, apply=False):
        output = StringIO()
        call_command("link_minutes_r2", manifest=str(self.manifest_path), receipt=str(self.receipt_path), apply=apply, stdout=output)
        return output.getvalue()

    def test_default_dry_run_never_changes_meetings(self):
        self.assertIn("DRY RUN", self.run_command())
        self.assertFalse(MeetingNote.objects.exclude(minutes_pdf="").exists())

    def test_apply_is_atomic_idempotent_preserves_ids_audiences_legacy_and_notifications(self):
        notifications = Notification.objects.count()
        with patch("core.signals.notify_users") as notify:
            self.run_command(apply=True)
            self.assertIn("0 links updated", self.run_command(apply=True))
        notify.assert_not_called()
        self.assertEqual(Notification.objects.count(), notifications)
        for item in self.manifest["entries"]:
            note = MeetingNote.objects.get(pk=item["meeting_id"])
            self.assertEqual(note.file.name, item["legacy_name"])
            self.assertEqual(note.audience, item["audience"])
            self.assertEqual(note.minutes_original_name, item["original_name"])
            self.assertEqual(note.minutes_sha256, item["sha256"])
            self.assertEqual(note.minutes_pdf.name, item["r2_key"])

    def test_changed_audience_source_or_identity_prevents_all_links(self):
        note = self.meetings[1]
        MeetingNote.objects.filter(pk=note.pk).update(audience="all_members")
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertFalse(MeetingNote.objects.exclude(minutes_pdf="").exists())

    def test_predeploy_links_using_private_remote_artifacts_without_local_media(self):
        with patch.object(FileSystemStorage, "open", side_effect=AssertionError("legacy filesystem must not be used")):
            call_command("link_minutes_r2", migration_id=self.manifest["migration_id"], apply=True, stdout=StringIO())
        self.assertEqual(MeetingNote.objects.exclude(minutes_pdf="").count(), 2)

    def test_additional_unpreserved_legacy_attachment_stops_linking(self):
        MeetingNote.objects.create(title="Uploaded after backup", file="minutes/new-unpreserved.pdf")
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.assertFalse(MeetingNote.objects.exclude(minutes_pdf="").exists())

    def test_migrated_downloads_keep_audiences_when_render_legacy_files_are_absent(self):
        self.run_command(apply=True)
        with patch("core.minutes_storage.storages", {"meeting_minutes": self}), patch.object(
            FileSystemStorage, "open", side_effect=AssertionError("old Render files are absent"),
        ):
            member = get_user_model().objects.create_user(username="migrated-member", role="member")
            self.client.force_login(member)
            first = self.client.get(f"/minutes/{self.meetings[0].pk}/download/")
            self.assertEqual(b"".join(first.streaming_content), self.source["minutes/first.pdf"])
            self.assertEqual(self.client.get(f"/minutes/{self.meetings[1].pk}/download/").status_code, 403)
            committee = get_user_model().objects.create_user(username="migrated-committee", role="committee")
            self.client.force_login(committee)
            second = self.client.get(f"/minutes/{self.meetings[1].pk}/download/")
            self.assertEqual(b"".join(second.streaming_content), self.source["minutes/SECOND..pdf"])

    def test_corrupt_remote_wrong_receipt_and_existing_private_attachment_are_rejected(self):
        with patch.object(self, "open", return_value=BytesIO(b"corrupt")), self.assertRaises(CommandError):
            self.run_command(apply=True)
        MeetingNote.objects.filter(pk=self.meetings[1].pk).update(minutes_pdf="minutes/already-private.pdf")
        with self.assertRaises(CommandError):
            self.run_command(apply=True)
        self.meetings[0].refresh_from_db()
        self.assertFalse(self.meetings[0].minutes_pdf)
        self.receipt_path.write_text('{"schema":1}')
        with self.assertRaises(CommandError):
            self.run_command(apply=True)


class ReleaseConfigurationTests(SimpleTestCase):
    def test_production_config_check_is_offline_and_credential_safe(self):
        configured = copy.deepcopy(settings.STORAGES)
        configured["meeting_minutes"] = {"BACKEND": "core.r2_storage.PrivateMinutesR2Storage", "OPTIONS": {
            "bucket_name": BUCKET, "endpoint_url": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
            "access_key": "fake-access", "secret_key": "fake-secret", "region_name": "auto",
        }}
        output = StringIO()
        with override_settings(DEBUG=False, ENVIRONMENT="production", MINUTES_R2_UPLOADS_ENABLED=False, STORAGES=configured):
            call_command("check_minutes_release", stdout=output)
            with override_settings(MINUTES_R2_UPLOADS_ENABLED=True), self.assertRaises(CommandError):
                call_command("check_minutes_release")
        self.assertIn("PASS", output.getvalue())
        self.assertNotIn("fake-secret", output.getvalue())


class ReconciledManifestTests(BackupFixture, SimpleTestCase):
    def setUp(self):
        self.backup = self.create_backup()
        self.records = [
            {"id": 24, "file": "minutes/missing_annual_report.pdf", "audience": "all_members"},
            {"id": 31, "file": "minutes/first.pdf", "audience": "all_members"},
        ]
        self.manifest = make_reconciled_manifest(
            self.backup, self.records, link_ids=[31], unlinked_names=["minutes/SECOND..pdf"], unresolved_ids=[24],
        )

    def test_one_link_one_unlinked_one_unresolved_preserves_exact_dispositions(self):
        self.assertEqual([item["meeting_id"] for item in validate_manifest(self.manifest)], [31])
        self.assertEqual(self.manifest["unresolved_records"][0]["meeting_id"], 24)
        orphan = self.manifest["unlinked_documents"][0]
        self.assertIn("minutes/unlinked/", orphan["r2_key"])
        self.assertNotIn("meeting_id", orphan)
        self.assertNotIn("audience", orphan)
        self.assertNotIn("meeting-id", object_metadata(self.manifest, orphan))
        self.assertEqual(object_metadata(self.manifest, orphan)["disposition"], "unlinked")

    def test_both_documents_are_copied_and_receipt_counts_are_explicit(self):
        client = FakeR2()
        receipt = copy_to_r2(client, self.backup, self.manifest)
        self.assertEqual(copy_to_r2(client, self.backup, self.manifest), receipt)
        for item in manifest_documents(self.manifest):
            self.assertEqual(client.objects[item["r2_key"]]["Body"], self.source[item["legacy_name"]])
        self.assertEqual((receipt["verified_count"], receipt["linked_count"], receipt["unlinked_count"], receipt["unresolved_count"]), (2, 1, 1, 1))
        validate_receipt(self.manifest, receipt)
        receipt["linked_count"] = 2
        with self.assertRaises(MigrationError):
            validate_receipt(self.manifest, receipt)

    def test_unmatched_document_cannot_be_associated_with_unresolved_record(self):
        altered = copy.deepcopy(self.manifest)
        altered["unlinked_documents"][0]["meeting_id"] = 24
        with self.assertRaises(MigrationError):
            validate_manifest(altered)
        altered = copy.deepcopy(self.manifest)
        altered["unresolved_records"][0]["legacy_name"] = "minutes/SECOND..pdf"
        with self.assertRaises(MigrationError):
            validate_manifest(altered)

    def test_legacy_schema_plan_proposes_only_31_and_preserves_input_rows(self):
        before = copy.deepcopy(self.records)
        plan = plan_database_links(self.manifest, self.records, private_schema_present=False)
        self.assertEqual([item["meeting_id"] for item in plan["pending"]], [31])
        self.assertEqual(plan["unresolved_ids"], [24])
        self.assertEqual(self.records, before)

    def test_explicit_unresolved_exception_does_not_bypass_other_legacy_files(self):
        records = self.records + [{"id": 99, "file": "minutes/new.pdf", "audience": "all_members"}]
        with self.assertRaises(MigrationError):
            plan_database_links(self.manifest, records, private_schema_present=False)

    def test_changed_unresolved_reference_or_audience_stops_release(self):
        for field, value in (("file", "minutes/different_missing.pdf"), ("audience", "committee_only")):
            records = copy.deepcopy(self.records)
            records[0][field] = value
            with self.subTest(field=field), self.assertRaises(MigrationError):
                plan_database_links(self.manifest, records, private_schema_present=False)


@override_settings(MINUTES_R2_UPLOADS_ENABLED=False, NOTIFICATIONS_SEND_EMAILS=False)
class ReconciledLinkTests(BackupFixture, TestCase):
    def setUp(self):
        self.backup = self.create_backup()
        MeetingNote.objects.create(id=24, title="Missing annual report", file="minutes/missing_annual_report.pdf")
        MeetingNote.objects.create(id=31, title="October minutes", file="minutes/first.pdf")
        records = [{"id": note.pk, "file": note.file.name, "audience": note.audience,
                    "created_at": note.created_at.isoformat(), "posted_by_id": note.posted_by_id}
                   for note in MeetingNote.objects.filter(pk__in=[24, 31])]
        self.manifest = make_reconciled_manifest(
            self.backup, records, link_ids=[31], unlinked_names=["minutes/SECOND..pdf"], unresolved_ids=[24],
        )
        self.manifest_path, self.receipt_path = self.root / "manifest.json", self.root / "receipt.json"
        write_json_exclusive(self.manifest_path, self.manifest)
        write_json_exclusive(self.receipt_path, make_receipt(self.manifest))
        self.opened = []
        storage = patch("core.management.commands.link_minutes_r2.storages", {"meeting_minutes": self})
        storage.start()
        self.addCleanup(storage.stop)
        configured = copy.deepcopy(settings.STORAGES)
        configured["meeting_minutes"] = {"BACKEND": "core.r2_storage.PrivateMinutesR2Storage", "OPTIONS": {"bucket_name": BUCKET}}
        override = override_settings(STORAGES=configured)
        override.enable()
        self.addCleanup(override.disable)

    def open(self, key, mode):
        self.opened.append(key)
        item = next(item for item in manifest_documents(self.manifest) if item["r2_key"] == key)
        return BytesIO(self.source[item["legacy_name"]])

    def command(self, **options):
        output = StringIO()
        arguments = {"manifest": str(self.manifest_path), "stdout": output, **options}
        if not options.get("database_only"):
            arguments["receipt"] = str(self.receipt_path)
        call_command("link_minutes_r2", **arguments)
        return output.getvalue()

    def test_verified_dry_run_does_not_change_records_and_checks_unlinked_bytes(self):
        before = list(MeetingNote.objects.order_by("pk").values())
        output = self.command()
        self.assertIn("1 links would update", output)
        self.assertIn("unresolved records unchanged: [24]", output)
        self.assertEqual(list(MeetingNote.objects.order_by("pk").values()), before)
        self.assertEqual(set(self.opened), {item["r2_key"] for item in manifest_documents(self.manifest)})

    def test_apply_and_retry_update_only_31_and_never_associate_orphan_with_24(self):
        before = MeetingNote.objects.filter(pk=24).values().get()
        with patch("core.signals.notify_users") as notify:
            self.command(apply=True)
            self.assertIn("0 links updated", self.command(apply=True))
        notify.assert_not_called()
        self.assertEqual(MeetingNote.objects.filter(pk=24).values().get(), before)
        note = MeetingNote.objects.get(pk=31)
        self.assertEqual(note.minutes_pdf.name, self.manifest["entries"][0]["r2_key"])
        self.assertEqual(note.audience, "all_members")
        self.assertEqual(note.file.name, "minutes/first.pdf")
        self.assertFalse(MeetingNote.objects.filter(minutes_pdf=self.manifest["unlinked_documents"][0]["r2_key"]).exists())

    def test_database_only_cannot_apply_and_does_not_touch_storage(self):
        before = list(MeetingNote.objects.order_by("pk").values())
        self.assertIn("R2 bytes/receipt NOT verified", self.command(database_only=True))
        self.assertFalse(self.opened)
        with self.assertRaises(CommandError):
            self.command(database_only=True, apply=True)
        self.assertEqual(list(MeetingNote.objects.order_by("pk").values()), before)

    def test_database_only_supports_old_schema_without_selecting_private_fields(self):
        from django.db import connection
        columns = [type("Column", (), {"name": name})() for name in ("id", "file", "audience", "created_at", "posted_by_id")]
        with patch.object(connection.introspection, "get_table_description", return_value=columns):
            output = self.command(database_only=True)
        self.assertIn("Private columns are absent", output)
        self.assertFalse(MeetingNote.objects.exclude(minutes_pdf="").exists())

    def test_unresolved_exception_never_covers_new_legacy_attachment(self):
        MeetingNote.objects.create(title="Unpreserved", file="minutes/new_upload.pdf")
        with self.assertRaises(CommandError):
            self.command(apply=True)
        self.assertFalse(MeetingNote.objects.exclude(minutes_pdf="").exists())
