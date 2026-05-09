# PeacePulse Hub

PeacePulse Hub is an offline-first anonymous reporting prototype for fragile and displaced communities. It runs as a local edge hub and supports report intake, deterministic redaction, human-reviewed triage, and a responder dashboard.

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
2. Review the redacted incident in the responder dashboard.
3. Update the incident status as a responder.
4. Toggle offline mode in the browser, submit another report, then go online and flush the queue.

See [Manual Test Checklist](docs/manual-test.md) for a fuller smoke test.

## Services

- `apps/web`: static offline-first PWA.
- `services/api`: local edge API, SQLite schema, redaction, and triage.
- `infra`: Docker Compose for the local API container.

## Safety Boundaries

The prototype does not identify people, infer guilt, track individual movement, or expose raw report text through the dashboard API. Triage output is deterministic local assistance for human review.
