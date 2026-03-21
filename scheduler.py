"""
Scheduler de notificações proativas.
Loop background que verifica agendamentos no SQLite a cada 60s.
"""

import asyncio
import logging
from datetime import datetime

from telegram.error import RetryAfter

logger = logging.getLogger(__name__)

DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

# Horário do cleanup diário (UTC) — roda uma vez por dia ao redor da meia-noite
_CLEANUP_HOUR_UTC = 0
_cleanup_done_day = ""


async def scheduler_loop(application, db, ask_claude_fn, conversations: dict,
                         save_conv_fn, admin_id: int, get_user_lock=None,
                         injection_threshold: float = 0.0):
    """Background loop — verifica agendamentos a cada 60s.
    get_user_lock: async fn(user_id) -> Lock para serializar com handle_message.
    """
    fired_key = ""
    global _cleanup_done_day
    while True:
        try:
            await asyncio.sleep(60)
            now = datetime.now()
            current_key = now.strftime("%Y-%m-%d %H:%M")
            if current_key == fired_key:
                continue

            # ── Cleanup diário do action_log ──────────────────────────────────
            today_str = now.strftime("%Y-%m-%d")
            if now.hour == _CLEANUP_HOUR_UTC and _cleanup_done_day != today_str:
                _cleanup_done_day = today_str
                try:
                    deleted = db.cleanup_old_action_logs(keep_days=30)
                    if deleted:
                        logger.info(f"[scheduler] action_log cleanup: {deleted} registros removidos")
                except Exception as e:
                    logger.warning(f"[scheduler] Falha no cleanup do action_log: {e}")

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
                dom = s.get("day_of_month", 0)
                if dom and dom != now.day:
                    continue

                user_id = s.get("user_id", admin_id)
                logger.info(f"[scheduler] Disparando: {s['id']} para user {user_id}")

                try:
                    schedule_msg = s["message"]
                    # ── Detecção de injection na mensagem do agendamento ──────
                    if injection_threshold > 0:
                        try:
                            from security import detect_injection
                            _flagged, _reason, _score = detect_injection(schedule_msg, injection_threshold)
                            if _flagged:
                                logger.warning(
                                    f"[scheduler] Injection detectada em {s['id']} "
                                    f"(score={_score}, padrões={_reason})"
                                )
                                if admin_id:
                                    try:
                                        await application.bot.send_message(
                                            chat_id=admin_id,
                                            text=(
                                                f"🚨 *Injection em agendamento*\n\n"
                                                f"ID: `{s['id']}`\n"
                                                f"Score: `{_score}` · Padrões: `{_reason}`\n"
                                                f"Msg: `{schedule_msg[:200]}`"
                                            ),
                                            parse_mode="Markdown",
                                        )
                                    except Exception:
                                        pass
                        except Exception as ie:
                            logger.warning(f"[scheduler] Erro na detecção de injection: {ie}")

                    prompt = f"[Agendamento automático — {s['id']}] {schedule_msg}"

                    # Envia indicador visual antes de processar
                    status_msg = None
                    try:
                        status_msg = await application.bot.send_message(
                            chat_id=user_id, text="⏳ Pensando...",
                        )
                    except Exception:
                        pass

                    # Animação do "Pensando" — alterna frames a cada 1.2s
                    _thinking_stop = asyncio.Event()
                    _thinking_task = None
                    if status_msg:
                        _thinking_frames = [
                            "⏳ Pensando",
                            "⏳ Pensando.",
                            "⏳ Pensando..",
                            "⏳ Pensando...",
                            "⏳ Pensando..",
                            "⏳ Pensando.",
                        ]
                        async def _animate_thinking():
                            idx = 0
                            while not _thinking_stop.is_set():
                                await asyncio.sleep(1.2)
                                if _thinking_stop.is_set():
                                    break
                                try:
                                    await application.bot.edit_message_text(
                                        text=_thinking_frames[idx % len(_thinking_frames)],
                                        chat_id=user_id,
                                        message_id=status_msg.message_id,
                                    )
                                except Exception:
                                    pass
                                idx += 1
                        _thinking_task = asyncio.create_task(_animate_thinking())

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
                        _thinking_stop.set()
                        if _thinking_task:
                            _thinking_task.cancel()
                        if status_msg:
                            try:
                                await application.bot.delete_message(
                                    chat_id=user_id, message_id=status_msg.message_id
                                )
                            except Exception:
                                pass

                    chunks = [reply[i:i+4096] for i in range(0, max(len(reply), 1), 4096)]
                    for chunk in chunks:
                        for attempt in range(5):
                            try:
                                await application.bot.send_message(chat_id=user_id, text=chunk)
                                break
                            except RetryAfter as e:
                                wait = int(e.retry_after) + 1
                                logger.warning(f"[scheduler] Flood control — aguardando {wait}s antes de reenviar")
                                await asyncio.sleep(wait)
                        else:
                            logger.error(f"[scheduler] Falhou após 5 tentativas ao enviar chunk para {user_id}")
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
