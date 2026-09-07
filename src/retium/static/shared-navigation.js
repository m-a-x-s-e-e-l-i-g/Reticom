import {latestNavigationPlans, sharedNavigationFeatures} from "./shared-navigation-model.js?v=20260906-1";
import {routeInstruction, formatRouteDuration} from "./route-navigation.js?v=20260903-1";

export function navigationSharePayload(navigation, distanceBetween) {
  if (!navigation) throw new Error("Start navigation first");
  const following = navigation.following;
  const origin = navigation.pinnedRoute || navigation.mode === "route" ? navigation.routeOrigin : navigation.currentPoint;
  const coordinates = navigation.pinnedRoute || navigation.mode === "route" ? navigation.routeCoordinates : [origin, navigation.target];
  if (!origin || !coordinates?.length || coordinates.length < 2 || navigation.routeLoading || navigation.routeError) {
    throw new Error(navigation.mode === "route" ? "Wait for the calculated route before sharing" : "Wait for your location before sharing");
  }
  const directions = navigation.pinnedRoute ? navigation.sharedDirections || [] : (navigation.routeSteps || []).map(step => ({
    text: routeInstruction([step]), distance_m: Number(step.distance) || 0,
  }));
  return {
    label: navigation.label, mode: navigation.mode, origin, target: navigation.target,
    target_kind: following ? "shared" : navigation.targetKind,
    target_id: navigation.id || "", coordinates,
    distance_m: navigation.mode === "route" ? navigation.routeDistance : distanceBetween(origin, navigation.target),
    duration_s: navigation.mode === "route" ? navigation.routeDuration : 0,
    ...(following ? {following: {sender_hash: following.sender_hash, route_id: following.route_id}} : {}),
    ...(directions.length ? {directions} : {}),
  };
}

