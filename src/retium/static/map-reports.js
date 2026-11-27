import {tacticalMarker} from "./marker-catalog.js?v=20261127-1";

export function reportDraft(type, location, description, urgent = false) {
  const marker = tacticalMarker(type);
  description = String(description || "").trim();
  if (!marker) throw new Error("Choose a report type");
  if (["note", "other"].includes(type) && !description) throw new Error("Describe what you are reporting");
  if (description.length > 160) throw new Error("Keep the description under 160 characters");
  return {type: "marker.created", marker_type: type, lat: location.lat, lon: location.lon,
    label: ["note", "other"].includes(type) ? description.slice(0, 80) : marker.label,
    description, urgent: Boolean(urgent)};
}

export function reportComposerHtml() {
  return `<header><strong data-report-heading>Report</strong><button type="button" data-report-cancel aria-label="Close report">×</button></header>
    <label>DESCRIPTION <small data-report-description-hint>optional</small>
      <textarea data-report-description maxlength="160" rows="2" placeholder="What should the team know?"></textarea></label>
    <label class="map-report-urgent"><input type="checkbox" data-report-urgent>Urgent</label>
    <p class="map-report-help">Your callsign and time are attached automatically.</p>
    <footer><button type="button" data-report-back>BACK</button><button type="submit">SHARE REPORT</button></footer>`;
}
