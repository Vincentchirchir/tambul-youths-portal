"""Backup-to-R2 migration primitives. No Django initialization or database access."""

from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
from urllib.parse import quote
import uuid


BUCKET = "tambul-portal-media"
MAX_PDF_BYTES = 10 * 1024 * 1024
AUDIENCES = {"all_members", "committee_only"}


class MigrationError(Exception):
    """Messages contain no credentials or document contents."""


def read_json(path):
    return json.loads(Path(path).read_text())


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_json_exclusive(path, value):
    # Do not overwrite a reviewed manifest, receipt, or source backup.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(value, output, indent=2, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())


def safe_legacy_name(name):
    if not isinstance(name, str):
        raise MigrationError("Invalid legacy filename.")
    path = PurePosixPath(name)
    if (path.is_absolute() or ".." in path.parts or "\\" in name
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
            or not name.startswith("minutes/") or str(path) != name
            or not name.lower().endswith(".pdf")):
        raise MigrationError("Unsafe or non-PDF legacy filename.")
    return path.name


def validate_pdf(data):
    if not data or len(data) > MAX_PDF_BYTES or not data.startswith(b"%PDF-"):
        raise MigrationError("Backup PDF header or size is invalid.")
    try:
        from pypdf import PdfReader
        pdf = PdfReader(BytesIO(data), strict=True)
        if pdf.is_encrypted or not pdf.pages:
            raise MigrationError("Backup PDF is encrypted or has no readable pages.")
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("Backup PDF cannot be parsed; no contents are reported.") from None


class VerifiedBackup:
    """Use an existing verified receipt; check only selected source PDFs as read.

    Does not rerun backup creation or full-archive verification, extract files,
    modify the backup, or contact Render.
    """

    def __init__(self, directory):
        self.directory = Path(directory)
        before = read_json(self.directory / "inventory-before.json")
        after = read_json(self.directory / "inventory-after.json")
        receipt = read_json(self.directory / "verified.json")
        if before != after or receipt.get("status") != "VERIFIED":
            raise MigrationError("The existing backup inventories/receipt are inconsistent.")
        self.files = before["files"]
        if receipt.get("files") != len(self.files):
            raise MigrationError("Backup receipt file count is inconsistent.")
        self.archive_sha256 = receipt["archive_sha256"]

    def read_pdf(self, name):
        safe_legacy_name(name)
        info = self.files.get(name)
        if not info or not 0 < info["size"] <= MAX_PDF_BYTES:
            raise MigrationError("PDF is absent from the verified inventory or exceeds the size limit.")
        with tarfile.open(self.directory / "media.tar", "r:") as archive:
            matches = [item for item in archive.getmembers() if item.name == "media/" + name]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size != info["size"]:
                raise MigrationError("Expected a single regular PDF entry matching the inventory.")
            with archive.extractfile(matches[0]) as source:
                data = source.read(MAX_PDF_BYTES + 1)
        if len(data) != info["size"] or hashlib.sha256(data).hexdigest() != info["sha256"]:
            raise MigrationError("Source PDF no longer matches the verified inventory.")
        validate_pdf(data)
        return data

    def inventory(self):
        entries = []
        for name in sorted(self.files):
            if name.startswith("minutes/") and name.lower().endswith(".pdf"):
                data = self.read_pdf(name)
                entries.append({
                    "legacy_name": name, "original_name": safe_legacy_name(name),
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                })
        return {"schema": 1, "archive_sha256": self.archive_sha256, "pdfs": entries}


