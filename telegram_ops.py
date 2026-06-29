"""Telethon wrapper for managing live Telegram accounts."""
import asyncio
import threading
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.account import (
    GetAuthorizationsRequest,
    ResetAuthorizationRequest,
    UpdatePasswordSettingsRequest,
    GetPasswordRequest,
)
from telethon.password import compute_check
import db

_loop = None
_loop_lock = threading.Lock()


def get_loop():
    global _loop
    with _loop_lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True).start()
        return _loop


def run_async(coro):
    loop = get_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=120)


def _client_from_session(session_string, api_id, api_hash):
    return TelegramClient(StringSession(session_string), api_id, api_hash)


async def _list_devices(session_string, api_id, api_hash):
    client = _client_from_session(session_string, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"error": "Session not authorized"}
        res = await client(GetAuthorizationsRequest())
        out = []
        for a in res.authorizations:
            out.append(
                {
                    "hash": a.hash,
                    "app_name": a.app_name,
                    "app_version": getattr(a, "app_version", ""),
                    "device_model": a.device_model,
                    "platform": a.platform,
                    "system_version": a.system_version,
                    "date_created": a.date_created.isoformat() if a.date_created else "",
                    "date_active": a.date_active.isoformat() if a.date_active else "",
                    "ip": a.ip,
                    "country": a.country,
                    "official_app": a.official_app,
                    "password_pending": a.password_pending,
                    "is_current": getattr(a, "current", False),
                }
            )
        return {"devices": out}
    finally:
        await client.disconnect()


async def _logout_device(session_string, api_id, api_hash, device_hash):
    client = _client_from_session(session_string, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"error": "Session not authorized"}
        await client(ResetAuthorizationRequest(hash=device_hash))
        return {"success": True}
    finally:
        await client.disconnect()


async def _logout_all_others(session_string, api_id, api_hash):
    client = _client_from_session(session_string, api_id, api_hash)
    await client.connect()
    try:
        res = await client(GetAuthorizationsRequest())
        killed = 0
        for a in res.authorizations:
            if not getattr(a, "current", False):
                try:
                    await client(ResetAuthorizationRequest(hash=a.hash))
                    killed += 1
                except Exception:
                    pass
        return {"success": True, "killed": killed}
    finally:
        await client.disconnect()


async def _get_2fa_status(session_string, api_id, api_hash):
    client = _client_from_session(session_string, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"error": "Session not authorized", "has_2fa": False}
        pwd = await client(GetPasswordRequest())
        return {"has_2fa": bool(pwd.has_password), "hint": pwd.hint or "", "has_recovery": bool(pwd.has_recovery)}
    finally:
        await client.disconnect()


async def _set_2fa(session_string, api_id, api_hash, new_password, hint, recovery_email, current_password=""):
    client = _client_from_session(session_string, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"error": "Session not authorized"}
        pwd = await client(GetPasswordRequest())
        old_pwd = None
        if pwd.has_password:
            if not current_password:
                return {"error": "Current 2FA password required to change"}
            old_pwd = compute_check(pwd, current_password)
            try:
                await client(UpdatePasswordSettingsRequest(old_pwd, pwd.new_empty()))
            except Exception:
                pass
        new_settings = pwd.new_empty()
        if new_password:
            new_settings.set_new_password(new_password, hint=hint or None, email=recovery_email or None)
        elif recovery_email:
            new_settings.set_email(recovery_email)
        await client(UpdatePasswordSettingsRequest(old_pwd, new_settings))
        return {"success": True}
    finally:
        await client.disconnect()


async def _get_me(session_string, api_id, api_hash):
    client = _client_from_session(session_string, api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {"error": "Session not authorized"}
        me = await client.get_me()
        return {"id": me.id, "first_name": me.first_name, "last_name": me.last_name, "username": me.username, "phone": me.phone}
    finally:
        await client.disconnect()


def _creds():
    api_id = db.get_setting("api_id")
    api_hash = db.get_setting("api_hash")
    if not api_id or not api_hash:
        raise RuntimeError("API_ID / API_HASH not set in Settings")
    return int(api_id), api_hash


# public sync wrappers
def list_devices(session_row):
    api_id, api_hash = _creds()
    return run_async(_list_devices(session_row["session_string"], api_id, api_hash))


def logout_device(session_row, device_hash):
    api_id, api_hash = _creds()
    return run_async(_logout_device(session_row["session_string"], api_id, api_hash, device_hash))


def logout_all_others(session_row):
    api_id, api_hash = _creds()
    return run_async(_logout_all_others(session_row["session_string"], api_id, api_hash))


def get_2fa_status(session_row):
    api_id, api_hash = _creds()
    return run_async(_get_2fa_status(session_row["session_string"], api_id, api_hash))


def set_2fa(session_row, new_password, hint, recovery_email, current_password=""):
    api_id, api_hash = _creds()
    return run_async(
        _set_2fa(
            session_row["session_string"],
            api_id,
            api_hash,
            new_password,
            hint,
            recovery_email,
            current_password,
        )
    )


def get_me(session_row):
    api_id, api_hash = _creds()
    return run_async(_get_me(session_row["session_string"], api_id, api_hash))


def force_logout_account(session_row):
    """Kill every authorization on this Telegram account (logout from all devices)."""
    return logout_all_others(session_row)
