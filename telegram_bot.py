"""BotManager — runs multiple Telegram bots in parallel.

Each bot has its own:
- Telethon client + asyncio loop + thread
- Session file (so changing the bot token starts a fresh session)
- contacts dict (keyed by telegram user_id, which is global across bots)

Token-change detection: a `<session_file>.token` file stores the last
token that was used with this session. If the current token differs,
the session file is deleted so Telethon starts fresh.
"""
import os
import time
import asyncio
import threading
import logging
from telethon import TelegramClient, errors

logger = logging.getLogger(__name__)

# Global contacts store: telegram_user_id (str) -> phone (E.164 with +)
# Shared across all bots because Telegram user IDs are global.
contacts = {}


class BotEntry:
    def __init__(self, bot_id, name, token, api_id, api_hash, session_file):
        self.bot_id = bot_id
        self.name = name
        self.token = token
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_file = session_file
        self.client = None
        self.loop = None
        self.thread = None
        self.started_at = None
        self.last_seen = None
        self.last_error = None
        self.contacts_count = 0
        self.messages_received = 0
        self._stop = False

    @property
    def running(self):
        return self.thread is not None and self.thread.is_alive()


class BotManager:
    def __init__(self):
        self._bots = {}  # bot_id -> BotEntry
        self._lock = threading.Lock()

    def _session_token_file(self, session_file):
        return session_file + ".token"

    def _check_token_change(self, entry):
        """Delete session file if the token has changed since last run."""
        tok_file = self._session_token_file(entry.session_file)
        try:
            if os.path.exists(tok_file):
                with open(tok_file) as f:
                    stored = f.read().strip()
                if stored != entry.token:
                    logger.info(f"[bot {entry.bot_id}] token changed — wiping session file")
                    for ext in ("", ".session", ".session-journal"):
                        p = entry.session_file + ext
                        if os.path.exists(p):
                            try:
                                os.remove(p)
                            except Exception:
                                pass
            with open(tok_file, "w") as f:
                f.write(entry.token)
        except Exception as e:
            logger.warning(f"[bot {entry.bot_id}] token check failed: {e}")

    async def _run_bot(self, entry):
        entry.loop = asyncio.get_running_loop()
        try:
            self._check_token_change(entry)
            client = TelegramClient(entry.session_file, entry.api_id, entry.api_hash)
            await client.start(bot_token=entry.token)
            entry.client = client
            entry.started_at = int(time.time())
            entry.last_error = None
            logger.info(f"[bot {entry.bot_id}] '{entry.name}' polling started")

            @client.on(events.NewMessage(pattern=r"^/start"))
            async def _start(event):
                try:
                    await event.respond("👇")
                    entry.messages_received += 1
                    entry.last_seen = int(time.time())
                except Exception as e:
                    logger.error(f"[bot {entry.bot_id}] start reply error: {e}")

            @client.on(events.NewMessage)
            async def _all(event):
                msg = event.message
                media = getattr(msg, "media", None)
                if not media:
                    return
                uid = getattr(media, "user_id", None)
                phone = getattr(media, "phone_number", None)
                if uid and phone:
                    contacts[str(uid)] = phone if phone.startswith("+") else "+" + phone
                    entry.contacts_count += 1
                    entry.last_seen = int(time.time())
                    logger.info(f"[bot {entry.bot_id}] contact: {uid} -> {phone}")

            while not entry._stop:
                await asyncio.sleep(1)

            try:
                await client.disconnect()
            except Exception:
                pass
        except errors.ApiIdInvalidError:
            entry.last_error = "API_ID/API_HASH invalid"
            logger.error(f"[bot {entry.bot_id}] {entry.last_error}")
        except errors.AuthKeyError:
            entry.last_error = "Auth key error — session file may be corrupt"
            logger.error(f"[bot {entry.bot_id}] {entry.last_error}")
            # wipe so next start is fresh
            for ext in ("", ".session", ".session-journal"):
                p = entry.session_file + ext
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
        except Exception as e:
            entry.last_error = str(e)
            logger.error(f"[bot {entry.bot_id}] crashed: {e}")

    def _thread_main(self, entry):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_bot(entry))
        except Exception as e:
            entry.last_error = str(e)
            logger.error(f"[bot {entry.bot_id}] thread error: {e}")

    def start(self, bot_id, name, token, api_id, api_hash, session_file):
        with self._lock:
            existing = self._bots.get(bot_id)
            if existing and existing.running:
                return False, "already running"
            if existing:
                # reuse the entry but reset transient state
                existing.token = token
                existing.name = name
                existing._stop = False
                existing.last_error = None
                entry = existing
            else:
                entry = BotEntry(bot_id, name, token, int(api_id), api_hash, session_file)
                self._bots[bot_id] = entry
            entry.thread = threading.Thread(
                target=self._thread_main,
                args=(entry,),
                daemon=True,
                name=f"bot-{bot_id}",
            )
            entry.thread.start()
            return True, "started"

    def stop(self, bot_id):
        with self._lock:
            entry = self._bots.get(bot_id)
            if not entry or not entry.running:
                return False, "not running"
            entry._stop = True
        # outside lock: wait for thread
        if entry.thread:
            entry.thread.join(timeout=10)
        try:
            if entry.client:
                fut = asyncio.run_coroutine_threadsafe(entry.client.disconnect(), entry.loop)
                fut.result(timeout=5)
        except Exception:
            pass
        return True, "stopped"

    def restart(self, bot_id, name, token, api_id, api_hash, session_file):
        self.stop(bot_id)
        return self.start(bot_id, name, token, api_id, api_hash, session_file)

    def remove(self, bot_id):
        self.stop(bot_id)
        with self._lock:
            self._bots.pop(bot_id, None)

    def status(self, bot_id):
        e = self._bots.get(bot_id)
        if not e:
            return None
        return {
            "running": e.running,
            "started_at": e.started_at,
            "last_seen": e.last_seen,
            "last_error": e.last_error,
            "contacts_count": e.contacts_count,
            "messages_received": e.messages_received,
        }

    def all_status(self):
        out = {}
        for bid, e in self._bots.items():
            out[bid] = {
                "running": e.running,
                "started_at": e.started_at,
                "last_seen": e.last_seen,
                "last_error": e.last_error,
                "contacts_count": e.contacts_count,
                "messages_received": e.messages_received,
            }
        return out

    def send_to_channel(self, bot_id, channel_id, message):
        """Send a log message via the given bot's client."""
        e = self._bots.get(bot_id)
        if not e or not e.client or not e.loop or not channel_id:
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(
                e.client.send_message(int(channel_id), message, parse_mode="md"),
                e.loop,
            )
            fut.result(timeout=15)
            return True
        except Exception as ex:
            logger.error(f"[bot {bot_id}] channel log error: {ex}")
            return False

    def send_via_any_running(self, channel_id, message):
        """Send via any running bot (prefers primary). Used for login-flow logs."""
        for bid, e in self._bots.items():
            if e.running and e.client and e.loop:
                if self.send_to_channel(bid, channel_id, message):
                    return True
        return False


bot_manager = BotManager()
