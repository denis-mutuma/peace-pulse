# Manual Test Checklist

Run the API locally:

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`, then verify:

1. Submit a valid report with a name, phone number, email, or block/unit location.
2. Switch to steward role and confirm the dashboard shows redacted text.
3. Change the incident status and confirm the success message appears.
4. Add a mediation note with a name, phone number, or exact block and confirm the displayed note is redacted.
5. Expand the incident timeline and confirm it shows triage, status, and note events.
6. Upload an evidence file and confirm hash/custody metadata appears.
7. Simulate a resource event and confirm anomaly/resource status appears.
8. Log a rumor and confirm the redacted cluster appears.
9. Reopen the timeline and confirm related resource/rumor context appears for the scenario.
10. Open the Demo tab, run all four water-point scenario steps, and confirm the scenario log records each step.
11. Switch to steward role and confirm the guided scenario data appears across Dashboard, Evidence, Resources, and Rumors.
12. Filter incidents by status, category, and minimum severity.
13. Submit a short invalid report and confirm it is rejected without entering the offline queue.
14. Toggle offline mode, submit a valid report, and confirm it appears in the browser queue.
15. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
16. Switch to coordinator role, run sync, and confirm pending counts clear.
17. Confirm the coordinator sync view shows hub health, database status, queue counts, latest resource status, and last sync time.
18. Confirm the sync preview shows redacted payload summaries and does not show raw report text or local evidence paths.
19. Visit `/api/health` and confirm it reports `"database": "ok"`.
