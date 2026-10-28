# Meeting minutes production release

## Readiness and scope

The operator-confirmed two-PDF backup and private R2 connection probe passed.
Do not repeat them for this release. Production deployment, schema changes,
attachment linking, and enabling uploads remain subject to approval.
`MINUTES_R2_UPLOADS_ENABLED=0` stays in effect throughout preparation.

The production reference audit and database-only preflight passed without writes.
The operator subsequently confirmed that both private R2 PDF copies, the genuine
copy receipt, and the R2-backed production dry run passed. That dry run proposed
only Meeting 31, left Meeting 24 unchanged, and retained AGM unlinked using a
server-enforced read-only database connection. These stages are complete; do not
repeat the copy, connection probe, backup verification, or dry run for preparation.
Migration `0024`, linking, and production deployment remain pending approval.

| Item | Confirmed finding | Release treatment |
| --- | --- | --- |
| Meeting 31, October 2026 | Private R2 copy and receipt verified, 83,652 bytes; audience `all_members` | Later link only its four new attachment fields |
| AGM PDF | Private R2 copy and receipt verified, 27,345 bytes; no matching meeting reference | Remain under its separate private unlinked key; no meeting or audience association |
| Meeting 24, Annual Report 2025 | Record and original filename reference exist; operator confirmed file missing on inspected instance | Explicit unresolved exception; preserve row, audience, and filename; never substitute AGM |
| Official Letter 6 | Record and generated filename exist; operator confirmed file missing on inspected instance | Separate unresolved historical PDF; no generation, overwrite, or record changes |

The database audit found **zero** references in profile photos, uploaded signatures,
uploaded stamps, and letter supporting attachments. The only non-minutes file
reference is Letter 6. This completes the reference audit for the application's
current file/image fields; it does not establish that unreferenced files or files
on other instances do not exist. The verified archive retains all bytes it contains.
Missing historical references are acknowledged recovery issues, not prerequisites
for rewriting or blocking the independent Meeting 31 migration. Newly introduced
or changed files/references must still be reconciled before cutover.

## Protected release artifacts

Source backup, unchanged:

```text
/home/vince/tambul-private-backups/tambul-media-fdc8w-20261025
```

Prepared private artifacts on this workstation:

```text
tmp/minutes-release-20261026/
  production-reference-audit.json
  reconciliation-report.json
  records.json
  manifest.json
  snapshot-preflight.json
  database-preflight.json
  copy-receipt.json
  r2-backed-dry-run.json
```

This directory is ignored by Git and restricted to its owner; JSON artifacts have
mode `0600`. Retain a protected copy outside the checkout before deployment or
cleanup. Never commit the backup, record export, manifest, or receipt.

The exact reviewed migration ID is:

```text
c5a094aafb5c4989bc6ef6989beca5a7
```

The schema-2 manifest records actual IDs, audiences, creation timestamps, poster
IDs, filenames, sizes, and source SHA-256 values from the read-only export and
verified inventory. Do not regenerate it on retry: a new manifest has new keys.
Its destinations are:

```text
minutes/migrated/c5a094aafb5c4989bc6ef6989beca5a7/31/TAMBUL_HUSTLE_YOUTH_GROUP_OCT_2026_MINUTES.pdf
minutes/unlinked/c5a094aafb5c4989bc6ef6989beca5a7/AGM_MINUTES_TAMBUL_HUSTLE_YOUTH_GROUP..pdf
```

Both original basenames, case, and the AGM filename's two periods are preserved.
Only the first object has a meeting ID. The AGM object has `disposition=unlinked`
metadata, no `meeting-id`, and no dashboard/download association. Meeting 24 has
no R2 destination. Letter 6 is tracked separately, outside the minutes manifest.

The completed production database-only preflight reports one proposed update
(Meeting 31), one unlinked document, and unresolved Meeting 24 unchanged. It also
confirms migration `0024` is not yet applied. The subsequent operator-confirmed
R2-backed dry run additionally verified both PDF objects and the genuine receipt.

## Copy both backup PDFs to private R2

**Completed.** The commands in this section are retained for the release record,
not as instructions to repeat the successful copy.

