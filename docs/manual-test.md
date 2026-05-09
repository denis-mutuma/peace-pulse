# Manual Test Checklist

Run the API locally:

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`, then verify:

1. Select each guided report tile and confirm it updates the concern type, rough location, and starter text.
2. Add a phone number, email, ID-like value, or block/unit location and confirm the active warning appears.
3. Remove the sensitive detail and confirm the warning hides.
4. Submit a valid report with a name, phone number, email, or block/unit location.
5. Change the report language and confirm the phrasebook examples and privacy warning update.
6. Submit a Swahili, French, or Arabic phrasebook example and confirm the dashboard public update uses that language.
7. Switch to steward role and confirm the dashboard shows redacted text.
8. Change the incident status and confirm the success message appears.
9. Add a mediation note with a name, phone number, or exact block and confirm the displayed note is redacted.
10. Expand the incident timeline and confirm it shows triage, status, and note events.
11. Upload an evidence file and confirm hash/custody metadata appears.
12. Simulate a resource event and confirm anomaly/resource status appears.
13. Open SafeRoute, confirm service points render, and add a route alert with a phone number or exact block.
14. Confirm the route alert is redacted and appears in the route alert list.
15. Open FairWork, confirm seeded opportunities render, and add a steward-checked opportunity.
16. Confirm the exploitation button prepares a work exploitation report without submitting identity data.
17. Log a rumor and confirm the redacted cluster appears.
18. Reopen the timeline and confirm related resource/rumor context appears for the scenario.
19. Open the Demo tab, run all four water-point scenario steps, and confirm the scenario log records each step.
20. Click Reset demo data, confirm the scenario log clears, and confirm seeded dashboard records return.
21. Switch to steward role and confirm the guided scenario data appears across Dashboard, Evidence, Resources, and Rumors.
22. Filter incidents by status, category, and minimum severity.
23. Submit a short invalid report and confirm it is rejected without entering the offline queue.
24. Toggle offline mode, submit a valid report, and confirm it appears in the browser queue.
25. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
26. Switch to coordinator role, run sync, and confirm pending counts clear.
27. Open the Privacy tab and confirm it shows record counts plus local-only, sync, and never-sync policies.
28. Confirm the privacy audit states raw evidence bytes and local evidence paths never sync.
29. Confirm the coordinator sync view shows hub health, database status, queue counts, latest resource status, and last sync time.
30. Confirm the sync preview shows route and opportunity summaries but no exact movement history or raw report text.
31. Visit `/api/health` and confirm it reports `"database": "ok"`.
