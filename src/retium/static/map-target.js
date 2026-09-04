export function mapTargetCapabilities({role, kind, markerType, senderHash, localIdentityHash}) {
  const waypoint = kind === "marker" && markerType === "waypoint";
  const operator = kind === "operator";
  const navigate = role === "field" && (waypoint || operator);
  return {
    navigate,
    privateChat: navigate && operator && Boolean(senderHash) && senderHash !== localIdentityHash,
    targetKind: operator ? "operator" : "waypoint",
  };
}

export function latestOperatorMapTarget(locations, senderHash) {
  const position = (locations || []).find((event) => (
    event?.type === "position.updated"
      && (event.network?.sender_hash || event.callsign) === senderHash
      && Number.isFinite(event.lon)
      && Number.isFinite(event.lat)
  ));
  if (!position) return null;
  return {
    target: [position.lon, position.lat],
    label: position.callsign || "Operator",
  };
}
