# PeacePulse Hub

PeacePulse Hub is an offline-first community resilience platform for fragile and displaced communities. It runs as a local edge hub and supports anonymous reports, human-reviewed triage, evidence protection, resource monitoring, rumor triage, and low-bandwidth sync.

## Quick Start

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`.

The app uses `uv` for Python environment management and only Python standard-library modules for the local demo. It creates a SQLite database under `data/peacepulse.db` and evidence files under `data/storage/evidence`.

## Docker

```bash
docker compose -f infra/docker-compose.yml up --build
```

## Tests

```bash
uv run python -m unittest discover -s tests
```

## Demo Flow

1. Submit an anonymous report about water-point tension.
2. Submit a related rumor.
3. Upload an evidence file.
4. Generate simulated resource sensor events.
5. Review the incident queue and clusters in the responder dashboard.
6. Toggle offline mode in the PWA, submit another report, then go online and flush the queue.
7. Run sync from the coordinator panel and confirm summaries are marked synced.

## Services

- `apps/web`: static offline-first PWA.
- `services/api`: local edge API, SQLite schema, triage, evidence hashing, sync queue.
- `services/worker`: background triage loop for queued reports.
- `services/sensor-sim`: simulated water-point sensor event producer.
- `services/sync`: one-shot sync runner.
- `infra`: Docker Compose and deployment notes.

## Safety Boundaries

The prototype does not identify people, infer guilt, track individual movement, or decide whether a rumor is true. AI-like outputs are deterministic local assistance for human review.
