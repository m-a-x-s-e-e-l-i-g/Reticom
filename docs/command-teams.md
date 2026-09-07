# Teams in Command

Open **Teams** to view saved teams, join by an `RTM1` code, or choose a recently announced team under **Discovered teams**. Discovery is based on Reticulum announces received by this device, not a worldwide team directory. Refresh discovery or use a code when a team is absent.

Creating a team hosts it here. Joining an existing team creates an isolated member identity with callsign `COMMAND`; it does not grant host or administrator privileges. Joined teams retain the laptop Command layout and synchronize mission data in the background, including while another team or All Teams is open. Permissions remain enforced by the remote host. An offline host leaves cached data available and outgoing messages queued.

**Stop hosting** pauses a locally hosted team. **Pause sync** stops this Command's connection to a joined team. Neither action erases history. **Open team** selects an active team in the current browser tab; each tab keeps its own team scope.

**Remove** has an explicit confirmation. It stops this device's team host/member connection and removes the entry from normal lists and All Teams. It does not broadcast a team deletion, revoke members, or delete their copies. The local files, identity and membership are retained. **Removed teams → Restore** returns the entry in a stopped state. Joining the same code again restores/reopens the saved identity rather than creating a duplicate.

Workspace metadata lives in `command-teams.json`. Legacy entries without `mode` remain hosts; joined entries have `mode: "member"`. `removed_at` marks recoverable removal, including the legacy `default` team whose files remain at the data-directory root. No team directory is recursively deleted or moved. Starting a removed entry is rejected until it is restored.

Tests: `tests/test_command_teams.py` covers persistence, isolation, stop/start, removal/restore and offline membership. Set `RETICOM_NETWORK_TESTS=1` to run `tests/test_command_join_network.py` against isolated real Reticulum TCP nodes. It checks background mission synchronization, message delivery, duplicate joins and removal without affecting the remote host.
