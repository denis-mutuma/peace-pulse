# PeacePulse Hub

PeacePulse Hub is an offline-first community resilience prototype for fragile and displaced communities. It runs as a local edge hub and supports anonymous report intake, deterministic redaction, human-reviewed triage, evidence protection, resource monitoring, rumor triage, runbook-grounded Copilot assistance, and low-bandwidth sync simulation.

## Quick Start

```bash
uv run python -m services.api_prod.main
```

Open `http://localhost:8080`.

The production API uses FastAPI, SQLAlchemy, and SQLite in WAL mode for a low-cost pilot deployment. It creates a SQLite database under `data/peacepulse-prod.db` by default.

## Docker

```bash
docker compose -f infra/docker-compose.yml up --build
```

The container serves the API and static PWA on port `8080`. Runtime state is written to `data/` by default, so local runtime records survive container rebuilds unless that directory is removed.

## Configuration

The production API supports these optional environment variables:

- `PEACEPULSE_ENV`: runtime environment, default `development`.
- `PEACEPULSE_DATABASE_URL`: SQLAlchemy database URL, default `sqlite:///data/peacepulse-prod.db`.
- `PEACEPULSE_JWT_SECRET`: signing secret for staff access tokens. Change this before deployment.
- `PEACEPULSE_BOOTSTRAP_TOKEN`: required in production for first-tenant bootstrap.
- `PEACEPULSE_EVIDENCE_STORAGE_DIR`: local fallback evidence object directory.
- `PEACEPULSE_S3_ENDPOINT_URL` and `PEACEPULSE_S3_BUCKET`: optional S3-compatible evidence object storage.

Bootstrap the first production tenant after starting the API:

```bash
curl -X POST http://localhost:8080/api/v1/admin/bootstrap \
  -H 'content-type: application/json' \
  -H "X-Bootstrap-Token: $PEACEPULSE_BOOTSTRAP_TOKEN" \
  -d '{"organization_name":"Demo Org","site_name":"North Site","admin_email":"admin@example.org","admin_password":"REPLACE_WITH_LONG_PASSWORD","admin_name":"Admin"}'
```

The browser also exposes this bootstrap flow in the Production Access panel. After bootstrapping, sign in with the admin account, enroll MFA from the access panel, and verify a code from an authenticator app. Staff views use `/api/v1` with server-enforced roles.

Evidence uploads are capped at 2 MB and limited to image, audio, text, or PDF content. Synced evidence records include metadata and hashes only; encrypted local storage paths are not included in the sync preview.

## Tests

```bash
uv run python -m unittest discover -s tests
```

The suite includes standard-library API tests plus headless browser smoke tests for the PWA. Browser tests launch Chromium when `chromium`, `chromium-browser`, or `google-chrome` is available; otherwise they skip with a clear unittest message.

## Demo Flow

1. Use a guided report tile to load a low-literacy starter report.
2. Add a phone number or exact block to show the active privacy warning, then remove it.
3. Sign in as staff and attach an optional short audio note to show linked evidence metadata.
4. Open the Demo tab and run the guided water-point scenario.
5. Review the redacted incident in the responder dashboard.
6. Check the evidence hash/custody record and resource anomaly.
7. Review the related rumor cluster for steward notes.
8. Toggle offline mode in the browser, submit another report, then go online and flush the queue.
9. Sign in with coordinator access, inspect the node health/sync preview, and run sync.

See [Manual Test Checklist](docs/manual-test.md) for a fuller smoke test.

## Guided Intake

The Report tab includes low-literacy guided tiles for the main concern types. Each tile fills the report category, a rough location, and a safe starter sentence that avoids names, phone numbers, and exact homes.

The browser also checks the report text for likely sensitive details before submission. Phone numbers, email addresses, ID-like values, exact block/unit locations, and titled names trigger a warning panel. The warning does not block reporting; it gives the community member a chance to remove identifying details while the backend still performs deterministic redaction during triage.

Reports can include optional audio-note metadata when staff are signed in. The browser validates the file type and size, hashes the bytes, creates a linked evidence metadata record, and syncs metadata only when allowed. Offline browser queueing stores text reports only, so voice notes should be added while the hub is reachable and staff access is active.

Voice-note boundaries:

- Keep recordings short and focused on the concern.
- Avoid speaking names, phone numbers, or exact shelters.
- Treat voice bytes as local-only material, not coordinator sync content.

