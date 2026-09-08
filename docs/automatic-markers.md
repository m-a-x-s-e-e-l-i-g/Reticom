# Automatic message markers

Text messages and ready voice transcripts share the same marker parser in Field,
Command and All Teams. Examples:

- `heat signature spotted 200 meters north of your position`
- `rally 100m north`
- `rally 100 m north`
- `rally 0.1 clicks north`
- `rally half a klick north`
- `rally at church`

Distances accept m, meter/metre, km, kilometer/kilometre, click and klick, including
plural forms, spaces and decimals. One click/klick means 1,000 metres. Explicit
distances require a compass direction and must be between 1 and 5,000 metres.
The reference fix is the reporter's shared position at the message time, no more
than ten minutes old; the viewer's changing position does not move a shared marker.
For landmark commands without a recent sender fix, the latest recent position from
the same team is used instead. Distance commands still require the sender fix.
Phrases such as “your position” do not select a different team member.

`at`, `by` and `near` can identify a landmark, with optional `the`, `a` or `nearest`.
Church, school, hospital, bridge, station, police station and fire station are
looked up by OpenStreetMap category; other names use an exact case-insensitive
name match. The nearest returned location within 5 km is selected. Ways and
relations use their mapped bounding-box center, so the point is approximate.

Landmark lookup requires Internet access and sends only the landmark query and
reference coordinates to the public Overpass service, not message text or identity.
Results are cached for the open page. Failed or empty results retry after one minute;
the original message remains in the feed, with no invented pin at the reporter's
position. The map redraws as soon as lookup completes.

Query syntax: [OpenStreetMap Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL).
