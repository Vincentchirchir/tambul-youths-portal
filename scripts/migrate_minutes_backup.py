"""Prepare/copy/verify minutes from a verified archive; never initializes Django."""

import argparse
import logging
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.minutes_migration import (
    MigrationError, VerifiedBackup, copy_to_r2, read_json, make_manifest,
    validate_manifest, verify_remote, write_json_exclusive, make_reconciled_manifest, manifest_documents,
)
from scripts.check_r2_connection import configuration, create_client, prompt_configuration


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inventory", "plan", "copy", "verify"))
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--records", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prompt", action="store_true")
    parser.add_argument("--link-meeting", type=int, action="append", default=[])
    parser.add_argument("--preserve-unlinked", action="append", default=[])
    parser.add_argument("--unresolved-meeting", type=int, action="append", default=[])
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    try:
        if args.operation in {"inventory", "plan"}:
            if not args.backup or not args.output or args.output.exists():
                raise MigrationError("Supply --backup and a new --output path.")
            backup = VerifiedBackup(args.backup)
            if args.operation == "inventory":
                result = backup.inventory()
            else:
                if not args.records:
                    raise MigrationError("Plan requires the actual --records export; IDs/audiences are never inferred.")
                records = read_json(args.records)
                if args.link_meeting or args.preserve_unlinked or args.unresolved_meeting:
                    result = make_reconciled_manifest(
                        backup, records, link_ids=args.link_meeting, unlinked_names=args.preserve_unlinked,
                        unresolved_ids=args.unresolved_meeting,
                    )
                else:
                    result = make_manifest(backup, records)
            write_json_exclusive(args.output, result)
            print("PASS: local preparation written with restricted permissions; no R2/database access.")
            return 0
        if not args.manifest:
            raise MigrationError("Supply the reviewed --manifest.")
        manifest = read_json(args.manifest)
        validate_manifest(manifest)
        entries = manifest_documents(manifest)
        if args.operation == "copy" and (not args.backup or not args.output or args.output.exists()):
            raise MigrationError("Copy requires --backup and a new --output receipt path.")
        import os
        config = prompt_configuration() if args.prompt else configuration(os.environ)
        if any(entry["size"] > config.max_upload_bytes for entry in entries):
            raise MigrationError("PDF exceeds the configured minutes size limit.")
        client = create_client(config)
        try:
            if args.operation == "copy":
                receipt = copy_to_r2(client, VerifiedBackup(args.backup), manifest)
                write_json_exclusive(args.output, receipt)
            else:
                for item in entries:
                    verify_remote(client, manifest, item)
        finally:
            client.close()
        print(f"PASS: {len(entries)} R2 PDFs verified; {len(manifest['entries'])} linked candidates, "
              f"{len(manifest.get('unlinked_documents', []))} preserved unlinked; no database records accessed or changed.")
        return 0
    except MigrationError as error:
        print("STOP: " + str(error), file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("STOP: interrupted; retain the manifest and retry; no R2 document is deleted.", file=sys.stderr)
    except Exception:
        print("STOP: migration operation failed; details suppressed. Retain the manifest and existing R2 objects.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
