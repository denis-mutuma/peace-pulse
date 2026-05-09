# PeacePulse Hub

PeacePulse Hub is an offline-first community resilience prototype for fragile and displaced communities. It runs as a local edge hub and supports anonymous report intake, deterministic redaction, human-reviewed triage, evidence protection, resource monitoring, rumor triage, and low-bandwidth sync simulation.

## Quick Start

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`.

The app uses `uv` for Python environment management and only Python standard-library modules for the local demo. It creates a SQLite database under `data/peacepulse.db`.

## Docker

```bash
docker compose -f infra/docker-compose.yml up --build
```

The container serves the API and static PWA on port `8080`. Runtime state is written to `data/` by default, so local demo data survives container rebuilds unless that directory is removed.

## Configuration

The API supports these optional environment variables:

- `PEACEPULSE_HOST`: bind host, default `0.0.0.0`.
- `PEACEPULSE_PORT`: bind port, default `8080`.
- `PEACEPULSE_DB_PATH`: SQLite database path, default `data/peacepulse.db`.

Evidence uploads are capped at 2 MB and limited to image, audio, text, or PDF content. Synced evidence records include metadata and hashes only; encrypted local storage paths are not included in the sync preview.

## Tests

```bash
uv run python -m unittest discover -s tests
```

## Demo Flow

1. Use a guided report tile to load a low-literacy starter report.
2. Add a phone number or exact block to show the active privacy warning, then remove it.
3. Open the Demo tab and run the guided water-point scenario.
4. Review the redacted incident in the responder dashboard.
5. Check the evidence hash/custody record and resource anomaly.
6. Review the related rumor cluster for steward notes.
7. Toggle offline mode in the browser, submit another report, then go online and flush the queue.
8. Switch to coordinator role, inspect the node health/sync preview, and run sync.

See [Manual Test Checklist](docs/manual-test.md) for a fuller smoke test.

## Demo Reset

The Demo tab includes a reset action for rehearsals and presentations. It clears runtime records, removes stored evidence binaries, clears the browser-side scenario log, and reseeds the water-point scenario.

Use reset when:

- A judge or reviewer asks to see the flow from the beginning.
- The browser offline queue contains old demo submissions.
- Sync counts or incident cards are cluttered from prior rehearsals.
- You want the dashboard to return to the same predictable seeded story.

The reset is intentionally local to the prototype runtime. It does not alter source files, git history, deployment configuration, or application code.

For scripted rehearsals, the same reset is available through the local API:

```bash
curl -X POST http://localhost:8080/api/demo/reset
```

The response includes the number of seeded reports, incidents, resource events, and rumor records. After calling it, refresh the browser so every tab reloads the seeded state.

Reset safety notes:

- Use it only for demo data, not for real submissions.
- It removes local evidence binaries created during rehearsal.
- It clears queued browser submissions from the current browser session.
- It preserves the codebase and deployment settings.
- It gives every presentation the same starting state.

Recommended rehearsal order after reset:

1. Select a guided intake tile and confirm the report form fills safely.
2. Run the guided scenario.
3. Add one mediation note.
4. Open the privacy audit.
5. Run coordinator sync.
6. Confirm the sync preview is redacted.

## Guided Intake

The Report tab includes low-literacy guided tiles for the main concern types. Each tile fills the report category, a rough location, and a safe starter sentence that avoids names, phone numbers, and exact homes.

The browser also checks the report text for likely sensitive details before submission. Phone numbers, email addresses, ID-like values, exact block/unit locations, and titled names trigger a warning panel. The warning does not block reporting; it gives the community member a chance to remove identifying details while the backend still performs deterministic redaction during triage.

## Services

- `apps/web`: static offline-first PWA.
- `services/api`: local edge API, SQLite schema, redaction, triage, evidence, resources, rumors, and sync queue.
- `infra`: Docker Compose and EC2 deployment notes.

## SafeRoute API

SafeRoute stores rough route and service-point status only. Use `GET /api/routes/status` to list default service points and current alerts, and `POST /api/routes/alerts` to add a redacted route alert for caution, blocked, or service-update conditions.

Route alerts are intentionally coarse. They sync as summaries for coordinator review and do not store GPS traces, person identities, or exact movement history.
Use the main report flow for detailed unsafe-route narratives that need responder triage.

## FairWork API

FairWork stores local opportunity summaries without worker profiles or employer identity claims. Use `GET /api/work/opportunities` to list opportunities and `POST /api/work/opportunities` to add a steward-reviewed listing.

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
- Reset demo data restores seeded opportunities.

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
- Sync preview shows summaries and metadata, not raw evidence bytes.
- The app does not create accounts, track movement, or infer guilt.
- The privacy audit should be shown before any discussion of deployment.

The audit is deliberately simple: it is a demo-facing safety explanation, not a legal compliance report.
Keep production compliance review separate from this prototype.
