function waypointNumber(label) {
  const match = String(label || "").trim().match(/^waypoint[\s-]+(\d+)$/i);
  return match ? Number(match[1]) : null;
}

export function withDisplayWaypointLabels(events) {
  const used = new Set(
    events
      .filter((event) => event.type === "marker.created" && event.marker_type === "waypoint")
      .map((event) => waypointNumber(event.label))
      .filter((number) => number !== null),
  );
  const generated = new Map();
  let next = 1;
  [...events]
    .filter((event) => event.type === "marker.created" && event.marker_type === "waypoint"
      && String(event.label || "").trim().toLowerCase() === "waypoint")
    .sort((a, b) => Number(a.network?.received_at || a.created_at || 0) - Number(b.network?.received_at || b.created_at || 0))
    .forEach((event) => {
      while (used.has(next)) next += 1;
      generated.set(event.id, `Waypoint ${next}`);
      used.add(next);
      next += 1;
    });
  return events.map((event) => generated.has(event.id) ? {...event, display_label: generated.get(event.id)} : event);
}

export function nextWaypointLabel(events) {
  let highestNumber = 0;
  withDisplayWaypointLabels(events).forEach((event) => {
    if (event.type !== "marker.created" || event.marker_type !== "waypoint") return;
    const number = waypointNumber(event.display_label || event.label);
    if (number !== null) highestNumber = Math.max(highestNumber, number);
  });
  return `Waypoint ${highestNumber + 1}`;
}

export function numberedWaypointSequence(waypoints, startId) {
  const first = waypoints.find(event => event.id === startId);
  const start = waypointNumber(first?.display_label || first?.label);
  if (!Number.isSafeInteger(start) || start < 1) return [];
  const stops = new Map([[start, first]]);
  for (const event of waypoints) {
    const number = waypointNumber(event.display_label || event.label);
    if (!Number.isSafeInteger(number) || number <= start) continue;
    stops.set(number, stops.has(number) ? null : event);
  }
  const sequence = [];
  for (let number = start; stops.get(number); number++) {
    const event = stops.get(number);
    if (!Number.isFinite(event.lon) || !Number.isFinite(event.lat)) break;
    sequence.push({id: event.id, label: event.display_label || event.label, target: [event.lon, event.lat]});
  }
  return sequence.length > 1 ? sequence : [];
}
