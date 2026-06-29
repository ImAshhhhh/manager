"""SQLite layer for the Manager app.

Connection strategy: open a fresh connection per call, with WAL mode + 60s
busy timeout + autocommit isolation. Combined with a global write lock,
this eliminates 'database is locked' errors under concurrent threads.

Reads (no lock) can run in parallel thanks to WAL. Writes take the lock
to serialize, since SQLite only allows one writer at a time.
"""
import os
import sqlite3
import time
import threading
import secrets as _secrets

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "manager.db")

_write_lock = threading.RLock()
_init_done = False
_init_lock = threading.Lock()


def get_conn():
    """Fresh connection per call. Cheap in SQLite; avoids thread-safety issues."""
    conn = sqlite3.connect(DB_PATH, timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_pragmas_once():
    global _init_done
    with _init_lock:
        if _init_done:
            return
        conn = get_conn()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        finally:
            conn.close()
        _init_done = True


def init_db():
    _init_pragmas_once()
    conn = get_conn()
    try:
        conn.executescript(
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

            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                bot_token TEXT UNIQUE NOT NULL,
                api_id TEXT,
                api_hash TEXT,
                session_file TEXT,
                enabled INTEGER DEFAULT 1,
                is_primary INTEGER DEFAULT 0,
                created_at INTEGER,
                last_seen INTEGER
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
            conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    finally:
        conn.close()


# ---------- settings (reads) ----------
def get_setting(key, default=None):
    conn = get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def get_all_settings():
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


# ---------- settings (writes) ----------
def set_setting(key, value):
    with _write_lock:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, str(value)),
            )
        finally:
            conn.close()


# ---------- sessions (reads) ----------
def list_sessions():
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
    finally:
        conn.close()


def get_session(sid):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    finally:
        conn.close()


def get_session_by_phone(phone):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM sessions WHERE phone = ?", (phone,)).fetchone()
    finally:
        conn.close()


# ---------- sessions (writes) ----------
def insert_session(data):
    with _write_lock:
        now = int(time.time())
        auto_logout_at = None
        if get_setting("auto_logout_enabled") == "1":
            hours = int(get_setting("auto_logout_hours", "24") or 24)
            auto_logout_at = now + hours * 3600
        conn = get_conn()
        try:
            conn.execute(
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
            row = conn.execute(
                "SELECT * FROM sessions WHERE phone = ?", (data["phone"],)
            ).fetchone()
            _audit(conn, row["id"], row["phone"], "session_added", "Captured via login flow")
            return row
        finally:
            conn.close()


def update_session(sid, fields):
    if not fields:
        return
    with _write_lock:
        conn = get_conn()
        try:
            cols = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [sid]
            conn.execute(f"UPDATE sessions SET {cols} WHERE id = ?", vals)
        finally:
            conn.close()


def delete_session(sid):
    with _write_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            if row:
                conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
                _audit(conn, sid, row["phone"], "session_deleted", "Removed from Manager")
        finally:
            conn.close()


def set_protected(sid, val):
    update_session(sid, {"is_protected": 1 if val else 0})
    row = get_session(sid)
    if row:
        audit(sid, row["phone"], "protect_toggled", "ON" if val else "OFF")


def set_current(sid, val):
    with _write_lock:
        conn = get_conn()
        try:
            if val:
                conn.execute("UPDATE sessions SET is_current = 0")
            conn.execute("UPDATE sessions SET is_current = ? WHERE id = ?", (1 if val else 0, sid))
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
            if row:
                _audit(conn, sid, row["phone"], "current_toggled", "ON" if val else "OFF")
        finally:
            conn.close()


def set_email(sid, email):
    update_session(sid, {"email": email})
    row = get_session(sid)
    if row:
        audit(sid, row["phone"], "email_set", email or "(cleared)")


def set_notes(sid, notes):
    update_session(sid, {"notes": notes})


def mark_auto_logout_fired(sid):
    update_session(
        sid,
        {"auto_logout_fired": 1, "last_action": "auto_logout", "last_action_at": int(time.time())},
    )


def touch_last_seen(sid):
    update_session(sid, {"last_seen": int(time.time())})


def pending_auto_logouts(now):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM sessions WHERE auto_logout_fired = 0 AND auto_logout_at IS NOT NULL AND auto_logout_at <= ?",
            (now,),
        ).fetchall()
    finally:
        conn.close()


# ---------- audit ----------
def _audit(conn, session_id, phone, action, detail=""):
    """Internal: audit using an existing connection (no extra lock)."""
    conn.execute(
        "INSERT INTO audit_log(session_id, phone, action, detail, ts) VALUES (?, ?, ?, ?, ?)",
        (session_id, phone, action, detail, int(time.time())),
    )


def audit(session_id, phone, action, detail=""):
    with _write_lock:
        conn = get_conn()
        try:
            _audit(conn, session_id, phone, action, detail)
        finally:
            conn.close()


def list_audit(limit=100):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()


# ---------- bots (reads) ----------
def list_bots():
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM bots ORDER BY is_primary DESC, created_at ASC").fetchall()
    finally:
        conn.close()


def get_bot(bot_id):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
    finally:
        conn.close()


def get_bot_by_token(token):
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM bots WHERE bot_token = ?", (token,)).fetchone()
    finally:
        conn.close()


def get_primary_bot():
    conn = get_conn()
    try:
        return conn.execute("SELECT * FROM bots WHERE is_primary = 1").fetchone()
    finally:
        conn.close()


# ---------- bots (writes) ----------
def insert_bot(name, token, api_id=None, api_hash=None, is_primary=False, session_file=None):
    with _write_lock:
        now = int(time.time())
        if not session_file:
            session_file = f"bot_session_{now}_{_secrets.token_hex(4)}"
        conn = get_conn()
        try:
            c = conn.execute(
                """
                INSERT INTO bots(name, bot_token, api_id, api_hash, session_file, enabled, is_primary, created_at, last_seen)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (name, token, api_id, api_hash, session_file, 1 if is_primary else 0, now, now),
            )
            bot_id = c.lastrowid
            _audit(conn, None, None, "bot_added", f"name={name} id={bot_id} primary={is_primary}")
            return conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        finally:
            conn.close()


def update_bot(bot_id, fields):
    if not fields:
        return
    with _write_lock:
        conn = get_conn()
        try:
            cols = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [bot_id]
            conn.execute(f"UPDATE bots SET {cols} WHERE id = ?", vals)
        finally:
            conn.close()


def delete_bot(bot_id):
    with _write_lock:
        conn = get_conn()
        try:
            row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
            if row and row["is_primary"]:
                return False
            if row:
                conn.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
                _audit(conn, None, None, "bot_removed", f"name={row['name']} id={bot_id}")
            return True
        finally:
            conn.close()


def set_primary_bot(bot_id):
    with _write_lock:
        conn = get_conn()
        try:
            conn.execute("UPDATE bots SET is_primary = 0")
            conn.execute("UPDATE bots SET is_primary = 1 WHERE id = ?", (bot_id,))
            row = conn.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
            _audit(conn, None, None, "bot_primary_set", f"name={row['name'] if row else '?'} id={bot_id}")
        finally:
            conn.close()


def touch_bot_last_seen(bot_id):
    update_bot(bot_id, {"last_seen": int(time.time())})