## Services

- `apps/web`: static offline-first PWA.
- `services/api_prod`: production FastAPI app, SQLAlchemy schema, auth, redaction, triage, evidence, resources, routes, rumors, Copilot, and sync preview.
- `infra`: Docker Compose and EC2 deployment notes.

## Copilot

PeacePulse Copilot adds a local, runbook-grounded assistant for staff review. It can investigate an incident, return conservative hypotheses, recommend next actions, show an agent trace, and cite the local runbooks used for grounding.

Use `GET /api/v1/copilot/runbooks` to list seeded and organization runbooks, `POST /api/v1/copilot/incidents/{incident_id}/investigate` to generate an investigation packet, and `/api/v1/copilot/sessions` plus `/api/v1/copilot/sessions/{session_id}/messages` for persisted staff chat.

Copilot uses redacted incident summaries and runbook text only. Chat transcripts remain local to the hub, do not enter coordinator sync preview, and should not be used to collect names, exact shelters, phone numbers, or raw evidence.

## SafeRoute API

SafeRoute stores rough route and service-point status only. Use `GET /api/v1/routes/status` to list default service points and current alerts, and `POST /api/v1/routes/alerts` to add a redacted route alert for caution, blocked, or service-update conditions.

Route alerts are intentionally coarse. They sync as summaries for coordinator review and do not store GPS traces, person identities, or exact movement history.
Use the main report flow for detailed unsafe-route narratives that need responder triage.

## FairWork API

FairWork stores local opportunity summaries without worker profiles or employer identity claims. Use `GET /api/v1/work/opportunities` to list opportunities and `POST /api/v1/work/opportunities` to add a steward-reviewed listing.

Only steward-checked opportunity summaries sync. Exploitation concerns should use the existing anonymous report flow with the `work_exploitation` concern type.
This keeps opportunity coordination separate from identity-based hiring or payroll systems.
Listings should remain short, role-based, and locally reviewed.

## FairWork Demo

The FairWork tab shows dignified local opportunity summaries and a shortcut for reporting exploitation concerns. Use it near the end of the demo to show how the same hub can reduce conflict pressure through safe coordination without collecting worker identities.

Suggested demo flow:

1. Add a steward-checked opportunity with a rough location.
2. Show that it appears as a short opportunity card.
3. Use Report exploitation to prefill the anonymous report form.
4. Submit only a non-identifying concern if further review is needed.
5. Inspect coordinator sync to confirm only summary fields appear.

What to avoid:

- Do not enter worker names, phone numbers, or identity document details.
- Do not present listings as vetted employment contracts.
- Do not collect payment details or wage disputes in the opportunity card.
- Use anonymous reporting for safety concerns instead of naming an employer.

During judging, describe FairWork as an extension module: it demonstrates that the same local-first foundation can support opportunity coordination after the safety workflow is proven.

Quick FairWork checks:

- Steward-checked listings appear on the board.
- Unverified listings are visually marked for caution.
- Safety notes are redacted before display and sync preview.
- Exploitation reports return to anonymous intake.
- No person profile is created during the flow.
- Clearing demo state removes only browser-local demo logs and queued reports.

Presentation prompts:

- "What can someone do safely near them?"
- "Who checked this opportunity?"
- "What should not be stored?"
- "Where does an exploitation concern go?"

## Safety Boundaries

The prototype does not identify people, infer guilt, track individual movement, or expose raw report text through the dashboard API. Triage output is deterministic local assistance for human review.

## Privacy Audit

Steward and coordinator roles include a Privacy tab that explains the local-first data policy in the running demo. It shows current record counts, what stays local, what may sync as a redacted summary, and what never syncs.

Use this view during demos to make the safety boundary explicit before showing the coordinator sync preview. The intended message is that PeacePulse supports human review and low-bandwidth coordination without becoming an identity, surveillance, or raw-evidence export system.

Demo talking points:

- Reports are anonymous and dashboard text is redacted.
- Evidence files remain encrypted in local edge storage.
- Copilot cites local runbooks and works from redacted incident context.
- Sync preview shows summaries and metadata, not raw evidence bytes.
- The app does not create accounts, track movement, or infer guilt.
- The privacy audit should be shown before any discussion of deployment.

The audit is deliberately simple: it is a demo-facing safety explanation, not a legal compliance report.
Keep production compliance review separate from this prototype.
