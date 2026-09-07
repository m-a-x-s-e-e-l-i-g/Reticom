# Command canvas

Command now uses the map as the workspace. The default floating Comms panel
contains the actual intel feed, PTT and text composer. Existing controls and
event handlers are moved, not cloned; transport, membership and team permissions
are unchanged. This layout applies to hosted and joined Command teams, not Field
or the separate All Teams view.

- Open Operators, enabled Tasks and Map layers from the bottom dock.
- Drag a panel by its header. Keyboard users can focus its header and use arrows.
- Close panels with X; the dock reopens them. Map only hides panels temporarily
  and restores their previous visibility when switched off.
- Panel positions and visibility are stored locally under
  `reticom-command-workspace-v1`. Reset layout restores the defaults. Layers
  reopen closed after a page reload, without changing enabled intel packs.
- Team selection, join/QR and team management are in the top team picker.
  Administration remains in the shield menu; member review is also available
  in Operators for admins.
- Event signatures and packet details are expandable inside Comms. Map message
  overlays are hidden in Command to avoid duplicating that feed.
- Window resizing clamps panels inside the workspace and resizes the map;
  opening, closing or moving panels does not change the map camera.

Implementation: `static/command-workspace.js` and `command-workspace.css`,
initialized from `renderTeam` in `app.js`. No new network endpoints.

Focused checks: `node --test tests/command-workspace.test.mjs`.
