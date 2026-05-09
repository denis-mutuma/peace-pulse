# Manual Test Checklist

Run the API locally:

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`, then verify:

1. Submit a valid report with a name, phone number, email, or block/unit location.
2. Change the report language and confirm the phrasebook examples and privacy warning update.
3. Submit a Swahili, French, or Arabic phrasebook example and confirm the dashboard public update uses that language.
4. Switch to steward role and confirm the dashboard shows redacted text.
5. Change the incident status and confirm the success message appears.
6. Add a mediation note with a name, phone number, or exact block and confirm the displayed note is redacted.
7. Expand the incident timeline and confirm it shows triage, status, and note events.
8. Upload an evidence file and confirm hash/custody metadata appears.
9. Simulate a resource event and confirm anomaly/resource status appears.
10. Log a rumor and confirm the redacted cluster appears.
11. Reopen the timeline and confirm related resource/rumor context appears for the scenario.
12. Open the Demo tab, run all four water-point scenario steps, and confirm the scenario log records each step.
13. Click Reset demo data, confirm the scenario log clears, and confirm seeded dashboard records return.
14. Switch to steward role and confirm the guided scenario data appears across Dashboard, Evidence, Resources, and Rumors.
15. Filter incidents by status, category, and minimum severity.
16. Submit a short invalid report and confirm it is rejected without entering the offline queue.
17. Toggle offline mode, submit a valid report, and confirm it appears in the browser queue.
18. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
19. Switch to coordinator role, run sync, and confirm pending counts clear.
20. Open the Privacy tab and confirm it shows record counts plus local-only, sync, and never-sync policies.
21. Confirm the privacy audit states raw evidence bytes and local evidence paths never sync.
22. Confirm the coordinator sync view shows hub health, database status, queue counts, latest resource status, and last sync time.
23. Confirm the sync preview shows redacted payload summaries and does not show raw report text or local evidence paths.
24. Visit `/api/health` and confirm it reports `"database": "ok"`.
