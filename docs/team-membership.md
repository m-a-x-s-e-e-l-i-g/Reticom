# Approved team membership

Joining a team requests access; it no longer grants it. A join code locates the
team and is not a password or approval token.

## Approving a teammate

1. The teammate joins using the code or QR code.
2. On the original owner's device, open **Team membership** in the Command team
   card or Field **Admin Settings**.
3. Compare the complete device identity with **User Settings → My device identity**
   on the teammate's device, through a trusted channel. Callsigns are self-reported.
4. Select **Approve**. The joining device checks its status periodically, normally
   within 15 seconds while connected. Its queued team transmissions then retry.

Pending devices can use local maps and keep local work queued. They cannot read
the remote mission/feed, tasks, recordings, transcripts or private mailboxes, or
send team events, live audio or live headings. Discovery metadata and the signed
host directory remain available to locate the team and request admission.

**Reject** denies a pending request. **Revoke** denies an approved device's future
network access and closes its existing links on that host. Rejoining does not
reset either decision. The owner can explicitly approve that identity again.
"Everyone is admin" grants application editing rights to approved members; it
does not grant membership-management rights.

## Upgrading existing teams

Update **all clients and hosts, including backup hosts**. Previously seen devices
appear as candidates but are **not automatically approved**. They need a one-time
owner approval. Older clients may not display pending status correctly; older
hosts do not enforce this policy and must not remain active.

Existing data and pending local messages are retained. Revocation cannot erase
previously downloaded data, revoke a copied identity key, or stop a legacy host.
Provider API credentials are still local; this change does not share them.

## Enforcement and replication

The original team identity signs a versioned membership allowlist, pinned to the
team's Reticulum destination. Receivers enforce it independently of the UI for
each application request and live frame. Legacy signed packets also require
approval. Current clients use acknowledged Link requests for events: a packet
proof alone does not establish that the application accepted a message.

The policy lives in `membership/<team-root>/policy.json`, separate from ordinary
replicated settings. Pending requests are bounded and stored separately. Only
the original owner's private identity can sign approval/rejection/revocation;
forged, wrong-team and conflicting policies are rejected, and older revisions
cannot overwrite newer decisions.

Approved backup hosts exchange the signed policy before regular data sync. A new
replica does not serve application requests until it has a policy and completed
initial synchronization. Previously approved members can continue through a
backup when the owner disappears, but new approvals require the owner to return.
Replication is asynchronous: a disconnected backup cannot know about a new
revocation until it reconnects. It keeps enforcing its last signed policy.

Backup-host authorization is separate and grants full trust in the stored team
data. Revoking a person's Field identity does **not** remove an independently
approved backup-host identity; do not approve an untrusted device as a host.

The web API is a trusted **local device control plane**, not a remotely
authenticated service. Keep it loopback-bound/private; do not expose HTTP or
WebSocket ports to the Internet or untrusted LAN clients. Membership approval
endpoints additionally require loopback access and reject cross-origin browser
requests. This is not a substitute for securing the rest of the local API.

## Tests

```powershell
.venv/Scripts/python.exe -m pytest tests/test_membership.py tests/test_command_teams.py -q
$env:RETICOM_NETWORK_TESTS = '1'
.venv/Scripts/python.exe -m pytest tests/test_membership_network.py tests/test_continuity_network.py -q
```

Network tests use isolated identities and localhost TCP interfaces with real
encrypted Reticulum Links. They exercise pending denial, queued delivery after
approval, revocation, persistence and backup-host synchronization.
