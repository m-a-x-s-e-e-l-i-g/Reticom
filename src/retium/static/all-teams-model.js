import {displayPositionSeries, locationTime} from "./location-filter.js";
import {drawingFeatures} from "./map-drawings.js?v=20260905-2";
import {withDisplayWaypointLabels} from "./map-labels.js";
import {tacticalMarker, reportProperties, REPORT_STATUS_LABELS} from "./marker-catalog.js?v=20260905-2";
import {activeAutomaticReports, buildAutomaticReportFeatures, AUTOMATIC_REPORT_POSITION_MAX_AGE_SECONDS} from "./automatic-reports.js";
import {latestNavigationPlans, sharedNavigationFeatures} from "./shared-navigation-model.js";

const PALETTE = ["#899a78", "#8cabc0", "#b8a16d", "#a393af", "#79a59a", "#b59e91"];
export function teamColor(id) {
  let hash = 0;
  for (const character of String(id)) hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  return PALETTE[hash % PALETTE.length];
}
const validPosition = (e) => Number.isFinite(e.lat) && Math.abs(e.lat) <= 90 && Number.isFinite(e.lon) && Math.abs(e.lon) <= 180;

export function overviewEventText(event) {
  switch (event.type) {
    case "chat.message": return event.message || "";
    case "ptt.broadcast": return event.transcript || "Voice transmission · open team to play";
    case "task.created": return `New task: ${event.title}${event.assignee ? ` · ${event.assignee}` : ""}`;
    case "task.completed": return "Assignment completed";
    case "marker.created": return `Marker: ${event.display_label || event.label || event.marker_type}`;
    case "drawing.created": return `Shared ${event.drawing_type || "trace"}${event.label ? ` · ${event.label}` : ""}`;
    case "position.updated": return "Location sharing resumed after more than an hour";
    case "team.joined": return "Joined the team";
    case "profile.updated": return "Updated callsign or appearance";
    case "waypoint.arrived": return `${event.operator_callsign} reached ${event.waypoint_label}`;
    case "navigation.updated": return `Shared ${event.mode === "route" ? "road route" : "straight line"} to ${event.label}`;
    case "navigation.stopped": return "Stopped sharing navigation";
    default: return event.type;
  }
}

function drawingGeometry(event) {
  const points = (event.points || []).filter((p) => Array.isArray(p) && validPosition({lon: p[0], lat: p[1]}));
  if (points.length < 2) return null;
  if (event.drawing_type !== "arrow") return {type: "LineString", coordinates: points};
  const start = points[0], end = points.at(-1);
  const scale = Math.max(.2, Math.cos((start[1] + end[1]) / 2 * Math.PI / 180));
  const dx = (end[0] - start[0]) * scale, dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  if (!length) return {type: "LineString", coordinates: points};
  const head = length * .24, ux = dx / length, uy = dy / length;
  const bx = end[0] * scale - ux * head, by = end[1] - uy * head, wing = head * .58;
  return {type: "MultiLineString", coordinates: [points, [end, [(bx - uy * wing) / scale, by + ux * wing]], [end, [(bx + uy * wing) / scale, by - ux * wing]]]};
}