Run locally from the reviewed repository in a private terminal. This command does
not initialize Django, use production database credentials, deploy, or enable
application uploads. It creates new private R2 objects only:

```bash
tambulyouthgroup_env/bin/python -B scripts/migrate_minutes_backup.py copy \
  --backup /home/vince/tambul-private-backups/tambul-media-fdc8w-20261025 \
  --manifest tmp/minutes-release-20261026/manifest.json \
  --output tmp/minutes-release-20261026/copy-receipt.json \
  --prompt
```

Enter the same six reviewed settings using the hidden prompts, keeping
`MINUTES_R2_UPLOADS_ENABLED=0`. Do not paste credentials into chat, command arguments,
or a committed file. Keep the bucket's public development URL and custom public
domains disabled, as verified for the passed connection probe. Private response
headers alone do not enforce bucket privacy.

The copy checks the selected source bytes against the existing verified inventory
and manifest, validates PDF structure, conditionally creates each object using
`If-None-Match: *`, and verifies retrieved size, SHA-256, ownership metadata, PDF
content type, and private cache headers. It does not redo the general connection
probe or full backup verification. Existing matching objects are verified, never
replaced; conflicts stop the operation. No source or R2 document is deleted.

After both documents verify, it stages these private R2 artifacts and creates the
local `copy-receipt.json`:

```text
_migrations/c5a094aafb5c4989bc6ef6989beca5a7/manifest.json
_migrations/c5a094aafb5c4989bc6ef6989beca5a7/receipt.json
```

Expect two verified PDFs, one linked candidate, one unlinked document, and one
unresolved meeting in the receipt. A failed or ambiguous copy retains all objects;
retry using the same manifest and an unused receipt output path. If a genuine
receipt already exists, retain it rather than creating a second migration.
Do not stage inputs as Render secret files or rely on the retiring instance's
filesystem. Pre-deploy obtains its reviewed inputs directly from private R2.

## Dry-run linking and release validation

**Completed.** Both preparation dry runs passed. The commands remain available for
the release record; deployed-service acceptance checks below are a separate step.

The following database-only command is available before or after `0024` if a
fresh preflight becomes necessary. It has already passed against production for
this manifest; do not rerun it merely to repeat the completed investigation:

```bash
python manage.py link_minutes_r2 \
  --manifest tmp/minutes-release-20261026/manifest.json --database-only
```

It rejects `--apply`, accesses no storage, and uses a read-only transaction.
After the real copy, run the full default dry run using candidate code and securely
supplied production settings (flag `0`):

```bash
python manage.py link_minutes_r2 --migration-id c5a094aafb5c4989bc6ef6989beca5a7
```

It reads the private manifest and authentic receipt from R2, checks both PDFs'
bytes, and verifies current record identities, exact legacy references, and
audiences. It supports the old schema for planning. Only `--apply` requires the
new columns. Additional unaccounted legacy references, changed identities, a
changed unresolved exception, conflicting private attachments, or invalid R2 bytes
stop linking. Only Meeting 31 is eligible for update; Meeting 24 is never locked
for modification or updated. No model save signals or notifications are emitted.

Use a separate staging database and bucket for actual portal workflow tests,
including both meeting audiences, both dashboards, meeting details, historical
notification links, missing attachments, storage outages, and unchanged media
features. The production migration tools intentionally restrict their target to
`tambul-portal-media`; their transaction/copy behavior is tested with isolated
fixtures. Do not point staging uploads at the production bucket to bypass this.

Keep a current PostgreSQL backup, the reviewed candidate commit, original media
backup, exact manifest, genuine receipt, and a compatible rollback release.
Use the confirmed web service settings and configuration-change sequence below.
The repository's `render.yaml` describes cron services rather than this web service.

## Confirmed web service settings

The operator inspected these live settings:

| Setting | Confirmed value |
| --- | --- |
| Repository | `Vincentchirchir/tambul-youths-portal` |
| Branch | `main` |
| Root Directory | Blank, using the repository root |
| Auto-Deploy | Off |
| Current Pre-Deploy Command | Empty |
| Start Command, retained exactly | `daphne -b 0.0.0.0 -p $PORT tambulyouthgroup_portal.asgi:application` |
| Current Build Command, to replace | `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate` |

