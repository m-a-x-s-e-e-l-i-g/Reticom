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
