# Manual Test Checklist

Run the API locally:

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`, then verify:

1. Submit a valid report with a name, phone number, email, or block/unit location.
2. Confirm the dashboard shows redacted text and no raw sensitive value.
3. Change the incident status and confirm the success message appears.
4. Filter incidents by status, category, and minimum severity.
5. Submit a short invalid report and confirm it is rejected without entering the offline queue.
6. Toggle offline mode, submit a valid report, and confirm it appears in the browser queue.
7. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
8. Visit `/api/health` and confirm it reports `"database": "ok"`.
