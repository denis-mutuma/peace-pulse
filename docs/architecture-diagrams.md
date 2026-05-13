# PeacePulse Architecture Diagrams

These diagrams are meant for quick explanation in reviews, demos, and the five-minute pitch. They describe the current implementation, not the older background ideation document.

## System Architecture

```mermaid
flowchart LR
    community[Community member<br/>Anonymous report form]
    staff[Staff and coordinators<br/>PWA dashboard]
    browser[(Browser storage<br/>offline report queue)]
    api[FastAPI edge hub<br/>/api/v1]
    db[(SQLite WAL<br/>tenant and workflow data)]
    localEvidence[(Encrypted local<br/>evidence fallback)]
    s3[(S3-compatible storage<br/>presigned PUT uploads)]
    coordinator[Remote coordinator<br/>signed sync batches]

    community --> staff
    community -->|text report| api
    community -->|offline text report| browser
    browser -->|flush when online| api
    staff -->|staff tools| api
    api --> db
    api -->|local upload path| localEvidence
    api -->|presigned upload target| staff
    staff -->|direct browser PUT| s3
    api -->|metadata and hashes only| db
    api -->|signed privacy-safe batch| coordinator
```

## Privacy-safe Data Flow

```mermaid
sequenceDiagram
    participant Reporter as Reporter
    participant Browser as PWA
    participant Hub as Edge Hub API
    participant DB as SQLite
    participant Evidence as Evidence Store
    participant Sync as Sync Preview
    participant Remote as Remote Coordinator

    Reporter->>Browser: Submit concern without identity
    Browser->>Hub: Text report
    Hub->>Hub: Redact names, phones, exact locations
    Hub->>DB: Store redacted report and incident
    Hub->>DB: Purge raw report text after triage
    Browser->>Hub: Optional evidence metadata
    Hub-->>Browser: Local upload URL or presigned S3 PUT URL
    Browser->>Evidence: Upload evidence bytes
    Hub->>DB: Store metadata, hash, consent flag
    Hub->>Sync: Build metadata-only sync records
    Sync-->>Remote: Push signed batch when configured
    Note over Sync,Remote: No raw report text, raw evidence bytes, local paths, or Copilot chat transcripts
```

## Five-minute Demo Flow

```mermaid
flowchart TD
    hook[Start with the risk:<br/>people need to report safely]
    intake[Guided anonymous report<br/>with sensitive-detail warning]
    triage[Redacted incident<br/>severity and category]
    evidence[Evidence locker<br/>hash and metadata only]
    response[Responder dashboard<br/>status, notes, timeline]
    copilot[Runbook-grounded Copilot<br/>local citations]
    sync[Coordinator sync<br/>remote push or local fallback]
    privacy[Privacy audit<br/>what stays local, syncs, never syncs]
    close[Close:<br/>privacy as infrastructure]

    hook --> intake --> triage --> evidence --> response --> copilot --> sync --> privacy --> close
```