The approved target **Build Command** is:

```bash
pip install -r requirements.txt && python manage.py collectstatic --noinput
```

The one-time target **Pre-Deploy Command** is:

```bash
python manage.py check --deploy --fail-level WARNING && python manage.py check_minutes_release && python manage.py migrate --noinput && python manage.py link_minutes_r2 --migration-id c5a094aafb5c4989bc6ef6989beca5a7 --apply
```

Migrations run only in pre-deploy. Keep the existing Daphne Start Command exactly,
including the literal `$PORT` and `:application` suffix. Keep branch, root directory,
Auto-Deploy Off, credentials, and all six minutes variables unchanged, with
`MINUTES_R2_UPLOADS_ENABLED=0`.

## Save configuration without deploying

Auto-Deploy Off prevents Git-triggered automatic releases. It is not a guarantee
that a Dashboard command save will avoid a deployment. Treat Build and Pre-Deploy
command saves as potentially deployment-triggering; do not infer command-save
behavior from the Environment page's separate Save only option. Render documents
that service configuration changes can trigger deployments regardless of build
filters: [configuration changes](https://render.com/docs/monorepo-support#faq).

For a configuration-only change, use Render's **Update Service API**. It explicitly
documents that updates do not deploy automatically, irrespective of auto-deploy
settings; deployment requires a separate action:
[Update Service](https://api-docs.render.com/reference/update-service).

After approval to save settings, include both command changes in **one** request
to `PATCH https://api.render.com/v1/services/<WEB_SERVICE_ID>`. Obtain the actual
web service ID from its Dashboard URL; do not target a cron service. The payload
is saved in `docs/render_minutes_release_settings.json` and contains only two
command fields, without secrets:

```json
{
  "serviceDetails": {
    "envSpecificDetails": {
      "buildCommand": "pip install -r requirements.txt && python manage.py collectstatic --noinput"
    },
    "preDeployCommand": "python manage.py check --deploy --fail-level WARNING && python manage.py check_minutes_release && python manage.py migrate --noinput && python manage.py link_minutes_r2 --migration-id c5a094aafb5c4989bc6ef6989beca5a7 --apply"
  }
}
```

The nested fields are defined in the
[Render API schema](https://api-docs.render.com/openapi/render-public-api-1.json).
Use a private authenticated API client. Keep the Render API token out of command
arguments, shell history, files, and logs; it is distinct from the R2 credentials.
Do not dump the complete API response because it can contain unrelated service
details. No environment-variable update, start-command change, auto-deploy change,
or deploy request is part of this settings request. Auto-Deploy Off and the upload
flag `0` are preconditions to verify before and after the update. Omitted fields
remain unchanged under Render's
[PATCH semantics](https://api-docs.render.com/reference/requests).

The exact order is:

1. Keep Auto-Deploy Off, hold independent CI/deploy-hook triggers, and confirm no
   queued or running deployment. Retain the existing live deployment ID and SHA.
2. Use the source-only Git staging procedure below. Resolve the already-tracked
   AGM document exception before pushing. After review, commit and push the
   candidate to `main` with automatic deploys still off; retain its full SHA.
   Use `[skip render]` as an additional commit guard.
3. After settings-change approval, save both commands with the configuration-only
   API request above. Do not save Build and Pre-Deploy separately in the Dashboard.
4. Read back Settings: Build has no migration, Pre-Deploy has the full reviewed
   sequence, Daphne Start is unchanged, root remains blank, branch remains `main`,
   Auto-Deploy remains Off, and the upload flag remains `0`. Confirm the live
   deployment ID has not changed and no deployment was queued.
5. Stop for deployment approval. Only then manually deploy the exact candidate SHA
   as described below. Do not send a deploy request while saving settings.

If using the Dashboard instead, do not save command changes during preparation
unless its actual UI explicitly guarantees a non-deploying save for both fields.
If it does not, use the documented configuration-only API path. An intermediate
deployment with the new Build Command and an empty Pre-Deploy Command could start
new application code before its schema exists. Installing the new Pre-Deploy
Command against old code can fail because that code lacks the new commands.

### Verify saved configuration and deployment identity

These are instructions for the approved settings change, not API calls to make
during preparation. Before PATCH, use the private API client to read:

```text
GET https://api.render.com/v1/services/<WEB_SERVICE_ID>
GET https://api.render.com/v1/services/<WEB_SERVICE_ID>/deploys?status=live&limit=100
GET https://api.render.com/v1/services/<WEB_SERVICE_ID>/deploys?limit=100
```

From the service response, retain only the Build, Pre-Deploy, and Start commands,
branch, root directory, and auto-deploy setting. From the deploy responses, retain
the live deployment ID(s), commit SHA(s), and statuses; confirm no deployment is
queued or in progress. Use IDs with status `live`, rather than assuming the newest
deploy is live. Paginate if necessary. Do not log full responses, credentials, or
deploy-hook URLs. The read endpoints are documented in
[Retrieve Service](https://api-docs.render.com/reference/retrieve-service) and
[List Deploys](https://api-docs.render.com/reference/list-deploys).

After the single settings PATCH, repeat those GET requests. Verify:

| Saved value | Required result |
| --- | --- |
| `serviceDetails.envSpecificDetails.buildCommand` | Exactly the revised Build Command above |
| `serviceDetails.envSpecificDetails.preDeployCommand` | Exactly the reviewed one-time Pre-Deploy Command above |
| `serviceDetails.envSpecificDetails.startCommand` | Exactly `daphne -b 0.0.0.0 -p $PORT tambulyouthgroup_portal.asgi:application` |
| Auto-deploy setting | Still Off (`autoDeployTrigger=off`, or legacy `autoDeploy=no`) |
| Branch and root directory | Still `main` and blank |
| Live deploy ID(s) and SHA(s) | Identical to the pre-PATCH snapshot |
| Deployment activity | No new deployment triggered or queued |

For native services, the GET response returns Pre-Deploy inside
`serviceDetails.envSpecificDetails`; the PATCH request instead accepts it at
`serviceDetails.preDeployCommand`. Preserve that distinction when comparing the
saved response with the request payload.

Verify `MINUTES_R2_UPLOADS_ENABLED=0` in the Environment page without editing or
printing other variables. Its value is not part of the service PATCH payload.
Stop on any discrepancy; do not issue a deploy request or automatic corrective
PATCH. No saved-setting/live-deployment verification has been executed locally.

### Git staging without private artifacts

`docs/minutes_release_git_paths.txt` is an explicit list of reviewed source,
tests, and documentation paths. It includes this runbook and the non-secret API
payload. It excludes `.env`, credential directories, uploaded media, archives,
the verified backup outside the repository, and everything under `tmp/`, including
the manifest, receipt, audit, and private migration reports. `.env.example` is a
public settings template, not the credential-bearing `.env` file.

The next single execution step is a local preview only:

```bash
git add --dry-run --pathspec-from-file=docs/minutes_release_git_paths.txt
```

After the preview is reviewed, actual staging can use the same explicit list:

```bash
git add --pathspec-from-file=docs/minutes_release_git_paths.txt
git diff --cached --name-only
git diff --cached --stat
```

Inspect the entire staged list, including anything staged earlier. Do not use
force-add or a broad workspace staging command. No staging, commit, or push is
performed by these documentation changes.

**Existing tracked-document exception:**
`media/minutes/AGM_MINUTES_TAMBUL_HUSTLE_YOUTH_GROUP..pdf` is already tracked in
the current Git commit. The `/media/` ignore rule does not remove existing tracked
files or historical copies. The explicit staging list does not add or modify it,
but a later commit still inherits the tracked document unless separately resolved.
Review this before pushing; repository visibility has not been established here.
No file has been removed, no index entry changed, and no history rewritten. Any
decision to stop tracking the PDF must preserve the local original and verified
backups; removing it from a future commit alone does not erase Git history.

## Compatibility and Letter 6 recovery

| Feature | Behavior retained by this release |
| --- | --- |
| Profile photographs | Existing filesystem field, paths, and views |
| Signatures and stamps | Existing protected filesystem paths and renderer; static fallback assets unchanged |
| Official letters and supporting attachments | Existing filesystem fields, permissions, generation, audit, and verification |
| Meeting notifications | Existing audience recipients; stable detail links delivered after commit |
| Other notifications and attachments | Existing storage and behavior |
| Static files | Existing effective static backend and WhiteNoise middleware |

No global storage switch or public media-directory route is introduced. Default
filesystem uploads remain vulnerable to Render instance replacement. Future photo,
letter, signature, stamp, or other local uploads need a separately approved durable
storage arrangement. Do not promise that this minutes-only release fixes them.
Coordinate a hold on legacy/media writes during cutover; the old release does not
understand the new minutes upload flag. Any newly uploaded local bytes must be
preserved before an instance replacement.

Letter 6's generated filename stays
`committee_letters/generated/THYG_COM_2026_001_Q0N7LEP.pdf`. The existing renderer
can produce bytes from stored recipient/body/reference/date, verification code,
and signatory text (`core/services/committee_letters.py`,
`generate_committee_letter_pdf`). It also uses current status, current related
signature/stamp assets or static fallbacks, current logo/contact/layout settings,
and ReportLab generation metadata/encryption. The stored row is not a complete
historical rendering snapshot; byte-identical recovery is not established.

A separately approved recovery can first export the saved data read-only, verify
status and audit history, and render an isolated preview without calling a model
or file save. An authorized reviewer must compare the content and artwork before
approving any restoration at the preserved filename. Prefer an original matching
PDF if it can be located. No preview or recovery for Letter 6 has been executed.

**Do not open Letter 6's production PDF URL as a read-only test.**
`CommitteeLetterPDFView.get()` can invoke `save_official_pdf()` for an approver
when status is approved. That helper writes a new file, saves the field, and adds
an audit row. A nonempty filename whose file is missing is otherwise opened
without an existence check, which can cause an existing download failure. This
legacy behavior remains a separate issue; the R2 migration does not invoke it or
regenerate any letter. Check letter list/detail/preview flows with staging data;
keep historical recovery and any PDF-view hardening in a separately reviewed task.

## Approved deployment sequence

Nothing below authorizes production changes. The R2 copy receipt/full dry run,
pending migration review, controlled cutover, and explicit approval are required.
Missing historical Meeting 24 and Letter 6 remain documented exceptions, not
reasons to substitute files or expand the minutes migration.

1. Retain the completed copy/dry-run artifacts, database backup, and candidate
   release. Account for changes since the audit and
   coordinate a temporary hold on legacy/media writes. Confirm the real web
   service supports pre-deploy and complete the settings-only sequence above.
2. Keep all six saved R2 variables and `MINUTES_R2_UPLOADS_ENABLED=0`. Review
   `python manage.py migrate --plan` with candidate code before approval.
   `core.0024_meetingnote_private_pdf` adds four columns only; it neither copies
   files nor rewrites existing references, audiences, or letter records.
3. After approval, use a fail-fast pre-deploy command with the exact reviewed ID:

   ```bash
   python manage.py check --deploy --fail-level WARNING &&
   python manage.py check_minutes_release &&
   python manage.py migrate --noinput &&
   python manage.py link_minutes_r2 --migration-id c5a094aafb5c4989bc6ef6989beca5a7 --apply
   ```

   This approved step writes the additive schema and only Meeting 31's four new
   fields: `minutes_pdf`, `minutes_original_name`, `minutes_sha256`, `minutes_size`.
   Keep `file`, ID, author, dates, audience, title, and content unchanged. No
   notifications are resent. The single linking transaction rolls back on failure.
   Schema changes may already be committed if a later step fails; retain them.
4. Trigger Manual Deploy > Deploy a specific commit using the approved candidate
   SHA, the revised Build Command, and the unchanged Daphne Start Command.
   Pre-deploy needs PostgreSQL and private R2 only, so it does not depend on old
   media surviving. New code must not receive traffic before `0024` and linking
   complete; dashboards select the new fields. Keep uploads disabled.
5. In the new service, run `python manage.py check_minutes_release`. Verify the
   real October PDF's authorized download, size/hash, original filename, both
   dashboards, detail, old notification/media-minutes links, and anonymous access
   enforcement. Confirm Meeting 24 remains a controlled missing-file response,
   Letter 6's record/reference stays unchanged, and AGM has no portal association.
   Test committee-only access with approved staging fixtures; neither real meeting
   should have its audience changed for testing. Check unaffected portal workflows
   without triggering Letter 6 generation or downloading its mutation-prone URL.
6. After website verification, remove the one-time linker from pre-deploy before
   enabling uploads. A retained linker intentionally refuses uploads-enabled mode.
   The normal reviewed Pre-Deploy Command can be:

   ```bash
   python manage.py check --deploy --fail-level WARNING && python manage.py migrate --noinput
   ```

   Keep the Build Command free of migrations permanently. Replace the one-time
   pre-deploy command through the configuration-only API path as well; do not
   assume a Dashboard command save is inert. This release stops with the flag at
   `0`; the following activation step requires separate approval.

   Only after separate approval set `MINUTES_R2_UPLOADS_ENABLED=1` using Render's
   controlled save/deploy action. Saved-only values do not alter running processes.
   Run `python manage.py check_minutes_release --expected-uploads enabled`, then
   verify an approved real Secretary upload and permission checks; do not invent
   a production meeting as a smoke test.
7. Confirm persistence during a later planned deployment/restart and monitor
   controlled 404/503 responses. Retain independent PDF/object and database backups.
   Do not configure automatic deletion of historical or unlinked objects.

If this service cannot use pre-deploy, approve a separate release runner with
candidate code and securely supplied settings before the web rollout. Do not
improvise schema changes in the build command or deploy schema-dependent code
first. No step needs to retrieve files from the old Render instance.

## Rollback preserving R2 documents

1. Disable uploads (`0`) through an approved deployment, retaining private reads.
   Use the normal pre-deploy command rather than the one-time linker.
2. Roll back/forward to a reviewed release that understands private R2 reads, the
   four new fields, audiences, and authenticated links. Retain credentials, bucket,
   schema, linked values, all R2 PDFs, private artifacts, and the source backup.
   Do not reverse `0024`, clear attachment fields, delete the AGM object, or run
   historical recovery automatically. Meeting 24 and Letter 6 remain unchanged.
3. Verify Meeting 31 and all later R2 uploads before ending the response. AGM
   stays private and unlinked. A pre-R2 code rollback cannot serve R2-only uploads;
   it is not the compatible rollback target and cannot rely on old instance files.
4. Prefer code rollback to a database restore. If database recovery is unavoidable,
   retain the current attachment mapping first and reconcile later objects against
   database backups and metadata. Preserve R2 bytes even if an older DB snapshot
   lacks their references. Never delete objects to match an older database state.

## Verification evidence

The completed database-only production preflight is saved privately in
`database-preflight.json`: one link proposed, Meeting 24 unchanged, AGM unlinked,
old schema detected, no database writes. The non-minutes audit is complete as
reported above; filesystem absence for historical files is operator-confirmed
on the inspected instance, not inferred from this SQL audit.

The focused minutes/migration suite passed **63 tests**, including schema-2
reconciliation, preserving an unlinked document, guarded Meeting 24 exception,
unchanged rows/notifications, idempotency, pre-schema dry runs, and failure cases.
The updated complete Django suite passed **215 tests in 270.127 seconds**, using
four local workers. It includes the existing portal, payment, official-letter,
notification, and assistant regressions plus the minutes and migration tests.
Production-mode `check --deploy --fail-level WARNING`, the credential-safe
`check_minutes_release`, `makemigrations --check --dry-run`, and `git diff --check`
passed. Production-mode checks use isolated synthetic settings; they are not a
claim that saved-only Render settings are already active. Additive migration SQL,
dependency checks, and isolated static collection passed during earlier
preparation. All automated tests use disposable local PostgreSQL and isolated
media, with production settings/credentials excluded.