def make_manifest(backup, records, expected_count=2):
    if not isinstance(records, list) or len(records) != expected_count:
        raise MigrationError("Record export does not match the expected meeting count.")
    backup_pdfs = {name for name in backup.files if name.startswith("minutes/") and name.lower().endswith(".pdf")}
    if {row.get("file") for row in records} != backup_pdfs:
        raise MigrationError("Meeting export does not cover exactly the backed-up minutes PDFs.")
    migration_id = uuid.uuid4().hex
    entries = []
    for row in records:
        name = row.get("file")
        original = safe_legacy_name(name)
        if type(row.get("id")) is not int or row["id"] <= 0 or row.get("audience") not in AUDIENCES:
            raise MigrationError("Record export has an invalid meeting ID or audience.")
        if not row.get("created_at") or "posted_by_id" not in row:
            raise MigrationError("Record export must include created_at and posted_by_id identity guards.")
        try:
            created_at = datetime.fromisoformat(row["created_at"])
            if created_at.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            raise MigrationError("Record creation time must be an ISO timestamp with timezone.") from None
        data = backup.read_pdf(name)
        key = f"minutes/migrated/{migration_id}/{row['id']}/{original}"
        if len(key) > 255:
            raise MigrationError("The preserved filename makes the R2 key exceed the model limit.")
        entries.append({
            "meeting_id": row["id"], "legacy_name": name, "audience": row["audience"],
            "created_at": row["created_at"], "posted_by_id": row["posted_by_id"],
            "original_name": original, "r2_key": key,
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
    manifest = {
        "schema": 1, "bucket": BUCKET, "migration_id": migration_id,
        "archive_sha256": backup.archive_sha256, "entries": entries,
    }
    validate_manifest(manifest, expected_count)
    return manifest


def make_reconciled_manifest(backup, records, *, link_ids, unlinked_names, unresolved_ids):
    """Account for every source without assigning unmatched files to meetings.

    Plan from the already verified inventory. The copy step rechecks source
    bytes. Additional identity guards are retained only when actually supplied.
    """
    by_id = {row["id"]: row for row in records}
    if (len(by_id) != len(records) or set(by_id) != set(link_ids) | set(unresolved_ids)
            or set(link_ids) & set(unresolved_ids)):
        raise MigrationError("Export must cover exactly the explicitly linked and unresolved meetings.")
    identifier = uuid.uuid4().hex

    def document(name, key):
        original = safe_legacy_name(name)
        info = backup.files.get(name)
        if info is None:
            raise MigrationError("A selected document is not present in the verified inventory.")
        return {"legacy_name": name, "original_name": original, "r2_key": key,
                "size": info["size"], "sha256": info["sha256"]}

    def identity(row):
        result = {"meeting_id": row["id"], "audience": row["audience"]}
        for name in ("created_at", "posted_by_id"):
            if name in row:
                result[name] = row[name]
        return result

    entries = []
    for meeting_id in link_ids:
        row = by_id[meeting_id]
        original = safe_legacy_name(row["file"])
        entry = document(row["file"], f"minutes/migrated/{identifier}/{meeting_id}/{original}")
        entry.update(identity(row))
        entries.append(entry)
    unlinked = [document(name, f"minutes/unlinked/{identifier}/{safe_legacy_name(name)}") for name in unlinked_names]
    unresolved = []
    for meeting_id in unresolved_ids:
        row = by_id[meeting_id]
        item = identity(row)
        if "file" in row:
            item["legacy_name"] = row["file"]
        item["reason"] = "Attachment missing on the inspected Render instance; recovery pending. Preserve this record unchanged."
        unresolved.append(item)
    expected = {name for name in backup.files if name.startswith("minutes/") and name.lower().endswith(".pdf")}
    if {item["legacy_name"] for item in entries + unlinked} != expected:
        raise MigrationError("Every backed-up PDF must be linked or explicitly preserved unlinked.")
    manifest = {"schema": 2, "bucket": BUCKET, "migration_id": identifier,
                "archive_sha256": backup.archive_sha256, "entries": entries,
                "unlinked_documents": unlinked, "unresolved_records": unresolved}
    validate_manifest(manifest)
    return manifest


def manifest_documents(manifest):
    return manifest["entries"] + manifest.get("unlinked_documents", [])


def _validate_identity(item, required=False):
    if type(item.get("meeting_id")) is not int or item["meeting_id"] <= 0 or item.get("audience") not in AUDIENCES:
        raise MigrationError("Invalid meeting ID or audience in manifest.")
    if required and (not item.get("created_at") or "posted_by_id" not in item):
        raise MigrationError("Manifest is missing meeting identity guards.")
    if "created_at" in item:
        try:
            if datetime.fromisoformat(item["created_at"]).tzinfo is None:
                raise ValueError
        except (TypeError, ValueError):
            raise MigrationError("Invalid meeting creation timestamp.") from None
    if "posted_by_id" in item and item["posted_by_id"] is not None and (
        type(item["posted_by_id"]) is not int or item["posted_by_id"] <= 0
    ):
        raise MigrationError("Invalid meeting author identity guard.")


def validate_manifest(manifest, expected_count=2):
    schema = manifest.get("schema")
    if schema not in {1, 2} or manifest.get("bucket") != BUCKET:
        raise MigrationError("Manifest schema or target bucket is invalid.")
    identifier = manifest.get("migration_id", "")
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        raise MigrationError("Invalid migration identifier.")
    entries = manifest.get("entries", [])
    unlinked = manifest.get("unlinked_documents", [])
    unresolved = manifest.get("unresolved_records", [])
    if (not isinstance(entries, list) or not entries or not isinstance(unlinked, list)
            or not isinstance(unresolved, list)
            or len(entries) + len(unlinked) != expected_count
            or (schema == 1 and (unlinked or unresolved))):
        raise MigrationError("Manifest document counts/dispositions are unexpected.")
    ids, keys, names = set(), set(), set()
    for item in entries:
        _validate_identity(item, required=schema == 1)
        original = safe_legacy_name(item["legacy_name"])
        meeting_id = item["meeting_id"]
        if type(meeting_id) is not int or meeting_id <= 0 or meeting_id in ids:
            raise MigrationError("Meeting IDs must be positive and unique.")
        key = f"minutes/migrated/{identifier}/{meeting_id}/{original}"
        if item["original_name"] != original or item["r2_key"] != key or len(key) > 255 or key in keys or item["legacy_name"] in names:
            raise MigrationError("Manifest key or original filename was changed.")
        if item["audience"] not in AUDIENCES or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]):
            raise MigrationError("Invalid audience or checksum in manifest.")
        if type(item["size"]) is not int or not 0 < item["size"] <= MAX_PDF_BYTES:
            raise MigrationError("Invalid PDF size in manifest.")
        ids.add(meeting_id)
        keys.add(key)
        names.add(item["legacy_name"])
    for item in unlinked:
        original = safe_legacy_name(item["legacy_name"])
        key = f"minutes/unlinked/{identifier}/{original}"
        if ("meeting_id" in item or "audience" in item or item["original_name"] != original
                or item["r2_key"] != key or len(key) > 255 or key in keys or item["legacy_name"] in names):
            raise MigrationError("Unlinked documents must have no meeting/audience association and a separate key.")
        if not re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) or type(item["size"]) is not int or not 0 < item["size"] <= MAX_PDF_BYTES:
            raise MigrationError("Invalid unlinked document checksum/size.")
        keys.add(key)
        names.add(item["legacy_name"])
    for item in unresolved:
        _validate_identity(item)
        if item["meeting_id"] in ids or not item.get("reason") or "r2_key" in item or item.get("legacy_name") in names:
            raise MigrationError("Unresolved records must remain separate from every stored document.")
        ids.add(item["meeting_id"])
    return entries


