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

1. Open the Demo tab and run the guided water-point scenario.
2. Review the redacted incident in the responder dashboard.
3. Check the evidence hash/custody record and resource anomaly.
4. Review the related rumor cluster for steward notes.
5. Toggle offline mode in the browser, submit another report, then go online and flush the queue.
6. Switch to coordinator role, inspect the node health/sync preview, and run sync.

See [Manual Test Checklist](docs/manual-test.md) for a fuller smoke test.

## Services

- `apps/web`: static offline-first PWA.
- `services/api`: local edge API, SQLite schema, redaction, triage, evidence, resources, rumors, and sync queue.
- `infra`: Docker Compose and EC2 deployment notes.

## Safety Boundaries

The prototype does not identify people, infer guilt, track individual movement, or expose raw report text through the dashboard API. Triage output is deterministic local assistance for human review.
