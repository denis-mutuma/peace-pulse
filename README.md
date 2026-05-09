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

## Configuration

The API supports these optional environment variables:

- `PEACEPULSE_HOST`: bind host, default `0.0.0.0`.
- `PEACEPULSE_PORT`: bind port, default `8080`.
- `PEACEPULSE_DB_PATH`: SQLite database path, default `data/peacepulse.db`.

## Tests

```bash
uv run python -m unittest discover -s tests
```

## Demo Flow

1. Submit an anonymous report about water-point tension.
2. Upload supporting evidence metadata to the locker.
3. Simulate resource sensor data for water-point pressure.
4. Log a related rumor for steward review.
5. Review the redacted incident in the responder dashboard.
6. Toggle offline mode in the browser, submit another report, then go online and flush the queue.
7. Switch to coordinator role and run sync.

See [Manual Test Checklist](docs/manual-test.md) for a fuller smoke test.

## Services

- `apps/web`: static offline-first PWA.
- `services/api`: local edge API, SQLite schema, redaction, triage, evidence, resources, rumors, and sync queue.
- `infra`: Docker Compose and EC2 deployment notes.

## Safety Boundaries

The prototype does not identify people, infer guilt, track individual movement, or expose raw report text through the dashboard API. Triage output is deterministic local assistance for human review.
