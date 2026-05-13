# Manual Test Checklist

Run the API locally:

```bash
uv run python -m services.api_prod.main
```

Open `http://localhost:8080`, then verify:

1. Select each guided report tile and confirm it updates the concern type, rough location, and starter text.
2. Add a phone number, email, ID-like value, or block/unit location and confirm the active warning appears.
3. Remove the sensitive detail and confirm the warning hides.
4. Sign in as staff, attach a short audio file to a report, and confirm the success message says voice-note metadata was stored.
5. Open Evidence and confirm the voice note appears as metadata with a linked report id.
6. Submit a valid report with a name, phone number, email, or block/unit location.
7. Change the report language and confirm the phrasebook examples and privacy warning update.
8. Submit a Swahili, French, or Arabic phrasebook example and confirm the dashboard public update uses that language.
9. Sign in with a steward-capable account and confirm the dashboard shows redacted text.
10. Change the incident status and confirm the success message appears.
11. Add a mediation note with a name, phone number, or exact block and confirm the displayed note is redacted.
12. Expand the incident timeline and confirm it shows triage, status, note, and linked voice evidence events.
13. Open Copilot, investigate the selected incident, and confirm the response includes hypotheses, recommended actions, verification checks, and runbook citations.
14. Start a Copilot chat session, ask for next responder steps, and confirm the assistant cites local runbooks without showing raw report text.
15. Upload an evidence file and confirm hash/custody metadata appears.
16. Simulate a resource event and confirm anomaly/resource status appears.
17. Open SafeRoute, confirm service points render, and add a route alert with a phone number or exact block.
18. Confirm the route alert is redacted and appears in the route alert list.
19. Open FairWork, confirm seeded opportunities render, and add a steward-checked opportunity.
20. Confirm the exploitation button prepares a work exploitation report without submitting identity data.
21. Log a rumor and confirm the redacted cluster appears.
22. Reopen the timeline and confirm related resource/rumor context appears for the scenario.
23. Open the Demo tab, run all four water-point scenario steps, and confirm the scenario log records each step.
24. Click Clear demo state and confirm only the browser-local scenario log and queued reports clear.
25. Confirm signed-in staff can see the guided scenario data across Dashboard, Evidence, Resources, Rumors, and Copilot.
26. Filter incidents by status, category, and minimum severity.
27. Submit a short invalid report and confirm it is rejected without entering the offline queue.
28. Toggle offline mode, submit a valid report with a voice note, and confirm only the text report enters the browser queue.
29. Toggle online mode, flush the queue, and confirm the accepted/rejected/still queued counts.
30. Sign in with a coordinator-capable account, run sync, and confirm pending counts clear.
31. Open the Privacy tab and confirm it shows record counts plus local-only, sync, and never-sync policies, including Copilot session counts.
32. Confirm the privacy audit states raw evidence bytes, local evidence paths, and Copilot chat transcripts never sync.
33. Confirm the coordinator sync view shows hub health, database status, queue counts, latest resource status, and last sync time.
34. Confirm the sync preview shows route, opportunity, and voice-note metadata summaries but no exact movement history, raw report text, or Copilot chat text.
35. Visit `/api/v1/health` and confirm it reports `"database": "ok"`.
