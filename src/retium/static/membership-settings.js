export function membershipRequestLabel(count) {
  return `${count} join request${count === 1 ? "" : "s"} to review`;
}

export function initMembershipSettings({apiUrl, escapeHtml, toast, onChange = () => {}}) {
  const panel = document.getElementById("membershipPanel"), list = document.getElementById("membershipList"), summary = document.getElementById("membershipSummary");
  const review = document.getElementById("reviewMembershipRequests");
  const status = document.getElementById("membershipRequestStatus");
  const closeButton = document.getElementById("closeMembership");
  let timer, generation = 0, localIdentity = "", teamKey = "", loading = false, deciding = false, opener;
  const request = async body => {
    const response = await fetch(apiUrl("/api/team/members"), {cache: "no-store", ...(body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {})});
    const data = await response.json();
    if (!response.ok) throw Object.assign(new Error(data.detail || "Membership is unavailable"), {status: response.status});
    return data;
  };
  const showPending = count => {
    const label = count ? membershipRequestLabel(count) : "";
    if (review.textContent === label) return;
    review.textContent = label;
    review.classList.toggle("hidden", !count);
    status.textContent = label;
  };
  const render = state => {
    showPending(state.requests.filter(item => !item.previously_seen).length);
    if (panel.classList.contains("hidden")) return;
    summary.textContent = "Compare the full device identity with your teammate before approving. Callsigns are self-reported, not proof of identity.";
    const rows = [...state.requests.map(item => ({...item, status: "pending"})), ...state.members];
    list.innerHTML = rows.length ? rows.map(item => {
      const approved = item.status === "approved";
      const commandApproval = state.can_grant_admin && item.command && item.status === "pending";
      const identity = escapeHtml(item.identity), callsign = escapeHtml(item.callsign);
      return `<li><strong>${callsign}</strong><small>${escapeHtml(item.status.toUpperCase())}${item.admin ? " · COMMAND ADMIN" : item.command ? " · REQUESTING COMMAND ADMIN" : ""}${item.previously_seen ? " · PREVIOUSLY SEEN, NOT APPROVED" : ""}</small><code>${identity}</code><div class="membership-actions">
        ${approved ? `<button type="button" class="danger-button" data-member="${identity}" data-status="revoked">Revoke access</button>`
          : `<button type="button" class="secondary compact" data-member="${identity}" data-status="approved" data-callsign="${callsign}" ${commandApproval ? 'data-admin="true"' : ""}>${commandApproval ? "Approve as Command admin" : "Approve"}</button>`}
        ${approved && state.can_grant_admin ? `<button type="button" class="secondary compact" data-member="${identity}" data-status="approved" data-admin="${!item.admin}">${item.admin ? "Remove admin rights" : "Grant Command admin"}</button>` : ""}
        ${item.status === "pending" ? `<button type="button" class="secondary compact" data-member="${identity}" data-status="rejected" data-callsign="${callsign}">Reject</button>` : ""}
        ${item.identity !== localIdentity ? `<button type="button" class="danger-button" data-member="${identity}" data-status="removed" data-callsign="${callsign}">Remove operator</button>` : ""}
      </div></li>`;
    }).join("") : "<li>No membership requests yet.</li>";
  };
  const refresh = async () => {
    if (!teamKey || loading || deciding) return;
    loading = true;
    const turn = ++generation;
    try {
      const state = await request();
      if (turn === generation) render(state);
    } catch (error) {
      if (turn === generation) {
        if (error.status === 403) showPending(0);
        if (!panel.classList.contains("hidden")) { summary.textContent = error.message; list.replaceChildren(); }
      }
    } finally {
      if (turn === generation) loading = false;
    }
  };
  const open = button => {
    if (!teamKey) return;
    opener = button;
    panel.classList.remove("hidden");
    void refresh();
    clearInterval(timer);
    timer = setInterval(refresh, 5000);
    closeButton.focus();
  };
  for (const button of document.querySelectorAll("[data-open-membership]")) button.addEventListener("click", () => open(button));
  const close = () => {
    panel.classList.add("hidden");
    clearInterval(timer);
    if (opener) {
      const focusTarget = opener.classList.contains("hidden") ? document.getElementById("adminSettingsToggle") : opener;
      focusTarget.focus();
      opener = null;
    }
  };
  closeButton.addEventListener("click", close);
  document.addEventListener("keydown", event => { if (event.key === "Escape" && !panel.classList.contains("hidden")) close(); });
  list.addEventListener("click", async event => {
    const button = event.target.closest("[data-member]"); if (!button || deciding || !teamKey) return;
    const identity = button.dataset.member, status = button.dataset.status;
    const admin = button.dataset.admin === undefined ? undefined : button.dataset.admin === "true";
    if (admin === false && !window.confirm("Remove this operator's Command admin rights?")) return;
    if (status === "approved" && admin !== false && !window.confirm(`Have you verified this device identity with your teammate?\n\n${identity}\n\n${admin ? "This grants access to team data, map moderation, and team settings." : "This grants access to team intel and communications."}`)) return;
    if (status === "removed" && !window.confirm(`Remove ${button.dataset.callsign || "this operator"} from this team? Their positions, markers and message history will be removed, and team access will be blocked. Copies on disconnected devices cannot be recalled.`)) return;
    if (status === "revoked" && !window.confirm("Revoke this device's team access? Previously downloaded data cannot be recalled.")) return;
    button.disabled = true;
    deciding = true;
    loading = false;
    const turn = ++generation;
    try {
      const state = await request({identity, status, ...(admin === undefined ? {} : {admin}), ...(button.dataset.callsign ? {callsign: button.dataset.callsign} : {})});
      if (turn === generation) render(state);
      toast(admin === true ? "Command admin granted" : admin === false ? "Command admin rights removed"
        : status === "removed" ? "Operator removed" : `Membership ${status}`);
      await onChange();
    }
    catch (error) { toast(error.message, true); button.disabled = false; }
    finally { deciding = false; if (turn !== generation) void refresh(); }
  });
  return {open, refresh, update(team, identity, canManageTeam) {
    localIdentity = identity || "";
    const notice = document.getElementById("membershipNotice"), membershipStatus = team.membership_status;
    notice.classList.toggle("hidden", !team.joined || !membershipStatus || membershipStatus === "approved");
    notice.textContent = membershipStatus === "revoked" || membershipStatus === "rejected" || membershipStatus === "removed" ? `Team access ${membershipStatus}. Contact the team owner. Local work stays on this device.` : "Awaiting team-owner approval. You can use the map locally; team transmissions remain queued.";
    document.getElementById("membershipDeviceIdentity").textContent = identity || "";
    const key = canManageTeam ? `${localIdentity}:${team.destination || ""}` : "";
    if (key === teamKey) return;
    teamKey = key;
    ++generation;
    loading = false;
    showPending(0);
    close();
    summary.textContent = "Loading membership requests…";
    list.replaceChildren();
    void refresh();
  }};
}
