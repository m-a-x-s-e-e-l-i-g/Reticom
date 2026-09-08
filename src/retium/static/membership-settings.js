export function initMembershipSettings({apiUrl, escapeHtml, toast, onChange = () => {}}) {
  const panel = document.getElementById("membershipPanel"), list = document.getElementById("membershipList"), summary = document.getElementById("membershipSummary");
  let timer, generation = 0, localIdentity = "";
  const request = async body => {
    const response = await fetch(apiUrl("/api/team/members"), {cache: "no-store", ...(body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Membership is unavailable");
    return data;
  };
  const render = state => {
    summary.textContent = "Compare the full device identity with your teammate before approving. Callsigns are self-reported, not proof of identity.";
    const rows = [...state.requests.map(item => ({...item, status: "pending"})), ...state.members];
    list.innerHTML = rows.length ? rows.map(item => `<li><strong>${escapeHtml(item.callsign)}</strong><small>${escapeHtml(item.status.toUpperCase())}${item.previously_seen ? " · PREVIOUSLY SEEN, NOT APPROVED" : ""}</small><code>${escapeHtml(item.identity)}</code><div class="membership-actions">${item.status !== "approved" ? `<button type="button" class="secondary compact" data-member="${escapeHtml(item.identity)}" data-status="approved" data-callsign="${escapeHtml(item.callsign)}">APPROVE</button>` : `<button type="button" class="danger-button" data-member="${escapeHtml(item.identity)}" data-status="revoked">REVOKE</button>`}${item.status === "pending" ? `<button type="button" class="secondary compact" data-member="${escapeHtml(item.identity)}" data-status="rejected" data-callsign="${escapeHtml(item.callsign)}">REJECT</button>` : ""}${item.identity !== localIdentity ? `<button type="button" class="danger-button" data-member="${escapeHtml(item.identity)}" data-status="removed" data-callsign="${escapeHtml(item.callsign)}">REMOVE OPERATOR</button>` : ""}</div></li>`).join("") : "<li>No membership requests yet.</li>";
  };
  const refresh = async () => {
    const turn = ++generation;
    try { const state = await request(); if (turn === generation) render(state); }
    catch (error) { if (turn === generation) { summary.textContent = error.message; list.replaceChildren(); } }
  };
  for (const button of document.querySelectorAll("[data-open-membership]")) button.addEventListener("click", () => {
    panel.classList.remove("hidden"); void refresh(); clearInterval(timer);
    timer = setInterval(refresh, 5000);
    document.getElementById("closeMembership").focus();
  });
  const close = () => { panel.classList.add("hidden"); clearInterval(timer); ++generation; };
  document.getElementById("closeMembership").addEventListener("click", close);
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !panel.classList.contains("hidden")) close(); });
  list.addEventListener("click", async event => {
    const button = event.target.closest("[data-member]"); if (!button) return;
    const identity = button.dataset.member, status = button.dataset.status;
    if (status === "approved" && !window.confirm(`Have you verified this device identity with your teammate?\n\n${identity}\n\nApproving grants access to team intel and communications.`)) return;
    if (status === "removed" && !window.confirm(`Remove ${button.dataset.callsign || "this operator"} from this team? Their positions, markers and message history will be removed, and team access will be blocked. Copies on disconnected devices cannot be recalled.`)) return;
    if (status === "revoked" && !window.confirm("Revoke this device's team access? Previously downloaded data cannot be recalled.")) return;
    button.disabled = true; clearInterval(timer); ++generation;
    try { render(await request({identity, status, ...(button.dataset.callsign ? {callsign: button.dataset.callsign} : {})})); toast(status === "removed" ? "Operator removed" : `Membership ${status}`); await onChange(); }
    catch (error) { toast(error.message, true); button.disabled = false; }
    finally { if (!panel.classList.contains("hidden")) timer = setInterval(refresh, 5000); }
  });
  return {update(team, identity) {
    localIdentity = identity || "";
    const notice = document.getElementById("membershipNotice"), status = team.membership_status;
    notice.classList.toggle("hidden", !team.joined || !status || status === "approved");
    notice.textContent = status === "revoked" || status === "rejected" || status === "removed" ? `Team access ${status}. Contact the team owner. Local work stays on this device.` : "Awaiting team-owner approval. You can use the map locally; team transmissions remain queued.";
    document.getElementById("membershipDeviceIdentity").textContent = identity || "";
  }};
}