export function allTeamsModel(teams, hidden = new Set(), now = Date.now() / 1000) {
  const features = [], feed = [], summaries = [];
  for (const team of teams) {
    const events = withDisplayWaypointLabels(team.events || []);
    const color = teamColor(team.id);
    const navigation = latestNavigationPlans(events, {includeStopped: true});
    const navigationIds = new Set(navigation.map((plan) => plan.id));
    const positions = new Map(), profiles = new Map();
    for (const event of events) {
      const sender = event.network?.sender_hash;
      if (!sender) continue;
      if (!profiles.has(sender)) profiles.set(sender, event);
      if (event.type === "position.updated" && validPosition(event)) {
        if (!positions.has(sender)) positions.set(sender, []);
        positions.get(sender).push(event);
      }
    }
    positions.forEach((items, sender) => positions.set(sender, displayPositionSeries(items)));
    const located = [...positions.values()].filter((series) => series.length);
    summaries.push({...team, color, located: located.length,
      recent: team.hosting ? located.filter((series) => now - locationTime(series.at(-1)) <= 300).length : 0,
      visible: !hidden.has(team.id)});
    if (hidden.has(team.id)) continue;
    const featureCounts = new Map();
    const add = (geometry, event, extra = {}) => {
      if (!geometry) return;
      const label = extra.label || event.display_label || event.label || event.callsign || "Report";
      const properties = {
        teamId: team.id, teamName: team.name, teamColor: color,
        hosting: team.hosting, stale: !team.hosting,
        eventId: event.id, callsign: event.callsign || "",
        observedAt: locationTime(event), color, kind: "marker", label,
        description: overviewEventText(event), ...extra,
        caption: `${team.name} · ${label}`,
      };
      const key = `${team.id}:${event.id}:${properties.kind}:${geometry.type}`;
      const index = featureCounts.get(key) || 0;
      featureCounts.set(key, index + 1);
      features.push({type: "Feature", id: `${key}:${index}`, geometry, properties});
    };
    for (const [sender, series] of positions) {
      if (!series.length) continue;
      const latest = series.at(-1), profile = profiles.get(sender) || latest;
      add({type: "Point", coordinates: [latest.lon, latest.lat]}, latest, {
        kind: "operator", label: profile.callsign, symbol: "", stale: !team.hosting || now - locationTime(latest) > 300,
        description: `Last reported fix · accuracy ${Math.round(latest.accuracy || 100)} m`,
      });
      const track = series.filter((fix) => locationTime(latest) - locationTime(fix) <= 1800);
      if (team.hosting && now - locationTime(latest) <= 600 && track.length > 1) {
        add({type: "LineString", coordinates: track.map((fix) => [fix.lon, fix.lat])}, latest, {kind: "track", label: profile.callsign});
      }
    }
    for (const feature of sharedNavigationFeatures(navigation, {colorForSender: () => color}).features) {
      const plan = navigation.find((item) => item.id === feature.properties.eventId);
      add(feature.geometry, plan, {...feature.properties, label: feature.properties.caption,
        stale: !team.hosting || feature.properties.stale});
    }
    for (const event of events) {
      if (event.type.startsWith("private.")) continue;
      if (["navigation.updated", "navigation.stopped"].includes(event.type) && !navigationIds.has(event.id)) continue;
      if (event.type === "marker.created" && validPosition(event)) {
        const marker = tacticalMarker(event.marker_type);
        const report = reportProperties(event);
        add({type: "Point", coordinates: [event.lon, event.lat]}, event, {color: marker?.color || color,
          symbol: marker?.symbol || ({waypoint: "WP", warning: "!", obstacle: "×", observation: "OBS", text: "T"})[event.marker_type] || "", kind: "marker", ...report,
          ...(report.reportStatus ? {description: `${REPORT_STATUS_LABELS[report.reportStatus]}${report.reportUrgent ? " · URGENT" : ""} · ${event.callsign}${event.description ? ` · ${event.description}` : ""}`} : {}),
          ...(report.reportStatus === "cleared" ? {color: "#59615c"} : {})});
      } else if (event.type === "drawing.created") {
        const geometry = drawingGeometry(event);
        if (geometry) for (const feature of drawingFeatures(event, {kind: "drawing", color}, geometry)) {
          add(feature.geometry, event, feature.properties);
        }
      }
      if (event.type !== "position.updated" || event.network?.became_active === true) {
        feed.push({teamId: team.id, teamName: team.name, color, hosting: team.hosting, event,
          key: `${team.id}:${event.id}`, at: locationTime(event), text: overviewEventText(event)});
      }
    }
    const automatic = activeAutomaticReports(events
      .filter((event) => ["chat.message", "ptt.broadcast"].includes(event.type))
      .map((event) => ({id: event.id, senderHash: event.network?.sender_hash, callsign: event.callsign,
        createdAt: event.created_at, message: event.type === "chat.message" ? event.message : event.transcript, event})), now);
    for (const item of automatic) {
      const {event, report} = item;
      const candidates = positions.get(event.network?.sender_hash) || [];
      const origin = [...candidates].reverse().find((fix) => fix.created_at <= event.created_at + 5);
      if (!origin || event.created_at - origin.created_at > AUTOMATIC_REPORT_POSITION_MAX_AGE_SECONDS) continue;
      for (const feature of buildAutomaticReportFeatures(report, [origin.lon, origin.lat], {
        id: event.id, callsign: event.callsign, senderHash: item.senderHash, createdAt: event.created_at, message: item.message, nowSeconds: now,
      })) add(feature.geometry, event, {...feature.properties, kind: "automatic", description: item.message});
    }
  }
  feed.sort((a, b) => b.at - a.at || a.key.localeCompare(b.key));
  return {features: {type: "FeatureCollection", features}, feed: feed.slice(0, 100), teams: summaries};
}

// Find the shortest longitude interval, so teams across the dateline don't
// force an almost-worldwide zoom when explicitly fitting the operational area.
export function overviewBounds(features) {
  const points = [];
  const visit = (value) => {
    if (Array.isArray(value) && typeof value[0] === "number") points.push(value);
    else if (Array.isArray(value)) value.forEach(visit);
  };
  features.forEach((feature) => visit(feature.geometry.coordinates));
  if (!points.length) return null;
  const lons = points.map((p) => ((p[0] % 360) + 360) % 360).sort((a, b) => a - b);
  let gap = -1, first = 0;
  for (let index = 0; index < lons.length; index++) {
    const size = (index === lons.length - 1 ? lons[0] + 360 : lons[index + 1]) - lons[index];
    if (size > gap) { gap = size; first = (index + 1) % lons.length; }
  }
  const west = lons[first] > 180 ? lons[first] - 360 : lons[first];
  const lats = points.map((p) => p[1]);
  return [[west, Math.min(...lats)], [west + 360 - gap, Math.max(...lats)]];
}
