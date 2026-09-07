import {calculatedRouteUrl} from "./route-navigation.js?v=20260907-3";
import {formatMapBytes} from "./map-offline.js?v=20260903-1";
import {createRoutingCatalogue} from "./routing-catalogue.js?v=20260907-1";

export const ROUTING_MODE_KEY = "reticom-routing-mode";

// Only absence of local coverage/engine permits the explicitly enabled online
// fallback. A local no-route/error never silently sends coordinates elsewhere.
export async function calculateDeviceRoute(origin, target, profile, {
  fetcher = fetch, signal, offlineOnly = false, beforeOnline = async () => {},
} = {}) {
  const url = calculatedRouteUrl(origin, target, undefined, profile);
  if (!url) throw new Error("Invalid route coordinates or travel mode");
  const local = await fetcher("/api/offline-routing/route", {
    method: "POST", headers: {"content-type": "application/json"}, signal,
    body: JSON.stringify({origin, target, profile}),
  });
  const payload = await local.json();
  if (local.ok) return payload;
  if (offlineOnly || !["outside_coverage", "engine_unavailable"].includes(payload.code)) {
    throw new Error(payload.detail || "Offline route calculation failed");
  }
  await beforeOnline();
  if (signal?.aborted) throw new DOMException("Route cancelled", "AbortError");
  const response = await fetcher(url, {cache: "no-store", signal, headers: {accept: "application/json"}});
  const online = await response.json();
  if (!response.ok) throw new Error(online.message || "Online route unavailable; import a routing pack to navigate offline");
  return {...online, source: "online"};
}

export function initOfflineRoutingSettings() {
  const section = document.getElementById("offlineRoutingSettings");
  if (!section) return {refresh: async () => {}};
  const status = section.querySelector("[data-routing-status]");
  const list = section.querySelector("[data-routing-packs]");
  const input = section.querySelector("input[type=file]");
  const mode = section.querySelector("select");
  mode.value = localStorage.getItem(ROUTING_MODE_KEY) === "offline" ? "offline" : "auto";
  mode.addEventListener("change", () => localStorage.setItem(ROUTING_MODE_KEY, mode.value));
  const render = (data) => {
    list.replaceChildren();
    status.textContent = data.engine_available
      ? "Walking + driving · calculated on this device. Map images are downloaded separately above."
      : "This build has no offline routing engine. Install the offline-routing extra on desktop, or update the Android app.";
    for (const pack of data.packs || []) {
      const row = document.createElement("div");
      row.className = "offline-routing-pack";
      const text = document.createElement("span");
      const title = document.createElement("strong");
      title.textContent = pack.name;
      const detail = document.createElement("small");
      detail.textContent = `${formatMapBytes(pack.bytes)} · built ${pack.created_at.slice(0, 10)}`;
      detail.title = pack.source;
      text.append(title, detail);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "secondary compact";
      remove.textContent = "REMOVE";
      remove.setAttribute("aria-label", `Remove routing pack ${pack.name}`);
      remove.addEventListener("click", async () => {
        if (!confirm(`Remove ${pack.name} routing data from this device? Saved map images and shared routes remain.`)) return;
        remove.disabled = true;
        try {
          const response = await fetch(`/api/offline-routing/${encodeURIComponent(pack.id)}`, {method: "DELETE"});
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Pack could not be removed");
          render(result);
          catalogue.refresh();
        } catch (error) { status.textContent = error.message; remove.disabled = false; }
      });
      row.append(text, remove);
      list.append(row);
    }
    if (!list.childElementCount) list.textContent = "No regions saved yet. Choose a download below.";
  };
  const refresh = async () => {
    catalogue.refresh();
    try {
      const response = await fetch("/api/offline-routing", {cache: "no-store"});
      if (!response.ok) throw new Error("Routing storage could not be read");
      render(await response.json());
    } catch (error) { status.textContent = error.message; }
  };
  const catalogue = createRoutingCatalogue(section, () => refresh());
  input.addEventListener("change", async () => {
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 512 * 1024 * 1024) { status.textContent = "Pack is too large (maximum 512 MB)."; input.value = ""; return; }
    input.disabled = true;
    section.setAttribute("aria-busy", "true");
    status.textContent = `Importing ${file.name} · ${formatMapBytes(file.size)}. Keep the app open…`;
    try {
      const response = await fetch("/api/offline-routing/import", {method: "POST", body: file, headers: {"content-type": "application/octet-stream"}});
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "Routing pack import failed");
      render(data);
      catalogue.refresh();
    } catch (error) { status.textContent = error.message; }
    finally { input.disabled = false; input.value = ""; section.removeAttribute("aria-busy"); }
  });
  return {refresh};
}
