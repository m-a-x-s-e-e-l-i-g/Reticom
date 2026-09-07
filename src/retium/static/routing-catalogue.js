import {formatMapBytes} from "./map-offline.js?v=20260903-1";

export function filterRegions(regions, search) {
  const words = String(search || "").toLocaleLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").split(/\s+/).filter(Boolean);
  return regions.filter(region => {
    const text = `${region.name} ${region.country}`.toLocaleLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    return words.every(word => text.includes(word));
  }).sort((a, b) => a.country.localeCompare(b.country) || a.name.localeCompare(b.name));
}

export function regionAction(region, job) {
  if (region.state === "unpublished") return {label: "PENDING RELEASE", disabled: true};
  const active = ["downloading", "verifying", "installing"].includes(job?.status);
  if (active && job.id === region.id) return {label: job.status === "installing" ? "INSTALLING" : "CANCEL", disabled: job.status === "installing", cancel: true};
  if (region.state === "installed") return {label: "INSTALLED", disabled: true};
  if (job?.id === region.id && ["error", "cancelled"].includes(job.status)) return {label: "RETRY", disabled: active};
  return {label: region.state === "update_available" ? "UPDATE" : "DOWNLOAD", disabled: active};
}

export function createRoutingCatalogue(section, onReady) {
  const list = section.querySelector("[data-region-list]");
  const search = section.querySelector("[data-region-search]");
  const status = section.querySelector("[data-catalogue-status]");
  const update = section.querySelector("[data-catalogue-refresh]");
  let data = {regions: []}, timer, loading = false, lastReady = "", pending = false;
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const render = () => {
    list.replaceChildren();
    const filtered = filterRegions(data.regions || [], search.value);
    for (const region of filtered) {
      const row = node("div", undefined, "routing-region");
      const info = node("div", undefined, "routing-region-info");
      info.append(node("strong", region.name), node("small", `${region.country} · ${formatMapBytes(region.bytes)} download`),
        node("small", `OSM ${region.data_date} · ${formatMapBytes(region.expanded_bytes)} on device`));
      const job = data.job?.id === region.id ? data.job : null;
      if (job && ["downloading", "verifying", "installing"].includes(job.status)) {
        const progress = node("progress");
        progress.max = job.total_bytes || region.bytes;
        progress.value = job.downloaded_bytes || 0;
        progress.setAttribute("aria-label", `Download progress for ${region.name}`);
        info.append(progress, node("small", job.status === "downloading"
          ? `${formatMapBytes(job.downloaded_bytes)} / ${formatMapBytes(job.total_bytes)}`
          : job.status === "verifying" ? "Checking download…" : "Installing · keeping existing region until ready…"));
      } else if (job?.status === "error") info.append(node("small", job.error, "error"));
      else if (job?.status === "cancelled") info.append(node("small", "Cancelled · retry when ready"));
      else if (region.state === "update_available") info.append(node("small", "New pack available · existing region stays usable"));
      const action = regionAction(region, data.job);
      const button = node("button", action.label, "secondary compact");
      button.type = "button";
      button.disabled = action.disabled || pending;
      button.setAttribute("aria-label", `${action.label.toLowerCase()} ${region.name}`);
      button.addEventListener("click", async () => {
        pending = true; render();
        try {
          const response = await fetch(action.cancel ? "/api/offline-routing/catalogue/cancel"
            : `/api/offline-routing/catalogue/${encodeURIComponent(region.id)}/download`, {
            method: "POST", headers: {"content-type": "application/json"}, body: "{}",
          });
          const result = await response.json();
          if (!response.ok) throw new Error(result.detail || "Download could not start");
          await refresh();
        } catch (error) { status.textContent = error.message; }
        finally { pending = false; render(); }
      });
      row.append(info, button);
      list.append(row);
    }
    if (!filtered.length) list.textContent = data.regions?.length ? "No regions match your search." : "No published regions available. Refresh the catalogue or import a pack below.";
  };
  const refresh = async (remote = false) => {
    if (loading) return;
    loading = true;
    clearTimeout(timer);
    update.disabled = true;
    try {
      const response = await fetch(remote ? "/api/offline-routing/catalogue/refresh" : "/api/offline-routing/catalogue",
        remote ? {method: "POST", headers: {"content-type": "application/json"}, body: "{}"} : {cache: "no-store"});
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "Catalogue unavailable");
      data = result;
      status.textContent = result.error || (result.development ? "Local development packs · public downloads are not configured for this test." : "Walking + driving · select a region to save on this device.");
      render();
      if (result.job?.status === "ready") {
        const stamp = `${result.job.id}:${result.job.total_bytes}`;
        if (lastReady !== stamp) { lastReady = stamp; onReady?.(); }
      }
    } catch (error) { status.textContent = error.message; }
    finally {
      loading = false; update.disabled = false;
      if (["downloading", "verifying", "installing"].includes(data.job?.status)) {
        timer = setTimeout(() => refresh(), document.hidden ? 5000 : 1000);
      }
    }
  };
  search.addEventListener("input", render);
  update.addEventListener("click", () => refresh(true));
  return {refresh};
}
