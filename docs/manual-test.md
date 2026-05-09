# Manual Test Checklist

Run the API locally:

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`, then verify:

1. Submit a valid report with a name, phone number, email, or block/unit location.
2. Switch to steward role and confirm the dashboard shows redacted text.
3. Change the incident status and confirm the success message appears.
4. Upload an evidence file and confirm hash/custody metadata appears.
5. Simulate a resource event and confirm anomaly/resource status appears.
6. Log a rumor and confirm the redacted cluster appears.
7. Filter incidents by status, category, and minimum severity.
8. Submit a short invalid report and confirm it is rejected without entering the offline queue.
9. Toggle offline mode, submit a valid report, and confirm it appears in the browser queue.
10. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
11. Switch to coordinator role, run sync, and confirm pending counts clear.
12. Visit `/api/health` and confirm it reports `"database": "ok"`.
