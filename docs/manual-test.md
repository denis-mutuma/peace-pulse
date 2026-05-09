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
5. Upload an evidence file and confirm hash/custody metadata appears.
6. Simulate a resource event and confirm anomaly/resource status appears.
7. Log a rumor and confirm the redacted cluster appears.
8. Open the Demo tab, run all four water-point scenario steps, and confirm the scenario log records each step.
9. Switch to steward role and confirm the guided scenario data appears across Dashboard, Evidence, Resources, and Rumors.
10. Filter incidents by status, category, and minimum severity.
11. Submit a short invalid report and confirm it is rejected without entering the offline queue.
12. Toggle offline mode, submit a valid report, and confirm it appears in the browser queue.
13. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
14. Switch to coordinator role, run sync, and confirm pending counts clear.
15. Confirm the coordinator sync view shows hub health, database status, queue counts, latest resource status, and last sync time.
16. Confirm the sync preview shows redacted payload summaries and does not show raw report text or local evidence paths.
17. Visit `/api/health` and confirm it reports `"database": "ok"`.