def object_metadata(manifest, item):
    metadata = {
        "migration-id": manifest["migration_id"],
        "sha256": item["sha256"], "original-name": quote(item["original_name"], safe=""),
    }
    if "meeting_id" in item:
        metadata["meeting-id"] = str(item["meeting_id"])
    else:
        metadata["disposition"] = "unlinked"
    return metadata


def verify_remote(client, manifest, item):
    target = {"Bucket": manifest["bucket"], "Key": item["r2_key"]}
    head = client.head_object(**target)
    if (head.get("ContentLength") != item["size"]
            or head.get("Metadata") != object_metadata(manifest, item)
            or head.get("CacheControl") != "private, no-store"
            or head.get("ContentType") != "application/pdf"):
        raise MigrationError("R2 object identity, size, or private headers do not match the manifest.")
    response = client.get_object(**target)
    try:
        data = response["Body"].read(item["size"] + 1)
    finally:
        response["Body"].close()
    if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise MigrationError("Retrieved R2 PDF does not match the source checksum.")


def copy_to_r2(client, backup, manifest):
    validate_manifest(manifest)
    entries = manifest_documents(manifest)
    if backup.archive_sha256 != manifest["archive_sha256"]:
        raise MigrationError("Manifest belongs to a different verified backup.")
    # Validate all sources before writing the first object.
    payloads = []
    for item in entries:
        data = backup.read_pdf(item["legacy_name"])
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise MigrationError("Source PDF differs from the reviewed manifest.")
        payloads.append(data)
    for item, data in zip(entries, payloads):
        try:
            client.put_object(
                Bucket=manifest["bucket"], Key=item["r2_key"], Body=data,
                IfNoneMatch="*", ContentType="application/pdf", CacheControl="private, no-store",
                Metadata=object_metadata(manifest, item),
            )
        except Exception as error:
            response = getattr(error, "response", {}) or {}
            if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                # Retain any objects already written, including ambiguous writes.
                # A retry with this same manifest is safe; never delete documents.
                raise
        verify_remote(client, manifest, item)
    receipt = make_receipt(manifest)
    # Private deployment artifacts let the new build link documents without
    # obtaining any files from the retiring Render instance or staging a secret
    # file (which would itself trigger a Render deploy).
    for name, document in (("manifest.json", manifest), ("receipt.json", receipt)):
        payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        key = f"_migrations/{manifest['migration_id']}/{name}"
        try:
            client.put_object(
                Bucket=BUCKET, Key=key, Body=payload, IfNoneMatch="*",
                ContentType="application/json", CacheControl="private, no-store",
                Metadata={"sha256": hashlib.sha256(payload).hexdigest(), "migration-id": manifest["migration_id"]},
            )
        except Exception as error:
            response = getattr(error, "response", {}) or {}
            if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise
        response = client.get_object(Bucket=BUCKET, Key=key)
        try:
            remote = response["Body"].read(len(payload) + 1)
        finally:
            response["Body"].close()
        if remote != payload:
            raise MigrationError("Existing private deployment artifact differs; it will not be replaced.")
    return receipt


