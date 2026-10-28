"""Read-only release inventory using an existing Render PostgreSQL connection.

No Django setup, R2 operations, database writes, or credential output. The
connection is verified against the operator-confirmed meeting records first.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


OCTOBER = "minutes/TAMBUL_HUSTLE_YOUTH_GROUP_OCT_2026_MINUTES.pdf"
AGM = "minutes/AGM_MINUTES_TAMBUL_HUSTLE_YOUTH_GROUP..pdf"
MEDIA_FIELDS = (
    ("accounts_user", "profile_photo"),
    ("core_signatory", "signature_image"),
    ("core_signatory", "stamp_image"),
    ("core_committeeletter", "pdf_file"),
    ("core_committeeletter", "supporting_attachment"),
)


def audit(connection):
    from psycopg import sql
    from psycopg.rows import dict_row

    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        cursor.execute("SHOW transaction_read_only")
        if cursor.fetchone()["transaction_read_only"] != "on":
            raise RuntimeError("Read-only transaction was not established.")
        cursor.execute(
            "SELECT id, file, audience, created_at, posted_by_id, title "
            "FROM core_meetingnote WHERE id IN (24, 31) ORDER BY id"
        )
        meetings = cursor.fetchall()
        by_id = {row["id"]: row for row in meetings}
        if (set(by_id) != {24, 31} or any(row["audience"] != "all_members" for row in meetings)
                or by_id[31]["file"] != OCTOBER or by_id[24]["file"] == AGM):
            raise RuntimeError("Configured database does not match the confirmed release records; audit stopped.")
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'core_meetingnote'"
        )
        columns = {row["column_name"] for row in cursor.fetchall()}
        has_private_schema = {"minutes_pdf", "minutes_original_name", "minutes_sha256", "minutes_size"} <= columns
        fields = sql.SQL("id, file, audience")
        if has_private_schema:
            fields += sql.SQL(", minutes_pdf")
        cursor.execute(sql.SQL(
            "SELECT {} FROM core_meetingnote WHERE file IS NOT NULL AND file NOT IN ('', 'None') ORDER BY id"
        ).format(fields))
        legacy_references = cursor.fetchall()
        report = []
        for table, field in MEDIA_FIELDS:
            cursor.execute(sql.SQL(
                "SELECT id, {field} AS name FROM {table} "
                "WHERE {field} IS NOT NULL AND {field} NOT IN ('', 'None') ORDER BY id"
            ).format(field=sql.Identifier(field), table=sql.Identifier(table)))
            references = cursor.fetchall()
            report.append({"table": table, "field": field, "count": len(references), "references": references})
    return {
        "schema": 1, "audited_at": datetime.now(timezone.utc).isoformat(),
        "source": "existing Render PostgreSQL configuration, matched confirmed records",
        "transaction_read_only": True, "private_schema_present": has_private_schema,
        "meetings": meetings, "legacy_meeting_references": legacy_references,
        "non_minutes_media": report,
        "non_minutes_reference_count": sum(item["count"] for item in report),
        "filesystem_existence": "Not inspected by this database-reference audit",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Read only DATABASE_URL from this existing private file.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise RuntimeError("Choose a new private output path; previous audits are never overwritten.")
        if args.env_file:
            from decouple import Config, RepositoryEnv
            dsn = Config(RepositoryEnv(str(args.env_file)))("DATABASE_URL", default="")
        else:
            dsn = os.environ.get("DATABASE_URL", "")
        host = urlsplit(dsn).hostname or ""
        if not (host.endswith(".render.com") or host.endswith(".render.internal")):
            raise RuntimeError("Expected the existing external Render PostgreSQL configuration; target not printed.")
        import psycopg
        with psycopg.connect(
            dsn, sslmode="require", connect_timeout=10,
            options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000",
        ) as connection:
            result = audit(connection)
            connection.rollback()
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(result, output, default=lambda value: value.isoformat(), indent=2)
            output.write("\n")
        print("PASS: read-only record and non-minutes reference audit saved privately; no database records changed.")
        print("Non-minutes media references: " + str(result["non_minutes_reference_count"]))
        return 0
    except RuntimeError as error:
        print("STOP: " + str(error), file=sys.stderr)
    except Exception as error:
        print("STOP: read-only audit failed (" + type(error).__name__ + "); target and credentials suppressed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