// Serialize updates and stops so a delayed PUT cannot resurrect a stopped route.
// Capture the team URL on enqueue, not after an asynchronous team switch.
export function navigationWriter({fetchImpl, urlFor = path => path}) {
  let chain = Promise.resolve();
  return (method, body) => {
    const url = urlFor("/api/navigation");
    const serialized = body ? JSON.stringify(body) : null;
    const operation = chain.catch(() => {}).then(async () => {
      const response = await fetchImpl(url, {method, headers: {"content-type": "application/json"},
        ...(serialized ? {body: serialized} : {})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Could not update shared navigation");
      return result;
    });
    chain = operation;
    return operation;
  };
}

const element = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const distance = meters => Number(meters) < 1000 ? `${Math.round(Number(meters) || 0)} m` : `${(Number(meters) / 1000).toFixed(1)} km`;

export function createSharedNavigation({fetchImpl, urlFor, getIdentity, getRole, onFollow, onStopSharing, onPlans, toast}) {
  let plans = [], signature = "", loading = false, context = "";
  const maps = new Map();
  const writer = navigationWriter({fetchImpl, urlFor});
  const containers = ["commandSharedRoutes", "fieldSharedRoutes"].map(id => document.getElementById(id));
  const own = () => plans.find(plan => plan.active && plan.sender_hash === getIdentity());
  const planFor = sender => plans.find(plan => plan.active && plan.sender_hash === sender);
  const queued = plan => plan.network?.queued || plan.network?.delivery_status === "queued";
  function installLayers(map) {
    if (map.getSource("shared-navigation")) return;
    map.addSource("shared-navigation", {type: "geojson", data: {type: "FeatureCollection", features: []}});
    const lineFilter = ["==", ["geometry-type"], "LineString"];
    map.addLayer({id: "shared-navigation-casing", source: "shared-navigation", type: "line", filter: lineFilter,
      paint: {"line-color": "#070a08", "line-width": 6, "line-opacity": .7}, layout: {"line-cap": "round", "line-join": "round"}});
    map.addLayer({id: "shared-navigation-lines", source: "shared-navigation", type: "line", filter: lineFilter,
      paint: {"line-color": ["get", "color"], "line-width": 3, "line-opacity": ["get", "opacity"], "line-dasharray": [3, 2]},
      layout: {"line-cap": "round", "line-join": "round"}});
    map.addLayer({id: "shared-navigation-targets", source: "shared-navigation", type: "circle", filter: ["==", ["geometry-type"], "Point"],
      paint: {"circle-radius": 6, "circle-color": "#070a08", "circle-stroke-color": ["get", "color"], "circle-stroke-width": 2}});
    map.addLayer({id: "shared-navigation-labels", source: "shared-navigation", type: "symbol", filter: ["==", ["geometry-type"], "Point"],
      layout: {"text-field": ["concat", ["get", "callsign"], " → ", ["get", "label"]], "text-font": ["Noto Sans Regular"],
        "text-size": 11, "text-offset": [0, 1.2], "text-anchor": "top", "text-max-width": 18},
      paint: {"text-color": ["get", "color"], "text-halo-color": "#070a08", "text-halo-width": 2}});
  }
  function renderMaps() {
    const data = sharedNavigationFeatures(plans, {ownIdentity: getIdentity()});
    for (const map of maps.keys()) {
      if (!map.getSource("shared-navigation")) {
        if (!map.isStyleLoaded()) continue;
        installLayers(map);
      }
      map.getSource("shared-navigation")?.setData(data);
    }
  }
  function metadata(plan) {
    const updated = new Date(Number(plan.created_at || plan.timestamp || 0) * 1000);
    return `${plan.mode === "route" ? "Road route" : "Direct line"} · ${distance(plan.distance_m)}${plan.mode === "route" ? ` · ${formatRouteDuration(plan.duration_s)}` : ""}`
      + `${queued(plan) ? " · queued" : " · shared"}${Number.isFinite(updated.getTime()) && updated.getTime() > 0 ? ` · ${updated.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})}` : ""}`;
  }
  function show(plan, map, Popup, position = plan.target) {
    const content = element("div", "", "map-popup shared-route-popup");
    content.append(element("small", `${plan.callsign} → ${plan.label}`, "eyebrow"), element("strong", plan.label), element("p", metadata(plan)));
    content.append(element("small", "Shared plan, not a live position. Dashed line shows the exact shared path."));
    if (plan.following) content.append(element("small", "Following a team route"));
    if (plan.directions?.length) {
      const details = element("details"), list = element("ol", "", "shared-route-directions");
      details.append(element("summary", `${plan.directions.length} directions`));
      for (const step of plan.directions) list.append(element("li", `${step.text} · ${distance(step.distance_m)}`));
      details.append(list); content.append(details);
    }
    const popup = new Popup({closeButton: true, offset: 16, maxWidth: "310px"}).setLngLat(position).setDOMContent(content).addTo(map);
    if (getRole() === "field") {
      const follow = element("button", plan.sender_hash === getIdentity() ? "RESUME NAVIGATION" : "FOLLOW & SHARE", "secondary map-navigation-choice");
      follow.type = "button";
      follow.addEventListener("click", () => { popup.remove(); onFollow(plan); });
      content.append(follow);
      if (plan.sender_hash !== getIdentity()) content.append(element("small", "Uses this exact path. Your navigation will be visible to the team."));
    }
    if (plan.sender_hash === getIdentity()) {
      const stop = element("button", "STOP SHARING", "text-button"); stop.type = "button";
      stop.addEventListener("click", async () => {
        stop.disabled = true;
        try { await onStopSharing(); popup.remove(); }
        catch (error) { toast(error.message, true); } finally { stop.disabled = false; }
      }); content.append(stop);
    }
  }
  function focus(plan) {
    const map = [...maps.keys()].find(candidate => candidate.getContainer().getBoundingClientRect().width > 0) || [...maps.keys()][0];
    if (!map) return;
    const points = plan.coordinates;
    if (points?.length) {
      const lons = points.map(point => point[0]), lats = points.map(point => point[1]);
      map.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], {padding: 72, maxZoom: 16, duration: 0});
    }
    show(plan, map, maps.get(map));
  }
  function renderLists() {
    const active = plans.filter(plan => plan.active);
    for (const container of containers) {
      if (!container) continue;
      container.replaceChildren();
      container.parentElement.classList.toggle("hidden", !active.length);
      for (const plan of active) {
        const row = element("div", "", "shared-route-row");
        const view = element("button", "", "shared-route-view"); view.type = "button";
        view.append(element("strong", `${plan.callsign} → ${plan.label}`), element("small", metadata(plan)));
        view.setAttribute("aria-label", `View ${plan.callsign}'s route to ${plan.label}`);
        view.addEventListener("click", () => {
          if (getRole() === "field") document.querySelector('[data-field-page="map"]')?.click();
          focus(plan);
        });
        row.append(view); container.append(row);
      }
    }
  }
  function accept(items) {
    plans = latestNavigationPlans(items, {includeStopped: true});
    const next = JSON.stringify([getIdentity(), getRole(), plans]);
    if (signature !== next) { signature = next; renderLists(); renderMaps(); onPlans?.(plans); }
  }
  async function write(method, body) {
    const requestContext = context;
    const result = await writer(method, body);
    if (requestContext === context && result.plan) accept([...plans, result.plan]);
    return result;
  }
  return {
    own, planFor, write, focus,
    async refresh() {
      if (loading) return;
      const url = urlFor("/api/navigation"), requestContext = context;
      loading = true;
      try {
        const response = await fetchImpl(url, {cache: "no-store"});
        if (!response.ok) return;
        const data = await response.json();
        if (requestContext === context) accept([...plans, ...(data.plans || [])]);
      } catch { /* Keep the last authenticated plans while the local node reconnects. */ }
      finally { loading = false; }
    },
    setContext(value) { if (context !== value) { context = value; accept([]); } },
    attachMap(map, Popup) {
      if (maps.has(map)) return;
      maps.set(map, Popup);
      installLayers(map); renderMaps();
      map.on("style.load", () => { installLayers(map); renderMaps(); });
    },
    showFeature(map, Popup, feature, position) {
      const plan = planFor(feature.properties?.senderHash);
      if (plan) show(plan, map, Popup, position);
    },
  };
}
