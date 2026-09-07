# Mission catch-up

Field's existing foreground and Android background feed polls request a paged
snapshot automatically. Connection details exposes **Sync now** to restart a
catch-up without leaving the team or clearing local work.

## Contents and limits

- All visible markers and drawings, after resolving authorized deletion and
  report-status events. Map objects are not subject to the recent-intel limit.
- Latest profile per operator, up to 20 position events per operator, and the
  latest 100 other intel events. This is current mission state, not a full archive.
- Current tasks when enabled, team name/modules/admin mode, and only the requesting
  identity's recent private messages. Private content is never shared team-wide.
- Audio references travel in events; audio bytes and transcripts use the existing
  on-demand endpoints. Offline map tiles are separate.

Command's `/api/state` also keeps all active map objects; its `limit` controls
recent intel, not the number of map objects. Field's SQLite and browser caches no
longer truncate the mission back to the last 60 events.

## Protocol

An identified `/feed` request containing `mission` opts into snapshot paging;
legacy empty requests retain the old feed response. Each page contains an offset,
cursor, total, team metadata and typed records. A SHA-256 digest identifies the
entire snapshot and is checked before applying it. Integrity/authentication of
the transport comes from the existing Reticulum Link and trusted-host model;
the digest is not an additional signature or host-blind encryption scheme.

Pages target 12 KB of records (one unusually large object can take up to 48 KB).
The current safety limit is 20,000 records / 8 MB per snapshot. Oversized missions
fail explicitly rather than silently truncating markers. A page gets a 20-second
request budget. When multiple hosts are approved, a failed attempt makes the
next poll try another candidate rather than blocking through every host at once.

Host snapshots are immutable for up to ten minutes, scoped to the requesting
identity, with at most 16 snapshots cached. Field stages pages and its cursor in
SQLite, surviving process restarts. An expired or evicted host snapshot restarts
the download without discarding the last complete mission. An unchanged digest
avoids downloading the same snapshot again. Changes currently trigger a new
snapshot, not per-object delta synchronization.

Applying a complete snapshot is transactional. Absent verified cache rows are
pruned, but queued rows present when the download started and newer local rows
are preserved. This reconciles old deletions/cleared history without erasing
unsent work. Host history is not deleted by catch-up. Partial downloads never
replace the displayed mission. Status includes progress and last successful sync;
a transport connection by itself is not reported as a completed mission sync.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests/test_mission.py -q
$env:RETICOM_NETWORK_TESTS='1'
.venv\Scripts\python.exe -m pytest tests/test_mission_network.py -q -s
```

The real-network test starts an isolated TCP carrier, Command and a late-joining
Field identity. It creates 70 waypoints followed by 85 newer position events,
then verifies full catch-up, task/module transfer and subsequent marker removal.
Unit tests also cover interrupted/restarted downloads, checksum rejection,
identity-scoped snapshots, queued/concurrent work and report-status projection.
