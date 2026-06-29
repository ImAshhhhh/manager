"""Background thread: enforces 24h auto-logout for new sessions.

Rules:
- Runs every 60 seconds.
- For every session with auto_logout_fired = 0 AND auto_logout_at <= now:
    - If is_protected = 1  -> skip (current device is never auto-logged-out)
    - Else -> call Telethon to kill ALL authorizations on that account, then mark fired = 1
- Fires once per session. After firing, that session is never auto-logged-out again by this loop.
"""
import time
import threading
import logging
import db
import telegram_ops

logger = logging.getLogger(__name__)
_thread = None
_stop = threading.Event()


def _worker():
    while not _stop.is_set():
        try:
            now = int(time.time())
            for row in db.pending_auto_logouts(now):
                if row["is_protected"]:
                    db.mark_auto_logout_fired(row["id"])
                    db.audit(row["id"], row["phone"], "auto_logout_skipped", "Protected (current device)")
                    continue
                try:
                    res = telegram_ops.force_logout_account(row)
                    db.mark_auto_logout_fired(row["id"])
                    db.audit(
                        row["id"],
                        row["phone"],
                        "auto_logout_fired",
                        f"Killed {res.get('killed', 0)} devices",
                    )
                    logger.info(f"Auto-logout fired for {row['phone']}: {res}")
                except Exception as e:
                    logger.error(f"Auto-logout failed for {row['phone']}: {e}")
                    db.audit(row["id"], row["phone"], "auto_logout_error", str(e))
        except Exception as e:
            logger.error(f"Auto-logout loop error: {e}")
        _stop.wait(60)


def start():
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_worker, daemon=True, name="auto-logout")
    _thread.start()
    logger.info("Auto-logout worker started")


def stop():
    _stop.set()
