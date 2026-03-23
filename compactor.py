"""
Compactação inteligente de contexto.

Quando o histórico atinge MAX_HISTORY, sumariza as mensagens mais antigas
usando um modelo rápido/barato em vez de simplesmente descartá-las.

Configuração via .env do bot:
  COMPACTION_ENABLED=true          (default: false — opt-in)
  COMPACTION_MODEL=google/gemini-2.0-flash-001
  COMPACTION_KEEP=10               (mensagens recentes a preservar intactas)
  COMPACTION_API_KEY=              (usa OPENROUTER_API_KEY se vazio)
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_SUMMARY_MARKER = "[RESUMO DA CONVERSA ANTERIOR]"

COMPACTION_MODEL   = os.environ.get("COMPACTION_MODEL", "google/gemini-2.0-flash-001")
COMPACTION_KEEP    = int(os.environ.get("COMPACTION_KEEP", "10"))
COMPACTION_API_KEY = os.environ.get("COMPACTION_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")


async def compact_history(messages: list, max_history: int, bot_name: str, db) -> list:
    """
    Compacta o histórico quando excede max_history.

    - Extrai o resumo anterior (se existir como primeira mensagem)
    - Sumariza as mensagens antigas
    - Retorna [resumo_novo] + mensagens_recentes

    Fallback: se a sumarização falhar, faz truncamento simples (comportamento original).
    """
    keep = int(os.environ.get("COMPACTION_KEEP", str(COMPACTION_KEEP)))
    if len(messages) <= max_history:
        return messages

    # Separa resumo anterior (se a primeira mensagem for um resumo compactado)
    previous_summary: Optional[str] = None
    working = messages
    if messages and isinstance(messages[0].get("content"), str) and messages[0]["content"].startswith(_SUMMARY_MARKER):
        previous_summary = messages[0]["content"][len(_SUMMARY_MARKER):].strip()
        working = messages[1:]

    # Mensagens a sumarizar (tudo menos as mais recentes)
    to_summarize = working[:-keep] if len(working) > keep else []
    recent = working[-keep:] if len(working) >= keep else working

    if not to_summarize:
        # Nada para compactar além do que já está no resumo
        return messages[-max_history:]

    summary_text = await _summarize(to_summarize, previous_summary, bot_name)
    if summary_text is None:
        # Fallback: truncamento simples
        logger.warning("[compactor] Sumarização falhou — usando truncamento simples")
        overflow = messages[:-max_history]
        if overflow and db:
            try:
                db.archive_conversation(0, overflow, bot_name)
            except Exception:
                pass
        return messages[-max_history:]

    summary_msg = _format_summary_message(summary_text)
    result = [summary_msg] + recent
    logger.info(f"[compactor] Histórico compactado: {len(messages)} → {len(result)} mensagens")
    return result


async def _summarize(messages: list, previous_summary: Optional[str], bot_name: str) -> Optional[str]:
    """
    Sumariza uma lista de mensagens usando um modelo rápido via OpenRouter.
    Retorna None em caso de falha (o chamador faz fallback para truncamento).
    """
    api_key = os.environ.get("COMPACTION_API_KEY", "") or os.environ.get("OPENROUTER_API_KEY", "")
    model = os.environ.get("COMPACTION_MODEL", COMPACTION_MODEL)

    if not api_key:
        logger.warning("[compactor] OPENROUTER_API_KEY não configurada — não é possível sumarizar")
        return None

    # Monta transcrição das mensagens
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, list):
            # Mensagens com mídia: extrai texto
            text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            content = " ".join(text_parts) or "[conteúdo de mídia]"
        lines.append(f"{role.upper()}: {str(content)[:500]}")

    transcript = "\n".join(lines)

    if previous_summary:
        prompt = (
            f"Você é um assistente que compacta históricos de conversas.\n\n"
            f"RESUMO ANTERIOR:\n{previous_summary}\n\n"
            f"NOVAS MENSAGENS A INCORPORAR:\n{transcript}\n\n"
            f"Crie um resumo único e atualizado que incorpore tanto o resumo anterior quanto as novas mensagens. "
            f"Seja conciso mas preserve todos os fatos importantes, decisões tomadas e contexto necessário para "
            f"continuar a conversa. Responda apenas com o resumo, sem preâmbulo."
        )
    else:
        prompt = (
            f"Você é um assistente que compacta históricos de conversas.\n\n"
            f"CONVERSA A RESUMIR:\n{transcript}\n\n"
            f"Crie um resumo conciso que preserve todos os fatos importantes, decisões tomadas e contexto necessário "
            f"para continuar a conversa. Responda apenas com o resumo, sem preâmbulo."
        )

    try:
        import aiohttp
        payload = {
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-Title": f"SMB-Claw/{bot_name}",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning(f"[compactor] OpenRouter {resp.status}: {body[:200]}")
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except ImportError:
        # aiohttp não disponível — tenta com httpx ou requests
        return await _summarize_fallback(prompt, api_key, model)
    except Exception as e:
        logger.warning(f"[compactor] Erro na sumarização: {e}")
        return None


async def _summarize_fallback(prompt: str, api_key: str, model: str) -> Optional[str]:
    """Fallback usando openai sdk (que já deve estar instalado para openrouter)."""
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
        )
        response = await client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"[compactor] Fallback também falhou: {e}")
        return None


def _format_summary_message(summary_text: str) -> dict:
    """Formata o resumo como mensagem de usuário para injetar no histórico."""
    return {
        "role": "user",
        "content": f"{_SUMMARY_MARKER}\n{summary_text}",
    }
