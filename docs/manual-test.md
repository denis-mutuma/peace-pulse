# Manual Test Checklist

Run the API locally:

```bash
uv run python services/api/server.py
```

Open `http://localhost:8080`, then verify:

1. Select each guided report tile and confirm it updates the concern type, rough location, and starter text.
2. Add a phone number, email, ID-like value, or block/unit location and confirm the active warning appears.
3. Remove the sensitive detail and confirm the warning hides.
4. Attach a short audio file to a report and confirm the success message says the voice note was linked.
5. Open Evidence and confirm the voice note appears as metadata with a linked report id.
6. Submit a valid report with a name, phone number, email, or block/unit location.
7. Change the report language and confirm the phrasebook examples and privacy warning update.
8. Submit a Swahili, French, or Arabic phrasebook example and confirm the dashboard public update uses that language.
9. Switch to steward role and confirm the dashboard shows redacted text.
10. Change the incident status and confirm the success message appears.
11. Add a mediation note with a name, phone number, or exact block and confirm the displayed note is redacted.
12. Expand the incident timeline and confirm it shows triage, status, note, and linked voice evidence events.
13. Upload an evidence file and confirm hash/custody metadata appears.
14. Simulate a resource event and confirm anomaly/resource status appears.
15. Open SafeRoute, confirm service points render, and add a route alert with a phone number or exact block.
16. Confirm the route alert is redacted and appears in the route alert list.
17. Open FairWork, confirm seeded opportunities render, and add a steward-checked opportunity.
18. Confirm the exploitation button prepares a work exploitation report without submitting identity data.
19. Log a rumor and confirm the redacted cluster appears.
20. Reopen the timeline and confirm related resource/rumor context appears for the scenario.
21. Open the Demo tab, run all four water-point scenario steps, and confirm the scenario log records each step.
22. Click Reset demo data, confirm the scenario log clears, and confirm seeded dashboard records return.
23. Switch to steward role and confirm the guided scenario data appears across Dashboard, Evidence, Resources, and Rumors.
24. Filter incidents by status, category, and minimum severity.
25. Submit a short invalid report and confirm it is rejected without entering the offline queue.
26. Toggle offline mode, submit a valid report with a voice note, and confirm only the text report enters the browser queue.
27. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
28. Switch to coordinator role, run sync, and confirm pending counts clear.
29. Open the Privacy tab and confirm it shows record counts plus local-only, sync, and never-sync policies.
30. Confirm the privacy audit states raw evidence bytes and local evidence paths never sync.
31. Confirm the coordinator sync view shows hub health, database status, queue counts, latest resource status, and last sync time.
32. Confirm the sync preview shows route, opportunity, and voice-note metadata summaries but no exact movement history or raw report text.
33. Visit `/api/health` and confirm it reports `"database": "ok"`.
