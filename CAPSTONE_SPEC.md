# PeacePulse Capstone Spec

## Overview
PeacePulse Hub is a local edge-hub prototype for fragile and displaced communities. It supports anonymous intake, deterministic redaction, human-reviewed triage, evidence metadata handling, resource and rumor monitoring, a runbook-grounded Copilot experience, low-bandwidth sync simulation, and optional S3-backed evidence uploads.

The current implementation is intentionally local-first. It is not a hardened production surveillance system and does not attempt identity tracking, guilt inference, or exact movement history.

## Product Modules

- Anonymous Report Intake
  - Guided report tiles prefill low-literacy starter reports.
  - The browser warns on likely sensitive details.
  - The backend redacts report text deterministically and creates an incident record.

- Evidence Locker
  - Staff can create evidence upload metadata and upload bytes to a local edge path, or receive a presigned S3 upload target when S3 storage is configured.
  - Content is hashed, validated, and encrypted at rest.
  - Sync preview contains metadata and hashes only.

- Incidents and Triage
  - Staff can list incidents, change status, add notes, and inspect a timeline.
  - Notes and incident output are redacted.
  - Timeline combines triage, notes, evidence, resource anomalies, and rumor context.

- Copilot
  - Retrieval is deterministic over seed and organization runbooks.
  - It returns hypotheses, recommended actions, verification checks, citations, and an agent trace.
  - Organization runbooks can be created and updated by authorized staff.

- SafeRoute
  - Stores rough route/service-point status and coarse route alerts.
  - Avoids exact travel traces or person-level tracking.

- FairWork
  - Stores steward-reviewed opportunity summaries.
  - Keeps worker identity, payroll, and employer-claim handling out of scope.

- Privacy Audit and Sync
  - Privacy audit summarizes local-only, syncable, and never-sync fields.
  - Sync preview and sync history remain local safety views, while `Run sync` can push signed batches to a configured remote coordinator.
  - Sync rejects payloads with raw-only or clearly unredacted fields.

## Architecture

- Frontend:
  - Static PWA in `apps/web`.
  - Uses browser storage for offline queueing and session state.
  - Talks to the API at `/api/v1`.

- Backend:
  - FastAPI app in `services/api_prod`.
  - SQLAlchemy models with SQLite as the default pilot datastore.
  - JWT-based staff auth and optional MFA enrollment.

- Deployment:
  - Docker Compose for local or demo deployment.
  - EC2 notes and SQLite backup script for the low-cost pilot setup.

## Key Behaviors

- Reports are anonymous and redacted before becoming incidents.
- Evidence bytes are kept local and encrypted.
- Remote sync uses the same privacy-safe batch shape as local preview and must never expose raw evidence bytes, local evidence paths, unredacted report text, or Copilot chat transcripts.
- The browser can queue text reports offline, but voice-note metadata should only be attached when the hub is reachable.
- Production bootstrap requires a bootstrap token in production mode.

## Important API Surfaces

- `GET /api/v1/health`
- `POST /api/v1/admin/bootstrap`
- `POST /api/v1/auth/login`
- `POST /api/v1/auth/mfa/enroll`
- `POST /api/v1/public/sites/{site_id}/reports`
- `GET /api/v1/incidents`
- `GET /api/v1/evidence`
- `GET /api/v1/resources/status`
- `GET /api/v1/routes/status`
- `GET /api/v1/work/opportunities`
- `GET /api/v1/copilot/runbooks`
- `POST /api/v1/copilot/incidents/{incident_id}/investigate`
- `GET /api/v1/sync/preview`
- `GET /api/v1/privacy/audit`

## Test Coverage

The current test suite covers:

- bootstrap, login, MFA, password change, and auth guardrails
- redaction and incident triage
- evidence upload validation and storage behavior
- rumor, route, work, and resource modules
- Copilot retrieval, citations, and chat sessions
- privacy audit and sync preview behavior
- Chromium browser smoke coverage for the PWA

Run:

```bash
uv run python -m unittest discover -s tests
```

## Current Status Notes

- The core end-to-end flows are implemented and green in the current test suite.
- The project still behaves as a capstone/demo system, not a fully hardened production platform.
- Remaining production gaps are mostly around external integrations and operational hardening rather than the sync transport itself.
