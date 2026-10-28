# Meeting minutes storage and production media preservation

The October 2026 PDF was confirmed readable on the inspected live Render
instance: `minutes/TAMBUL_HUSTLE_YOUTH_GROUP_OCT_2026_MINUTES.pdf`, 83,652 bytes,
with a PDF header. Its HTTP 404 is a retrieval failure. All current media must be
preserved before any deployment, restart, disk attachment, or data migration.

The operator has confirmed the two-PDF Render backup and a successful private R2
connection test for `tambul-portal-media`. Its credentials are saved in Render
using **Save only**. Do not repeat those completed checks for this release.
Continue with the [production release procedure](minutes_production_release.md),
which records the completed read-only metadata and media-reference audit, the
Meeting 31-only migration, the private unlinked AGM copy, and unresolved historical
Meeting 24 and Official Letter 6 files. No production schema or records were changed;
R2 document copying and deployment remain pending.

## Back up every live instance

Use a trusted workstation with Python 3, OpenSSH, and encrypted storage. Obtain
the service's SSH user and host from Render's Connect menu. Use Render's
instance-specific SSH target: an ordinary service SSH target selects a random
instance. Match the instance to the one inspected in the production investigation,
then repeat for every other running instance. Use existing live instances, not a
cron job, one-off job, preview, or ephemeral shell.

