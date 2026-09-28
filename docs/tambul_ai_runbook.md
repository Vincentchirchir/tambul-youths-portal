# Tambul Assistant Runbook

Tambul Assistant is a read-only assistant for authenticated portal users. It may answer using only server-side Tambul tools that enforce the same role policy as the portal. It must not create, approve, update, send, notify, or remember anything.

## Operating Guarantees

- PostgreSQL via `DATABASE_URL` remains the application database. SQLite files are repository artifacts only and must not be used for production or normal operation.
- OpenAI credentials stay in server-side environment variables. Browser code posts only a `message` to `/ai/chat/`.
- The Responses API request is created with `store=False` and `parallel_tool_calls=False`.
- `OPENAI_MODEL` is configurable by environment and is not exposed to end users.
- Automated tests mock OpenAI. Do not put paid provider calls in the test suite.
- Audit logs store metadata only: request id, user, role, tool name, sensitivity, authorization result, success/failure, error category, provider response id, token counts, duration, and timestamp.

## Configuration

Set these variables in the deployment environment, not in committed files:

- `AI_ENABLED`: `1` only in approved development/staging/production rollout windows.
- `OPENAI_API_KEY`: server-side OpenAI key.
- `OPENAI_MODEL`: Responses-compatible model selected by operations.
- `AI_MAX_INPUT_LENGTH`: maximum browser message length.
- `AI_MAX_OUTPUT_TOKENS`: output cap for each provider response.
- `AI_MAX_TOOL_CALLS`: maximum deterministic Tambul tool calls per request.
- `AI_REQUEST_TIMEOUT`: provider timeout in seconds.
- `AI_RATE_LIMIT`: requests allowed per user per window.
- `AI_RATE_LIMIT_WINDOW_SECONDS`: rate-limit window size.
- `AI_PILOT_ROLES`: comma-separated roles eligible for AI pilot access. Default: `admin,chairperson,treasurer`.
- `AI_PILOT_USER_IDS`: optional comma-separated pilot allowlist by user id.
- `AI_PILOT_USERNAMES`: optional comma-separated pilot allowlist by username.

See `docs/tambul_ai_limited_pilot.md` for the Phase 4D pilot procedure.

## Enable Or Disable

Disable immediately by setting `AI_ENABLED=0` and redeploying/restarting the app process.

Before enabling, confirm:

- `DATABASE_URL` points to PostgreSQL.
- `OPENAI_API_KEY` and `OPENAI_MODEL` are present only in the server environment.
- `.env`, SQLite databases, local virtualenvs, caches, logs, and editor files are ignored by Git.
- Phase 4 automated validation passes.
- A synthetic staging smoke test has passed.

## Monitoring

Use Django Admin `AIAuditLog` for a simple operating view:

- Filter by `created_at`, `success`, `error_category`, `user_role`, `tool_name`, `sensitivity`, and `authorization_result`.
- Review request volume, failed requests, denied tools, provider errors, total tokens, and high durations.
- Estimate cost by summing `input_tokens` and `output_tokens` over a time window and applying the current price for the configured `OPENAI_MODEL`.
- Do not add prompt or answer content to the audit model.

For a compact metadata-only CLI view, run:

```bash
python manage.py ai_pilot_report --days 7
python manage.py ai_pilot_report --days 7 --role treasurer
```

## Incident Response

For suspected data exposure:

1. Set `AI_ENABLED=0`.
2. Preserve `AIAuditLog` rows and application logs for the incident window.
3. Identify affected `request_id` values, user roles, tools, sensitivity classes, and authorization results.
4. Verify whether deterministic tools allowed or denied the data access.
5. If credentials may have been exposed outside the server environment, rotate the OpenAI key.
6. Fix the permission/tool issue, add a regression test, and re-run Phase 4 validation before re-enabling.

For permission mistakes:

- Disable AI if the issue can expose private data.
- Fix central role policy or the tool registration, not the prompt alone.
- Verify the multi-role matrix in `docs/tambul_ai_staging_validation.md`.

For cost spikes:

- Lower `AI_RATE_LIMIT`, `AI_MAX_OUTPUT_TOKENS`, or disable AI.
- Inspect `AIAuditLog` for request counts, token totals, high-latency requests, and repeated failures.
- Confirm no automated job or browser loop is calling `/ai/chat/`.

For provider failures:

- Check `OPENAI_API_KEY`, `OPENAI_MODEL`, provider availability, and timeout settings.
- User-facing failures must remain controlled `503`, `504`, or `429` responses and must not break non-AI portal pages.

## Rollout

Recommended state after Phase 4: keep AI disabled in production until the real-provider smoke test passes in staging with synthetic data. Then enable for a narrow role/user group using the Phase 4D pilot gate, review audit logs daily during the pilot, and only broaden access after no permission, cost, or failure-mode issues appear.
