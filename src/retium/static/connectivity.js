export function connectivityState(state, probe, now = Date.now()) {
  const interfaces = state?.network?.interfaces?.interfaces || [];
  const active = interfaces.filter((item) => {
    const up = item.status === true || item.online === true || item.status === "Up";
    if (/ServerInterface$/.test(item.type || "")) return up && Number(item.clients) > 0;
    return up;
  });
  const fresh = probe && now - probe.at < 45000 && now >= probe.at;
  const remote = state?.role === "field" && state?.team?.joined && !state?.network?.hosted;
  const latency = remote && fresh && probe.online && typeof probe.ms === "number"
    && Number.isFinite(probe.ms) && probe.ms >= 0 ? probe.ms : null;
  let bars = active.length ? 1 : 0;
  let label = active.length ? "Connected · quality unmeasured" : "No active links";
  if (!state) label = "Local node unavailable";
  else if (remote && fresh && !probe.online) label = "Team offline";
  else if (latency !== null) {
    bars = latency <= 300 ? 4 : latency <= 1000 ? 3 : 2;
    label = bars === 4 ? "Fast team connection" : bars === 3 ? "Good team connection" : "Slow team connection";
  }
  return {bars, label, active, interfaces, latency, team: !state ? "Unknown"
    : state.role === "gateway" || state.network?.hosted ? "Hosted on this device"
    : !state.team?.joined ? "No team joined"
    : fresh ? (probe.online ? "Reachable" : "Offline · saving locally")
    : "Waiting for a fresh team response"};
}
