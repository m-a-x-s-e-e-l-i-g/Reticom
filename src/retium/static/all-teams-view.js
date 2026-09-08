import {onLandmarkResolved} from "./report-landmarks.js?v=20260908-2";
import {allTeamsModel, overviewBounds} from "./all-teams-model.js?v=20260908-3";
import {addDrawingDecorationLayers} from "./map-drawings.js?v=20260905-2";
import {teamPageUrl} from "./command-teams.js";
import {HeadingTracker, HeadingOverlay, HeadingConnection} from "./live-heading.js?v=20260905-2";

const $ = (id) => document.getElementById(id);
const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[c]);
const stamp = (seconds) => seconds ? new Date(seconds * 1000).toLocaleString([], {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit"}) : "No reports yet";
const urlFor = (id) => teamPageUrl(id, location.href);

export async function startAllTeamsView({mapStyle, initialStyle = "hiking", saveStyle, onMapReady}) {
  let map, popup, popupId, model, snapshot = {teams: []}, loading = false, framed = false, disconnected = false;
  let styleMode = initialStyle, teamSignature = "", feedSignature = "";
  window.addEventListener("reticom-map-style", event => {
    if (!map || !["dark", "hiking", "satellite"].includes(event.detail)) return;
    styleMode = event.detail;
    map.setStyle(mapStyle(styleMode));
    updateStyleButton();
  });
  onLandmarkResolved(() => render());
  const hidden = new Set();
  const headingViews = new Map();
  $("allTeamsView").classList.remove("hidden");
  const mapUrl = (input) => {
    const url = new URL(input, location.href);
    if (url.origin === location.origin && url.pathname.startsWith("/api/map/")) {
      url.pathname = url.pathname.replace("/api/map/", "/api/command/map/");
      url.searchParams.delete("team");
    }
    return url.href;
  };

  function addLayers() {
    if (map.getSource("all-teams")) return;
    map.addSource("all-teams", {type: "geojson", data: {type: "FeatureCollection", features: []}});
    const color = ["coalesce", ["get", "color"], "#899a78"];
    const opacity = ["case", ["==", ["get", "stale"], true], .55, .9];
    addDrawingDecorationLayers(map, "all-teams", "all-team-drawings");
    map.addLayer({id: "all-team-areas", type: "fill", source: "all-teams", filter: ["all", ["!=", ["get", "kind"], "drawing"], ["==", ["geometry-type"], "Polygon"]], paint: {"fill-color": color, "fill-opacity": .16}});
    map.addLayer({id: "all-team-lines", type: "line", source: "all-teams", filter: ["!=", ["geometry-type"], "Point"], layout: {"line-cap": "round", "line-join": "round"}, paint: {"line-color": color, "line-width": 2.5, "line-opacity": opacity}});
    const pointFilter = ["all", ["==", ["geometry-type"], "Point"], ["!=", ["get", "drawingLabel"], true]];
    map.addLayer({id: "all-team-points", type: "circle", source: "all-teams", filter: pointFilter, paint: {
      "circle-radius": ["case", ["==", ["get", "kind"], "operator"], 7, 13],
      "circle-color": ["case", ["==", ["get", "kind"], "operator"], color, "#121711"],
      "circle-opacity": opacity, "circle-stroke-width": 2,
      "circle-stroke-color": ["get", "teamColor"], "circle-stroke-opacity": opacity,
    }});
    map.addLayer({id: "all-team-symbols", type: "symbol", source: "all-teams", filter: pointFilter, layout: {
      "text-field": ["coalesce", ["get", "symbol"], ""], "text-size": 9, "text-font": ["Noto Sans Regular"], "text-allow-overlap": true,
    }, paint: {"text-color": color, "text-opacity": opacity}});
    map.addLayer({id: "all-team-labels", type: "symbol", source: "all-teams", filter: pointFilter, layout: {
      "text-field": ["get", "caption"], "text-size": 11, "text-font": ["Noto Sans Regular"], "text-offset": [0, 1.6], "text-anchor": "top", "text-max-width": 20,
    }, paint: {"text-color": ["get", "teamColor"], "text-halo-color": "#080c08", "text-halo-width": 1.5, "text-opacity": opacity}});
  }

  function fit(features = model?.features.features || []) {
    const bounds = overviewBounds(features);
    if (bounds && map) { framed = true; map.fitBounds(bounds, {padding: 64, maxZoom: 15, duration: 0}); }
  }

  function render() {
    model = allTeamsModel(snapshot.teams.map((team) => disconnected ? {...team, hosting: false} : team), hidden);
    for (const [id, view] of headingViews) {
      if (disconnected || hidden.has(id) || !snapshot.teams.some(team => team.id === id && team.hosting)) {
        view.connection.destroy(); view.overlay.destroy(); headingViews.delete(id);
      }
    }
    if (map) for (const team of snapshot.teams) {
      if (disconnected || !team.hosting || hidden.has(team.id)) continue;
      if (!headingViews.has(team.id)) {
        const tracker = new HeadingTracker();
        const overlay = new HeadingOverlay(map, tracker, () => "", `live-pointing-${team.id}`);
        const connection = new HeadingConnection({
          clock: tracker.clock,
          url: () => `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/heading/live?team=${encodeURIComponent(team.id)}`,
          operational: () => !disconnected && !hidden.has(team.id), eligible: () => false, sample: () => null, status: () => {},
          receive: frame => { if (frame.type === "heading.clear") tracker.clear(); else tracker.accept(frame); overlay.wake(); },
        });
        headingViews.set(team.id, {overlay, connection});
      }
      const operators = model.features.features.filter(feature => feature.properties.teamId === team.id && feature.properties.kind === "operator");
      headingViews.get(team.id).overlay.setOperators(operators.map(feature => ({
        ...(team.events || []).find(event => event.id === feature.properties.eventId),
        lon: feature.geometry.coordinates[0], lat: feature.geometry.coordinates[1], headingColor: feature.properties.teamColor,
      })));
    }
    const teams = model.teams;
    $("allTeamsSummary").textContent = `${teams.filter((team) => team.visible).length} / ${teams.length} teams shown`;
    $("allTeamsHostSummary").textContent = disconnected ? "Snapshot · connection lost" : `${teams.filter((team) => team.hosting).length} active · ${teams.filter((team) => !team.hosting).length} stopped`;
    $("allTeamsRefreshState").textContent = disconnected
      ? `Connection lost · showing snapshot from ${stamp(snapshot.snapshot_at)}`
      : `Updated ${stamp(snapshot.snapshot_at)} · every 5 s`;
    $("allTeamsRefreshState").classList.toggle("is-stale", disconnected);
    const nextTeamSignature = JSON.stringify(teams.map(({events, ...team}) => team));
    if (nextTeamSignature !== teamSignature) {
      teamSignature = nextTeamSignature;
      const focusedId = document.activeElement?.dataset?.teamVisibility;
      $("allTeamsRoster").innerHTML = teams.map((team) => `
        <article class="overview-team ${team.visible ? "" : "is-hidden"}">
          <label class="overview-team-name"><input type="checkbox" data-team-visibility="${escape(team.id)}" ${team.visible ? "checked" : ""}><i style="background:${team.color}" aria-hidden="true"></i><strong>${escape(team.name)}</strong></label>
          <small class="overview-host-state">${!team.snapshot_available ? "SNAPSHOT UNAVAILABLE" : disconnected ? "CONNECTION LOST" : team.hosting ? team.mode === "member" ? "JOINED · SYNC ACTIVE" : "HOSTING HERE" : "STOPPED · LAST SAVED DATA"}</small>
          <p>${team.snapshot_available ? `${team.located} located · ${team.recent} recent<br>${team.open_tasks ?? "—"} open tasks` : "Reports unavailable"}</p>
          <small>Last report ${escape(stamp(team.last_event_at))}</small>
          <div class="overview-team-actions"><button type="button" class="text-button" data-focus-team="${escape(team.id)}">LOCATE</button><a href="${escape(team.hosting && !disconnected ? urlFor(team.id) : urlFor("none"))}">${team.hosting && !disconnected ? "OPEN TEAM →" : "MANAGE →"}</a></div>
        </article>`).join("") || '<p class="empty-copy">No teams yet. Use Teams to create one.</p>';
      if (focusedId) [...$("allTeamsRoster").querySelectorAll("[data-team-visibility]")].find((input) => input.dataset.teamVisibility === focusedId)?.focus();
    }
    const nextFeedSignature = JSON.stringify(model.feed);
    if (nextFeedSignature !== feedSignature) {
      feedSignature = nextFeedSignature;
      $("allTeamsIntel").innerHTML = model.feed.map((item) => `
        <article class="overview-intel-item">
          <div><a style="color:${item.color}" href="${escape(item.hosting && !disconnected ? urlFor(item.teamId) : urlFor("none"))}">${escape(item.teamName)}</a><time>${escape(stamp(item.at))}</time></div>
          <strong>${escape(item.event.callsign)}</strong><p>${escape(item.text)}</p>
        </article>`).join("") || '<p class="empty-copy">No intel for the visible teams.</p>';
    }
    $("allTeamsMapEmpty").textContent = !teams.length ? "Create a team to build the shared picture." : !teams.some((team) => team.visible) ? "All teams hidden. Select a team to show it." : "No usable positions or map reports for the visible teams.";
    $("allTeamsMapEmpty").classList.toggle("hidden", model.features.features.length > 0);
    $("fitAllTeams").disabled = !model.features.features.length;
    if (map?.getSource("all-teams")) {
      map.getSource("all-teams").setData(model.features);
      if (!framed) fit();
    }
    // Never leave a removed report or hidden team's popup visible after refresh.
    if (popup && !model.features.features.some((feature) => feature.id === popupId)) popup.remove();
  }

  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const response = await fetch("/api/command/overview", {cache: "no-store", signal: AbortSignal.timeout(12000)});
      if (!response.ok) throw new Error("Overview unavailable");
      snapshot = await response.json();
      disconnected = false;
    } catch { disconnected = true; }
    finally { loading = false; render(); }
  }

  $("allTeamsRoster").addEventListener("change", (event) => {
    const input = event.target.closest("[data-team-visibility]");
    if (!input) return;
    if (input.checked) hidden.delete(input.dataset.teamVisibility); else hidden.add(input.dataset.teamVisibility);
    render();
  });
  $("allTeamsRoster").addEventListener("click", (event) => {
    const button = event.target.closest("[data-focus-team]");
    if (!button) return;
    hidden.delete(button.dataset.focusTeam);
    render();
    const features = model.features.features.filter((feature) => feature.properties.teamId === button.dataset.focusTeam);
    if (!features.length) $("allTeamsMapStatus").textContent = "This team has no usable map reports yet.";
    else { $("allTeamsMapStatus").textContent = ""; fit(features); }
  });
  $("fitAllTeams").addEventListener("click", () => fit());
  $("showAllTeams").addEventListener("click", () => { hidden.clear(); render(); });
  $("refreshAllTeams").addEventListener("click", refresh);
  $("allTeamsMapStyle").addEventListener("click", () => {
    if (!map) return;
    styleMode = styleMode === "satellite" ? "hiking" : "satellite";
    saveStyle(styleMode);
    updateStyleButton();
    map.setStyle(mapStyle(styleMode)); // No fitBounds: retain the current camera.
  });
  function updateStyleButton() {
    $("allTeamsMapStyle").textContent = styleMode === "satellite" ? "MAP" : "SATELLITE";
    $("allTeamsMapStyle").setAttribute("aria-pressed", String(styleMode === "satellite"));
    $("allTeamsMapCredit").innerHTML = styleMode === "satellite"
      ? '<a href="https://www.esri.com/" target="_blank" rel="noreferrer">Imagery © Esri</a>'
      : '<a href="https://openfreemap.org/" target="_blank" rel="noreferrer">OpenFreeMap</a> · <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">© OSM</a>';
  }
  updateStyleButton();
  void refresh();
  const timer = setInterval(() => { if (!document.hidden) void refresh(); }, 5000);
  const visible = () => { if (!document.hidden) void refresh(); };
  document.addEventListener("visibilitychange", visible);
  window.addEventListener("pagehide", () => { clearInterval(timer); document.removeEventListener("visibilitychange", visible); }, {once: true});
  try {
    const {Map: LibreMap, NavigationControl, Popup} = await import("/vendor/maplibre-gl/maplibre-gl.mjs?v=6.6.0");
    map = new LibreMap({container: "allTeamsMap", style: mapStyle(styleMode), center: [0, 18], zoom: 1.25,
      attributionControl: false, pitchWithRotate: false, dragRotate: false,
      transformRequest: (url) => ({url: mapUrl(url)}),
    });
    map.addControl(new NavigationControl({showCompass: false}), "bottom-right");
    map.on("style.load", () => { addLayers(); render(); });
    onMapReady?.(map, Popup);
    map.on("movestart", (event) => { if (event.originalEvent) framed = true; });
    map.on("error", () => { $("allTeamsMapStatus").textContent = "Some map detail could not load. Saved team reports remain available."; });
    map.on("click", (event) => {
      if (!map.getLayer("all-team-points")) return;
      const [feature] = map.queryRenderedFeatures(event.point, {layers: ["all-team-points", "all-team-lines", "all-team-areas", "all-team-drawings-names", "all-team-drawings-fill", "all-team-drawings-pattern"]});
      if (!feature) return;
      const p = feature.properties;
      popup?.remove();
      popupId = feature.id;
      popup = new Popup({maxWidth: "280px"}).setLngLat(event.lngLat).setHTML(`
        <div class="overview-popup"><small style="color:${escape(p.teamColor)}">${escape(p.teamName)}</small><strong>${escape(p.label)}</strong>
        <p>${escape(p.description)}</p><small>${p.stale ? "LAST KNOWN · " : ""}${escape(stamp(Number(p.observedAt)))}</small>
        <a href="${escape(p.hosting ? urlFor(p.teamId) : urlFor("none"))}">${p.hosting ? "OPEN TEAM →" : "MANAGE TEAM →"}</a></div>`).addTo(map);
    });
  } catch {
    $("allTeamsMapStatus").textContent = "Map unavailable. Team status and intel are still shown.";
  }
}
