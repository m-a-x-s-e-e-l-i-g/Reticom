# Team continuity (experimental)

An approved Field device can hold a full replica of a team and serve it through
its own Reticulum destination when the original host is unavailable. The join
code and logical team identity do not change. A public transport node is still
only a carrier: at least one approved application host must be reachable.

## Set up a backup

1. Update the original host and participating Field apps to this build.
2. Join the same team on the backup device. In **User settings → Team continuity**,
   choose **Use this device as a backup** and copy its public key.
3. On the original host, open **Team continuity**, enter that public key and a
   device name, and explicitly confirm the trust warning. Compare the displayed
   host fingerprint with the backup device. Never exchange private identity files.
4. Keep both devices connected until **Initial sync complete** appears. The first
   sync includes the full retained database history, tasks, settings, private
   mailboxes and recorded audio—not merely the latest visible feed page.
5. Allow other Field apps to receive the signed host list before going offline.
   **Hand over to this host** changes the preferred host after a fresh sync
   acknowledgment. It leaves the original host running as another replica.

If the preferred host fails, updated clients try another approved, initialized
host. A backup resumes from its saved replica after its own restart. When hosts
reconnect, their journaled changes merge automatically. A phone used as backup
must keep the Reticom service running; Android force-stop or OS termination still
takes that host offline. This feature does not bypass Android battery management.

## Guarantees and limits

- Only the original team owner's signature can authorize another host or change
  the preferred host. Owner identity is pinned to the destination in the original
  join code. Policy rollback and conflicting same-revision policies are rejected.
- Each host has its own private key. The owner's key is **not cloned** or sent to
  backups. Approval grants trusted-host/admin capabilities, including access to
  private mailboxes; it does not provide host-excluding end-to-end encryption.
- Replication uses identified encrypted Reticulum Links. Unapproved identities
  cannot request replication pages or audio. No HTTP shortcut connects replicas.
- SQLite triggers journal inserts, updates and deletes in the application's own
  transaction. Durable cursors allow catch-up beyond UI limits and after restart.
- Concurrent row edits converge using Lamport counter + host destination ordering,
  not wall-clock timestamps. Deletions win for an ID; recreating an object requires
  a new ID. There is no single ordered, consensus-backed global transaction log.
- Replication is **asynchronous**. A transport delivery receipt does not mean all
  replicas have the event. Changes not yet copied can remain unavailable while
  their originating device is offline; permanent loss of that device can lose
  those changes. Handover checks a recent copy acknowledgment, but does not freeze
  all concurrent sends or promise zero-loss shutdown.
- The initial-sync flag means the first copy was complete, not that a host is
  currently online or fully caught up. Check the last synchronization time.
- Recorded audio is copied. Live PTT streams are not bridged between simultaneous
  hosts and can be interrupted at failover; archived audio subsequently syncs.
- Cold joining with only a legacy join code while the owner is unavailable is
  not supported unless the client already cached the approved host list.
- The original owner retains authority to approve hosts. This is preferred-host
  handover, **not ownership-key transfer**. Owner loss does not prevent existing
  ready replicas serving the team, but prevents approving additional hosts.
- Up to four hosts are supported in this first version. Journals and deletion
  records are retained indefinitely; compaction, host revocation, storage quotas,
  low-bandwidth scheduling and an independent security audit remain future work.
- Maps, user settings, TTS voices and transcription caches are device-local.
  Replication covers application data, not a complete device backup.

## Developer verification

Unit tests exercise policy pinning/forgery/rollback, access control, pagination,
restart cursors, concurrent edits, remove-wins semantics and transaction rollback.

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_replication.py
$env:RETICOM_NETWORK_TESTS = '1'
.venv\Scripts\python.exe -m pytest -q tests/test_continuity_network.py -s
```

The opt-in integration test starts a separate TCP Reticulum carrier, Command, a
Field backup and another Field client on temporary ports/directories. It approves
the backup, tests signed handover, kills the preferred Command process (and verifies
HTTP is unreachable), sends through the backup, checks byte-identical Ogg audio,
private messages, markers and task changes, restarts the backup while Command is
absent, then restores Command and checks convergence. Optional transcription is
disabled in this test; transport and persisted events are real, not mocked.

The original single-host format remains readable. Replication metadata is additive
inside each host database plus `continuity/`. Do not copy a replicated database
under a different host identity: origin checks intentionally reject this.
