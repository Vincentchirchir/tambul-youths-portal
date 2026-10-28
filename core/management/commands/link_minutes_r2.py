"""Verify private documents and link selected meetings; dry run unless --apply."""

import hashlib
import json
import re

from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.minutes_migration import (
    MigrationError, read_json, validate_manifest, validate_receipt,
    manifest_documents, plan_database_links,
)
from core.models import MeetingNote


PRIVATE_FIELDS = ("minutes_pdf", "minutes_original_name", "minutes_size", "minutes_sha256")


class Command(BaseCommand):
    help = "Dry-run a reviewed migration; --apply links selected PDFs, preserves unlinked documents, and leaves unresolved records unchanged."

    def add_arguments(self, parser):
        source = parser.add_mutually_exclusive_group(required=True)
        source.add_argument("--manifest")
        source.add_argument("--migration-id")
        parser.add_argument("--receipt")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--database-only", action="store_true", help="Read-only database preflight; no storage access or copy verification.")

    def handle(self, *args, **options):
        try:
            self.link(options)
        except MigrationError as error:
            raise CommandError(str(error)) from None
        except Exception:
            raise CommandError("Minutes linking/preflight failed; sensitive details suppressed. Review state before retrying.") from None

    def link(self, options):
        database_only = options.get("database_only", False)
        if settings.MINUTES_R2_UPLOADS_ENABLED:
            raise MigrationError("Keep MINUTES_R2_UPLOADS_ENABLED=0 during this operation.")
        if database_only and (options["apply"] or not options.get("manifest") or options.get("receipt")):
            raise MigrationError("--database-only requires a local manifest without --apply or --receipt.")
        backend = None
        if not database_only:
            configured = settings.STORAGES["meeting_minutes"]
            if configured.get("BACKEND") != "core.r2_storage.PrivateMinutesR2Storage" or configured.get("OPTIONS", {}).get("bucket_name") != "tambul-portal-media":
                raise MigrationError("Configured private minutes backend/bucket does not match the manifest.")
            backend = storages["meeting_minutes"]
        if options.get("migration_id"):
            identifier = options["migration_id"]
            if not re.fullmatch(r"[a-f0-9]{32}", identifier) or options.get("receipt"):
                raise MigrationError("Use a reviewed migration ID without a separate receipt path.")

            def artifact(name):
                with backend.open(f"_migrations/{identifier}/{name}", "rb") as source:
                    data = source.read(65537)
                if len(data) > 65536:
                    raise MigrationError("Private deployment artifact exceeds its size bound.")
                return json.loads(data)

            manifest, receipt = artifact("manifest.json"), artifact("receipt.json")
            if manifest.get("migration_id") != identifier:
                raise MigrationError("Remote artifact does not match the reviewed migration ID.")
        else:
            manifest = read_json(options["manifest"])
            receipt = read_json(options["receipt"]) if options.get("receipt") else None
        entries = validate_manifest(manifest)
        if not database_only:
            if receipt is None:
                raise MigrationError("A verified dry run/apply requires its real R2 copy receipt.")
            validate_receipt(manifest, receipt)
            for item in manifest_documents(manifest):
                with backend.open(item["r2_key"], "rb") as source:
                    data = source.read(item["size"] + 1)
                if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise MigrationError("R2 PDF bytes do not match the reviewed manifest.")
        outer_transaction = connection.in_atomic_block
        with transaction.atomic():
            if not options["apply"] and not outer_transaction:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            with connection.cursor() as cursor:
                columns = {column.name for column in connection.introspection.get_table_description(cursor, MeetingNote._meta.db_table)}
            private_schema = set(PRIVATE_FIELDS) <= columns
            if options["apply"] and not private_schema:
                raise MigrationError("Apply schema migration 0024 in the approved release before linking.")
            fields = ["id", "file", "audience", "created_at", "posted_by_id"]
            if private_schema:
                fields.extend(PRIVATE_FIELDS)
            rows = MeetingNote.objects.order_by("pk")
            if options["apply"]:
                # Lock only rows eligible for updates; unresolved records and
                # text-only meetings are read but never locked for modification.
                list(MeetingNote.objects.filter(pk__in=[item["meeting_id"] for item in entries])
                     .order_by("pk").select_for_update().values_list("pk", flat=True))
            plan = plan_database_links(manifest, list(rows.values(*fields)), private_schema_present=private_schema)
            if options["apply"]:
                for item in plan["pending"]:
                    # Unresolved records never enter this loop. No post_save
                    # signal, email, or notification is emitted.
                    MeetingNote.objects.filter(pk=item["meeting_id"]).update(**item["updates"])
        mode = "DATABASE PREFLIGHT ONLY" if database_only else "APPLIED" if options["apply"] else "DRY RUN"
        self.stdout.write(
            f"{mode}: {len(entries)} linked candidates; {len(plan['pending'])} links "
            f"{'updated' if options['apply'] else 'would update'}; "
            f"{len(plan['unlinked_document_keys'])} documents remain unlinked; "
            f"unresolved records unchanged: {plan['unresolved_ids']}."
        )
        if database_only:
            self.stdout.write("R2 bytes/receipt NOT verified by this database-only preflight; no records changed.")
        if not private_schema:
            self.stdout.write("Private columns are absent; schema migration 0024 is required before --apply.")