def make_receipt(manifest):
    validate_manifest(manifest)
    receipt = {"schema": manifest["schema"], "bucket": BUCKET, "manifest_sha256": digest_json(manifest),
               "verified_count": len(manifest_documents(manifest))}
    if manifest["schema"] == 2:
        receipt.update(linked_count=len(manifest["entries"]), unlinked_count=len(manifest["unlinked_documents"]),
                       unresolved_count=len(manifest["unresolved_records"]))
    return receipt


def validate_receipt(manifest, receipt):
    if receipt != make_receipt(manifest):
        raise MigrationError("Copy receipt does not match this reviewed manifest.")


def plan_database_links(manifest, records, *, private_schema_present):
    """Check a read-only row snapshot; return changes for linked entries only."""
    entries = validate_manifest(manifest)
    meetings = {row["id"]: row for row in records}
    unresolved = manifest.get("unresolved_records", [])
    accounted = {item["meeting_id"] for item in entries + unresolved}
    for row in records:
        if (row.get("file") not in (None, "", "None") and row["id"] not in accounted
                and not row.get("minutes_pdf")):
            raise MigrationError("Additional legacy attachments are not accounted for; stop and preserve them first.")

    def check_identity(item):
        row = meetings.get(item["meeting_id"])
        if row is None or row["audience"] != item["audience"]:
            raise MigrationError("A meeting ID or audience changed; nothing linked.")
        if "legacy_name" in item and row.get("file") != item["legacy_name"]:
            raise MigrationError("A meeting source reference changed; nothing linked.")
        if "created_at" in item:
            actual = row["created_at"]
            if isinstance(actual, str):
                actual = datetime.fromisoformat(actual)
            if actual != datetime.fromisoformat(item["created_at"]):
                raise MigrationError("A meeting creation identity changed; nothing linked.")
        if "posted_by_id" in item and row.get("posted_by_id") != item["posted_by_id"]:
            raise MigrationError("A meeting author identity changed; nothing linked.")
        return row

    for item in unresolved:
        row = check_identity(item)
        if row.get("minutes_pdf") or row.get("file") in {doc["legacy_name"] for doc in manifest_documents(manifest)}:
            raise MigrationError("An unresolved record now has a stored document; reconcile its exception before release.")
    pending, already_linked = [], []
    for item in entries:
        row = check_identity(item)
        updates = {"minutes_pdf": item["r2_key"], "minutes_original_name": item["original_name"],
                   "minutes_size": item["size"], "minutes_sha256": item["sha256"]}
        if private_schema_present and row.get("minutes_pdf"):
            if any(row.get(name) != value for name, value in updates.items()):
                raise MigrationError("An existing private attachment differs; it will not be overwritten.")
            already_linked.append(item["meeting_id"])
        else:
            pending.append({"meeting_id": item["meeting_id"], "updates": updates})
    return {"pending": pending, "already_linked": already_linked,
            "unresolved_ids": [item["meeting_id"] for item in unresolved],
            "unlinked_document_keys": [item["r2_key"] for item in manifest.get("unlinked_documents", [])],
            "private_schema_present": private_schema_present}
