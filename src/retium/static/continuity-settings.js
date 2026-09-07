export function initContinuitySettings({apiUrl, escapeHtml, toast}) {
  const panel = document.getElementById('continuityPanel');
  const hostList = document.getElementById('continuityHosts');
  let timer;
  const request = async (path, body) => {
    const response = await fetch(apiUrl(`/api/team/continuity${path}`), {
      cache: 'no-store', ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Team continuity is unavailable');
    return data;
  };
  const showOffer = (offer) => {
    document.getElementById('backupOffer').classList.toggle('hidden', !offer);
    if (offer) {
      document.getElementById('backupPublicKey').value = offer.public_key;
      document.getElementById('backupFingerprint').textContent = offer.destination;
    }
  };
  const refresh = async () => {
    const state = await request('');
    document.getElementById('backupApprovalForm').classList.toggle('hidden', !state.owner);
    document.getElementById('offerBackup').classList.toggle('hidden', !!state.local_host);
    showOffer(state.offer);
    document.getElementById('continuitySummary').textContent = state.policy?.hosts.length > 1
      ? 'Approved hosts keep copies of this team. Devices reconnect through another ready host when needed.'
      : 'This team still depends on one host. Add a trusted backup before the host goes offline.';
    hostList.innerHTML = (state.policy?.hosts || []).map(host => {
      const peer = state.peers?.[host.destination];
      const synced = peer?.acknowledged_at || peer?.synced_at;
      return `<li><strong>${escapeHtml(host.label)}</strong><small>${host.destination === state.policy.preferred ? 'PREFERRED · ' : ''}${host.destination === state.local_host ? 'THIS DEVICE · ' : ''}${host.ready ? 'INITIAL SYNC COMPLETE' : 'WAITING FOR FIRST SYNC'}</small>
        <code>${escapeHtml(host.destination)}</code>
        ${synced ? `<small>Last sync ${escapeHtml(new Date(synced * 1000).toLocaleTimeString())}</small>` : ''}
        ${peer?.error ? `<small>${escapeHtml(peer.error)}</small>` : ''}
        ${state.owner && host.ready && host.destination !== state.policy.preferred ? `<button class="secondary compact" type="button" data-prefer-host="${escapeHtml(host.destination)}">HAND OVER TO THIS HOST</button>` : ''}</li>`;
    }).join('');
  };
  for (const button of document.querySelectorAll('[data-open-continuity]')) button.addEventListener('click', async () => {
    panel.classList.remove('hidden');
    try { await refresh(); } catch (error) { toast(error.message, true); }
    clearInterval(timer);
    timer = setInterval(() => refresh().catch(error => { document.getElementById('continuitySummary').textContent = error.message; }), 5000);
  });
  document.getElementById('closeContinuity').addEventListener('click', () => { panel.classList.add('hidden'); clearInterval(timer); });
  document.getElementById('offerBackup').addEventListener('click', async (event) => {
    event.target.disabled = true;
    try { showOffer(await request('/offer', {})); } catch (error) { toast(error.message, true); }
    finally { event.target.disabled = false; }
  });
  document.getElementById('copyBackupKey').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(document.getElementById('backupPublicKey').value); toast('Backup public key copied'); }
    catch { document.getElementById('backupPublicKey').select(); toast('Select and copy the public key'); }
  });
  document.getElementById('backupApprovalForm').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.target;
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    try {
      await request('/approve', {label: form.elements.label.value, public_key: form.elements.public_key.value.trim(), trust_host: form.elements.trust_host.checked});
      form.reset(); await refresh(); toast('Backup approved · waiting for initial sync');
    } catch (error) { toast(error.message, true); }
    finally { button.disabled = false; }
  });
  hostList.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-prefer-host]');
    if (!button) return;
    button.disabled = true;
    try { await request('/handover', {destination: button.dataset.preferHost}); await refresh(); toast('Preferred host updated · devices follow the signed host list'); }
    catch (error) { toast(error.message, true); button.disabled = false; }
  });
}
