"""
Scheduler de notificações proativas.
Loop background que verifica agendamentos no SQLite a cada 60s.
"""

import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


async def scheduler_loop(application, db, ask_claude_fn, conversations: dict,
                         save_conv_fn, admin_id: int, get_user_lock=None):
    """Background loop — verifica agendamentos a cada 60s.
    get_user_lock: async fn(user_id) -> Lock para serializar com handle_message.
    """
    fired_key = ""
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now()
            current_key = now.strftime("%Y-%m-%d %H:%M")
            if current_key == fired_key:
                continue

            schedules = db.schedule_list()
            if not schedules:
                continue

            weekday = now.weekday()
            for s in schedules:
                if s["hour"] != now.hour or s["minute"] != now.minute:
                    continue
                wdays = s.get("weekdays", "all")
                if wdays != "all":
                    allowed = {DAY_MAP.get(d.strip().lower(), -1) for d in wdays.split(",")}
                    if weekday not in allowed:
                        continue

                user_id = s.get("user_id", admin_id)
                logger.info(f"[scheduler] Disparando: {s['id']} para user {user_id}")

                try:
                    prompt = f"[Agendamento automático — {s['id']}] {s['message']}"

                    # Envia indicador visual antes de processar
                    status_msg = None
                    try:
                        status_msg = await application.bot.send_message(
                            chat_id=user_id, text="⏳ Pensando...",
                        )
                    except Exception:
                        pass

                    # Loop de typing indicator em paralelo
                    typing_stop = asyncio.Event()
                    async def _typing_loop():
                        while not typing_stop.is_set():
                            try:
                                await application.bot.send_chat_action(
                                    chat_id=user_id, action="typing"
                                )
                            except Exception:
                                pass
                            await asyncio.sleep(4)
                    typing_task = asyncio.create_task(_typing_loop())

                    # Adquire lock do user para serializar com handle_message
                    lock = await get_user_lock(user_id) if get_user_lock else None
                    if lock:
                        await lock.acquire()
                    try:
                        history = conversations.setdefault(user_id, [])
                        history.append({"role": "user", "content": prompt})
                        reply = await ask_claude_fn(list(history), user_id=user_id)
                        history.append({"role": "assistant", "content": reply})
                        save_conv_fn(user_id, history)
                    finally:
                        if lock:
                            lock.release()
                        typing_stop.set()
                        typing_task.cancel()
                        if status_msg:
                            try:
                                await application.bot.delete_message(
                                    chat_id=user_id, message_id=status_msg.message_id
                                )
                            except Exception:
                                pass

                    for i in range(0, max(len(reply), 1), 4096):
                        await application.bot.send_message(
                            chat_id=user_id, text=reply[i:i+4096],
                        )
                except Exception as e:
                    logger.error(f"[scheduler] Erro no agendamento {s['id']}: {e}")
                    try:
                        await application.bot.send_message(
                            chat_id=user_id,
                            text=f"⚠️ Erro no agendamento `{s['id']}`: {type(e).__name__}",
                        )
                    except Exception:
                        pass

            fired_key = current_key

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[scheduler] Erro no loop: {e}", exc_info=True)
            await asyncio.sleep(60)