If SSH access needs configuring, follow Render's account-level SSH key setup.
Verify the SSH host fingerprint against Render's regional fingerprints before
the first connection. Do not disable host-key checking or change service settings.
See [Render SSH access](https://render.com/docs/ssh).

Copy `scripts/backup_render_media.py` to the workstation, or run it from the
reviewed local checkout. From the repository root, run:

```bash
python3 -B scripts/backup_render_media.py \
  --ssh-target 'INSTANCE_USER@ssh.REGION.render.com' \
  --destination '/secure/local/path/tambul-media-instance-1'
```

Replace the SSH target and destination. The destination's parent must already
exist, and the destination itself must be new. Repeat with a different destination
for each instance.

The helper reads `/opt/render/project/src/media` in full, including minutes,
profile photos, protected signatures and stamps, generated letters, supporting
attachments, hidden files, and empty directories. It executes only standard
library filesystem reads on Render. It does not import Django, query PostgreSQL,
create an archive on Render, or modify production files. The archive is streamed
over SSH directly to the workstation with restricted local permissions.

The helper hashes each source file before and after transfer and compares those
inventories, including instance identity. It then checks every archived file's
name, size, and SHA-256 without extracting anything. A successful directory has:

```text
inventory-before.json
media.tar
inventory-after.json
verified.json
```

Accept the backup only if the command exits zero and prints `"status": "VERIFIED"`.
The receipt includes the instance, file count, total bytes, and archive SHA-256.
Inspect `inventory-before.json` to confirm the October PDF's relative path and
83,652-byte size, along with the other media categories. The SHA-256 inventory
gives a stronger integrity check than the PDF header alone.

Recheck the local copy without connecting to Render:

```bash
python3 -B scripts/backup_render_media.py \
  --destination '/secure/local/path/tambul-media-instance-1' \
  --verify-existing
```

Keep a second copy of the entire backup directory on separately protected storage
and run the same verification against that copy. These archives contain private
documents and signature images; keep them outside Git, public media, and publicly
accessible buckets.

If the source changes during transfer, the program exits with `UNVERIFIED` and
keeps the partial or inconsistent copy for investigation. Repeat into a new
destination during a coordinated interval without media writes. Media writes
include profile photo changes, signatory edits, document attachments, and letter
PDF generation as well as minutes uploads. A letter PDF GET can generate a file.
No application restart or configuration change is needed to arrange this interval.

Symlinks, special files, permissions failures, or transfer errors also prevent a
verified result. Resolve each item before declaring the media backup complete;
the helper does not silently omit those paths or follow links outside media.

The matching inventories verify the captured files across the observed backup
interval; they are not an atomic PostgreSQL/filesystem snapshot. Confirm a current
PostgreSQL backup through the existing database backup process as a separate
release prerequisite. Keep the per-instance copies separate if their files differ.

## Preserve local media through a later deployment

Backups are mandatory but do not by themselves make local uploads persistent.
Render replaces ephemeral files on deployment or restart. Adding a persistent
disk itself triggers a deploy, so it must follow preservation, not precede it.
See [Render persistent disks](https://render.com/docs/disks).

Before production release, verify a recovery copy can be restored into a fresh
isolated directory and matches the inventory. Do not overwrite an existing live
media directory as a restoration test.

The release plan must provide every replacement instance with all remaining local
media at its original relative paths before it receives traffic. This requires an
explicit, tested persistence or restoration arrangement for legacy photos,
signatures, stamps, letters, and attachments. The minutes-only R2 integration
does not implement that arrangement or change those fields' storage backends.
Do not deploy until that arrangement and verified current backups are confirmed.
Coordinate media writes through the final backup and cutover so uploads made
after the backup cannot be lost.

After backup confirmation and separate production authorization, arrange the
additive schema migration before the new application release receives traffic.
The old application remains compatible with the added columns. Deploying code
that selects the new fields before its migration would break meeting queries on
both dashboards. Use a reviewed pre-deploy database step or another controlled
operation; this change does not alter Render's deployment commands automatically.
Keep new R2 uploads disabled until the new release, legacy media availability,
and dedicated staging R2 checks have been verified.

## Private R2 configuration

Use a dedicated private bucket with public custom-domain and `r2.dev` access
disabled. Create bucket-scoped object credentials. Configure these secrets only
in the appropriate environment; never commit real values:

```text
MINUTES_R2_UPLOADS_ENABLED=0
MINUTES_MAX_UPLOAD_BYTES=10485760
MINUTES_R2_ACCOUNT_ID=<account ID>
MINUTES_R2_BUCKET_NAME=tambul-portal-media
MINUTES_R2_ACCESS_KEY_ID=<bucket-scoped key>
MINUTES_R2_SECRET_ACCESS_KEY=<secret>
```

The upload switch defaults to off. Text-only posting and legacy downloads remain
available. Missing R2 credentials fail only minutes storage operations rather
than unrelated application startup. Disabling new uploads later does not disable
reads of already stored private documents.

Only the new `MeetingNote.minutes_pdf` field uses the named `meeting_minutes`
backend. `MeetingNote.file` retains its existing legacy storage and references.
The default storage, `MEDIA_ROOT`, `MEDIA_URL`, and the current effective static
backend retain their behavior. Model migration `0024` adds fields only; it does
not copy documents, rewrite existing filenames, or delete records.

New PDFs are checked for extension, header, size, unencrypted readable structure,
and at least one page. The original filename, byte size, and SHA-256 are recorded.
Objects use generated unique names and are never intentionally overwritten.
PDF parsing checks structure; it does not constitute a malware scan.

Portal and admin forms use the same validation and private field. The admin shows
authorized download links and the preserved legacy filename, without exposing
raw storage URLs. Existing default-storage uploads are not redirected to R2.

## Test the R2 connection without deploying

Render's **Save only** stores values for a future deployment; the running service
does not receive them immediately. Do not redeploy or restart to make the test
work. Run the reviewed standalone script on a trusted workstation instead. See
[Render environment variables](https://render.com/docs/configure-environment-variables).

The six settings in `tambulyouthgroup_portal/settings.py` are mapped as follows:

| Variable | Application use | Connection test check |
| --- | --- | --- |
| `MINUTES_R2_UPLOADS_ENABLED` | Gate new minutes uploads; default off | Must explicitly be `0`, `false`, `no`, or `off` |
| `MINUTES_MAX_UPLOAD_BYTES` | Limit PDF size; default 10 MiB | Positive integer; synthetic object must fit |
| `MINUTES_R2_ACCOUNT_ID` | Construct HTTPS S3 endpoint | Require 32 hexadecimal characters; never print the value |
| `MINUTES_R2_BUCKET_NAME` | Named minutes storage bucket | Must equal `tambul-portal-media` |
| `MINUTES_R2_ACCESS_KEY_ID` | Explicit SDK access key | Required; hidden prompt; never printed |
| `MINUTES_R2_SECRET_ACCESS_KEY` | Explicit SDK secret | Required; hidden prompt; never printed |

An offline test imports the actual production settings with invented values,
disables `.env` loading and network connections, and asserts all six mappings,
including trimming, Boolean conversion, size conversion, region `auto`, and the
unchanged default/static backends. This verifies repository wiring. Only an
actual test with the saved credentials can verify bucket access; it does not
prove those pending values have reached Render's running application.

### Inspect privacy first

In Cloudflare, open **R2 object storage → tambul-portal-media → Settings**. Check
these settings without changing anything:

1. **Public Development URL** is disabled; public URL access is not allowed.
2. **Custom Domains** has no domain allowing public bucket access. Prefer no
   connected domains for this private minutes bucket.
3. No Worker or other public proxy exposes this bucket without authorization.
   Inspect any Workers with an R2 binding to this bucket and their routes.
4. The S3 credentials have **Object Read & Write** permission scoped to this
   bucket, rather than unnecessary account-wide bucket administration.
5. No bucket lock/retention rule prevents deleting objects in the test prefix.
   Do not alter retention, lifecycle, event notifications, or access settings to
   run the test. Review any existing event consumers: temporary object creation
   and deletion can trigger them. Stop if their effects are unknown.

If any privacy check fails, stop and report the finding. Do not enable a public
domain, add CORS, disable TLS checks, or change Django security settings.
`r2.dev` and custom domains are independent public paths. An unsigned S3 denial
alone cannot certify those settings or exclude a public Worker. R2 does not
implement S3 bucket ACL APIs, so `get_bucket_acl` is not a privacy check. See
[Cloudflare public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/)
and [S3 compatibility](https://developers.cloudflare.com/r2/api/s3/api/).

### Run from the local reviewed checkout

Use a terminal on the trusted workstation, outside any session recorder. Do not
paste credentials into commands, `.env`, chat, Git, command arguments, or a shell
history. Obtain the same account ID, access key, and secret saved in Render
through the existing secure credential workflow. Do not display or dump Render's
environment. The account ID prompt is hidden too.

From the repository root on that workstation, create an isolated local Python
environment and install only the SDK version pinned in this change. Run each
command separately and stop if it fails:

```bash
R2_PROBE_VENV="$(mktemp -d -t tambul-r2-probe.XXXXXXXX)"
python3 -m venv "$R2_PROBE_VENV"
"$R2_PROBE_VENV/bin/python" -m pip install 'boto3==1.43.118'
"$R2_PROBE_VENV/bin/python" -B scripts/check_r2_connection.py \
  --prompt --private-settings-checked
```

These commands install nothing on Render. `--private-settings-checked` records
your completion of the manual checks above; it does not call Cloudflare's
management API or infer public-domain settings from S3 credentials. The probe
never changes bucket configuration.

At the three visible prompts, press Enter to use `0`, `10485760`, and
`tambul-portal-media`, or supply the exact matching non-secret values saved in
Render. At the next three hidden prompts, enter the account ID, access key ID,
and secret respectively. Input is not echoed. If hidden input is unavailable,
the script refuses to fall back to visible credential input.

`--prompt` deliberately ignores existing environment values and loads no `.env`.
It holds the supplied settings only in this process, without modifying Render,
the parent shell's environment, application configuration, files, or PostgreSQL.
For an already securely provisioned test process, omitting `--prompt` reads all
six variables directly from its environment and requires all six explicitly.
Do not use the live service to activate saved-only values for this purpose.

The test makes only these object requests:

1. Generate a UUID key under `_connection-tests/minutes/` with a small synthetic
   text payload. It reads no existing PDF and does not list bucket contents.
2. Upload with `If-None-Match: *`, `Cache-Control: private, no-store`, and a unique
   ownership marker. A key collision cannot overwrite another object. No ACL is
   sent. Connection/read timeouts, region, and signing match the application's
   R2 client. Automatic retries are disabled specifically for this probe so
   an earlier unknown upload attempt cannot race a retry and cleanup.
3. Check that the object's metadata and length match, retrieve it with the
   supplied credentials, and compare the exact bytes and SHA-256.
4. Make an unsigned HTTPS GET for the same synthetic object, before deleting it.
   The probe's SDK uses path addressing so both requests target the same account
   S3 endpoint and object path. Print the numeric HTTP status, an allowlisted S3
   error code, response category, and whether the synthetic bytes were returned.
   Never print response text, error messages, headers, URLs, or signed query
   strings. Inspect at most 8,193 response bytes; parse only bounded XML without
   DTDs/entities. Require HTTP 401/403, or HTTP 400 with a recognized structured
   authentication error. For example, `InvalidArgument` must identify
   `Authorization` or another explicitly recognized missing-authorization message;
   a generic `InvalidArgument` is insufficient. Any 2xx status fails, including
   HTTP 200 carrying an error-looking body. Returned test bytes also fail
   regardless of status. Generic 400s, 404s, redirects, server errors, network
   errors, and TLS failures remain inconclusive and fail the test. No credential
   or signed URL is sent to the anonymous request. Recheck the object's ownership
   and length with an authenticated HEAD before accepting the rejection.
5. In `finally`, including after a failed retrieval or interrupted/ambiguous
   upload, inspect the exact key's ownership marker. Delete only the matching
   test object, then confirm authenticated HEAD reports it missing. If the PUT
   did not return success, mark cleanup unverified even if HEAD reports missing:
   the in-flight upload could still commit afterward. Existing minutes keys are
   never used. Hard termination, power loss, or network failure can also prevent
   cleanup. See [R2 negative reads and in-flight writes](https://developers.cloudflare.com/r2/reference/consistency/#negative-reads-and-in-flight-writes).

This direct test-object write is isolated from the application's upload switch.
It neither enables production uploads nor tests the Django model, validation,
database migration, or download permissions. Those have separate automated and
staging checks.

### Interpret the result

A successful run prints passes for settings, upload, exact retrieval, unsigned
denial, and cleanup, ending with:

```text
PASS: R2 connection test complete. No Django/database operation was performed.
Bucket privacy depends on the dashboard/Worker checks as well as the unsigned S3 denial.
```

Require exit status zero and every check to pass. Recheck Cloudflare's privacy
settings afterward. The bucket's object browser should have no test object at
the printed key. A prefix may remain visually as an empty logical folder.

The anonymous result line has this format, with actual values filled in:

```text
Anonymous S3 GET: HTTP <status>; S3 code=<allowlisted code>; response=<category>; test_payload_returned=<yes/no>.
```

The older probe discarded its actual status and accepted only 401/403. Its
generic failure message cannot establish whether the response was an
authentication-related 400, an unexpected routing response, or a readable 200.
Run the corrected probe to distinguish them; do not infer a particular status
from that old message. Do not request the old test key after cleanup to diagnose
the original failure: its deletion would make that a different test. Each
corrected run creates and checks a fresh object before cleanup. A failing
anonymous check still executes cleanup in `finally`.

A failure exits nonzero and suppresses raw SDK/HTTP errors, endpoints, and
credentials. Check the named failure phase against bucket-scoped permissions,
credential/account/bucket alignment, the S3 endpoint shown by Cloudflare, network
access, and clock accuracy. Do not enable uploads, broaden bucket access, or
disable checks to force a pass. A bucket with a jurisdiction-specific endpoint
requires a reviewed endpoint configuration change; this integration currently
uses the standard account endpoint.

If `CLEANUP NOT VERIFIED` appears, note the exact printed test key. In Cloudflare's
object browser inspect **only that key**. If it has this run's `tambul-probe-id`
(the UUID filename) and the synthetic test content, remove just that object and
confirm it is gone. If its ownership marker differs, do not delete it. Never
delete a whole prefix or any minutes document. An already-absent key requires no
deletion. After an uncertain upload, an immediate absence is insufficient; check
again after its outcome is resolved. Keep cleanup marked unverified while that
outcome is unknown. Resolve cleanup before treating the connection test as
successful.

Keep a credential-free record of the pass/fail output and manual privacy checks.
No live connection test is run by the offline unit tests:

```bash
tambulyouthgroup_env/bin/python -B -m unittest scripts.test_r2_connection -v
```

Local verification of the corrected anonymous check passed 36 offline
probe/settings tests. These include status/error classification, bounded HTTP
response inspection, URL agreement with the SDK, object presence before
deletion, diagnostic redaction, and cleanup on failure. The existing six
`PrivateR2ConfigurationTests` and five backup-helper tests passed in the prior
preparation stage. No live R2 test was included in these automated checks.

## Download and notification behavior

`/minutes/<id>/download/` requires login and the existing audience check before
accessing storage. A verified private attachment takes precedence; otherwise the
original legacy attachment is used. A missing private object does not silently
fall back to an older legacy version. The response is an attachment with private,
non-cacheable headers. No inline preview or global security relaxation is added.

Old `/media/minutes/<filename>` links resolve an exact meeting-file database
reference and perform the same authorization. Unreferenced files and all other
media paths are not exposed by that compatibility route.

Both dashboards and the detail page use the authorized route. The detail page
also displays written minutes when an attachment reference exists. Future meeting
notifications link to the stable meeting page and retain existing audience-based
recipient selection. Delivery occurs after commit; errors are logged without
making a saved upload appear to have failed. No durable delivery retry queue is
introduced in this change.

Missing attachments produce a controlled 404. Backend timeouts, permission
failures, and invalid credentials produce a controlled 503. Neither outcome
deletes or changes the meeting. R2 operations have bounded connection/read
timeouts. Response delivery handles ASGI with asynchronous chunks; test actual
Render memory and throughput before production use.

## Later PDF migration and rollback

Production copying and migration require separate authorization after backups
are confirmed. For each legacy PDF, copy to a new private key, compare size and
SHA-256, then populate the new private field and metadata only if the meeting's
source reference is unchanged. Preserve its legacy field and original document.
Retain a per-record manifest, make retries idempotent, and report missing sources
for recovery from the verified archive or Secretary-held originals. Never resend
creation notifications during migration.

The prepared tools now implement that workflow. Follow the
[production release procedure](minutes_production_release.md) for exact commands.
Use the prepared schema-2 manifest from the verified archive and actual read-only
meeting export: link only Meeting 31, preserve AGM unlinked, and leave Meeting 24
unchanged. Official Letter 6 is a separate unresolved historical file; do not
regenerate it automatically. Copy and verify PDFs and private deployment artifacts
**before** the production deploy. Then, after approval, use pre-deploy to add the schema and atomically link
verified R2 objects before new code receives traffic. This prevents the cutover
from relying on either old instance files or a gap where links still point to
ephemeral local storage. Keep uploads disabled and retain legacy references.

Resolve any non-minutes media references separately, preserving their existing
backend and availability on replacement instances. Enable new minutes uploads
only after website verification, and remove the one-time linker from pre-deploy
before enabling the flag. The release procedure includes compatible rollback and
retains every R2 document and the additive schema.

Storage writes and database commits cannot be one atomic transaction. A database
failure after an upload can leave an unreferenced private object. Reconcile those
objects from logs/manifests; do not automatically delete historical documents.

Roll back to a release that understands both attachment fields. Disable new R2
uploads if needed while preserving private reads and the additive schema. Returning
to a version that understands only legacy files would hide new R2 attachments.

## Local verification

Use a disposable PostgreSQL database and temporary file backends. Explicitly
override `DATABASE_URL`; the existing workspace environment may point remotely.
Do not run tests, `migrate`, or `collectstatic` against production for validation.

```bash
python3 -B -m unittest scripts.test_backup_render_media
```

With a separately configured local PostgreSQL test database, run the Django minutes
tests and existing portal regressions. Use a dedicated staging R2 bucket for a
later real-service upload/download, restart, and backup recovery exercise.
