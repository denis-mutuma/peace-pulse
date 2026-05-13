# AGENTS.md

## Project Shape
PeacePulse Hub is an offline-first community resilience prototype. The codebase is split into:

- `services/api_prod`: FastAPI production API, SQLAlchemy models, auth, privacy, redaction, triage, evidence, resources, routes, rumors, Copilot, and sync preview.
- `apps/web`: static PWA frontend that talks to `/api/v1`.
- `infra`: Docker Compose, EC2 deployment notes, and SQLite backup script.
- `tests`: API tests plus Chromium-driven browser smoke tests.

## Current Ground Truth
- Anonymous reports are triaged into redacted incidents.
- Evidence uploads are metadata-first; raw bytes stay local and are encrypted on disk when the local fallback is active, or are uploaded directly to S3 through a presigned PUT when S3 storage is configured.
- Copilot is deterministic retrieval over seed and org runbooks, not an external LLM call.
- Sync preview stays privacy-safe locally, and `Run sync` now pushes signed batches to a configured remote coordinator endpoint when one is present.
- Voice-note report submission and the browser smoke flow are currently working and covered by tests.

## Working Rules
- Prefer `rg` and `rg --files` for discovery.
- Use `apply_patch` for file edits.
- Do not revert user changes or unrelated work.
- Keep changes narrow and behavior-driven.
- If you touch the browser UI, verify the Chromium smoke tests as well as the API suite.

## Verification
Run these after meaningful changes:

```bash
uv run python -m unittest discover -s tests
```

The browser suite expects Chromium to be available and may skip if it is not installed.

## Implementation Notes
- The app is designed around local-first privacy boundaries. Do not add code that leaks raw evidence bytes, unredacted report text, exact movement history, or Copilot chat transcripts into sync preview.
- The `/api/v1/health` response now reports the active evidence storage mode. Use it when checking whether the deployment is in local fallback or S3-backed upload mode.
- Remote sync uses the existing batch contract and should keep the same privacy filters in both outbound and inbound directions.
- The report flow expects the report row to exist before linked incident creation. Keep that ordering intact.
- If you change the frontend submit flow, keep the report result stable across form resets and refreshes.
- Keep commits small and focused when working in this repo.

## Handy Endpoints
- `GET /api/v1/health`
- `POST /api/v1/admin/bootstrap`
- `POST /api/v1/auth/login`
- `POST /api/v1/public/sites/{site_id}/reports`
- `GET /api/v1/evidence`
- `GET /api/v1/sync/preview`
- `GET /api/v1/privacy/audit`
