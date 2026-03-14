"""
Claude Multi-Bot Framework — com sistema de memória em camadas
Uso: python3 bot.py --bot-dir /home/ubuntu/claude-bots/bots/assistente

Arquitetura modular:
  bot.py        — Core: config, handlers, main loop
  db.py         — Persistência SQLite (WAL mode)
  security.py   — Shell safety, path traversal
  scheduler.py  — Notificações proativas
  tools/        — Ferramentas modulares

Provedores suportados (variável PROVIDER no .env do bot):
  anthropic     — Claude via API Anthropic ou OAuth do Claude Code (padrão)
  openrouter    — Qualquer modelo via OpenRouter (requer OPENROUTER_API_KEY)

ATENÇÃO: ao adicionar ferramentas, comandos ou camadas de memória,
leia /home/ubuntu/claude-bots/CLAUDE.md e siga os checklists.
"""

import os
import re
import html as _html
import sys
import json
import base64
import argparse
import logging
import time
import asyncio
import tempfile
import traceback
from datetime import date, timedelta, datetime
from pathlib import Path

import anthropic
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)
from telegram.constants import ChatAction

from db import BotDB
import tools as tool_registry
from tools.tasks import task_status_emoji
import scheduler as sched_mod

# ── Argumentos ────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--bot-dir", required=True)
args = parser.parse_args()

BOT_DIR = Path(args.bot_dir).resolve()
if not BOT_DIR.exists():
    print(f"Erro: '{BOT_DIR}' não encontrado.")
    sys.exit(1)

BASE_DIR = BOT_DIR.parent.parent  # /home/ubuntu/claude-bots

# ── Carrega config.global, .env e secrets.env ─────────────────────────────────

def _load_env_file(path: Path, override: bool = False) -> None:
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if override:
                    os.environ[key] = value
                else:
                    os.environ.setdefault(key, value)

_load_env_file(BASE_DIR / "config.global")
_load_env_file(BASE_DIR / "secrets.global", override=True)  # credenciais globais compartilhadas
_load_env_file(BOT_DIR / ".env", override=True)
_load_env_file(BOT_DIR / "secrets.env", override=True)

# ── Configurações ─────────────────────────────────────────────────────────────

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
BOT_NAME           = os.environ.get("BOT_NAME", BOT_DIR.name)
MODEL              = os.environ.get("MODEL", "claude-opus-4-6")
MAX_HISTORY        = int(os.environ.get("MAX_HISTORY", "20"))
ADMIN_ID           = int(os.environ.get("ADMIN_ID") or "0")
ACCESS_MODE        = os.environ.get("ACCESS_MODE", "approval").lower()
PROVIDER           = os.environ.get("PROVIDER", "anthropic").lower()  # anthropic | openrouter

_tools_raw    = os.environ.get("TOOLS", "none").lower()
ENABLED_TOOLS = set() if _tools_raw == "none" else {t.strip() for t in _tools_raw.split(",")}

GROUP_MODE = os.environ.get("GROUP_MODE", "always").lower()  # always | mention_only

WORK_DIR  = Path(os.environ.get("WORK_DIR", str(BOT_DIR / "workspace")))
MEM_DIR   = BOT_DIR / "memory"
MEM_DIR.mkdir(exist_ok=True)

DB_URL       = os.environ.get("DB_URL", "")
GIT_TOKEN    = os.environ.get("GIT_TOKEN", "")
GIT_USER     = os.environ.get("GIT_USER", "")
GIT_EMAIL    = os.environ.get("GIT_EMAIL", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") or GIT_TOKEN

_PROTECTED_PATHS = [
    str(BASE_DIR / "config.global"),
    str(BOT_DIR / ".env"), str(BOT_DIR / "secrets.env"),
    str(Path.home() / ".claude" / ".credentials.json"),
    "/etc/passwd", "/etc/shadow", "/root", str(Path.home() / ".ssh"),
]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format=f"[{BOT_NAME}] %(asctime)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(BOT_NAME)

# ── Database SQLite ───────────────────────────────────────────────────────────

db = BotDB(BOT_DIR / "bot_data.db")
db.migrate_from_json(BOT_DIR)  # migra arquivos JSON legados se existirem

# ── Config dict para passar aos módulos ───────────────────────────────────────

def _append_daily_log(content: str, day=None):
    """Adiciona entrada ao diário do dia (com timestamp)."""
    d = day or date.today()
    path = MEM_DIR / f"{d.isoformat()}.md"
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n### {timestamp}\n{content.strip()}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
    path.chmod(0o600)


# Fila de arquivos pendentes para envio ao usuário (preenchida pelo tool send_telegram_file)
_pending_files: dict[int, list[dict]] = {}

TOOL_CONFIG = {
    "BOT_DIR": BOT_DIR, "BASE_DIR": BASE_DIR, "BOT_NAME": BOT_NAME,
    "WORK_DIR": WORK_DIR, "MEM_DIR": MEM_DIR,
    "DB_URL": DB_URL, "GIT_TOKEN": GIT_TOKEN, "GIT_USER": GIT_USER,
    "GIT_EMAIL": GIT_EMAIL, "GITHUB_TOKEN": GITHUB_TOKEN,
    "PROTECTED_PATHS": _PROTECTED_PATHS,
    "append_daily_log": _append_daily_log,
    "pending_files": _pending_files,
}

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOL_DEFINITIONS = tool_registry.build_definitions(ENABLED_TOOLS, WORK_DIR, BASE_DIR, BOT_NAME)

# ═══════════════════════════════════════════════════════════════════════════════
# SISTEMA DE MEMÓRIA
# ═══════════════════════════════════════════════════════════════════════════════

def _read_file_safe(path: Path, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8").strip()
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n... (truncado — {len(content)} chars total)"
    return content


def build_context() -> str:
    parts = []
    # Identidade do bot — injetada antes do soul.md para que o modelo saiba seu nome
    parts.append(f"# Identidade\nSeu nome é **{BOT_NAME}**. Apresente-se sempre como {BOT_NAME}, nunca como \"Claude\" ou qualquer outro nome.")
    # Instruções globais — compartilhadas por todos os bots
    global_ctx = _read_file_safe(BASE_DIR / "context.global")
    if global_ctx:
        parts.append(global_ctx)
    soul = _read_file_safe(BOT_DIR / "soul.md")
    if soul:
        parts.append(soul)
    user_md = _read_file_safe(BOT_DIR / "USER.md")
    if user_md:
        parts.append(f"---\n## Sobre o usuário\n{user_md}")
    memory_md = _read_file_safe(BOT_DIR / "MEMORY.md")
    if memory_md:
        parts.append(f"---\n## Memória de longo prazo\n{memory_md}")
    today = date.today().isoformat()
    today_log = _read_file_safe(MEM_DIR / f"{today}.md")
    if today_log:
        parts.append(f"---\n## Memória de hoje ({today})\n{today_log}")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    yesterday_log = _read_file_safe(MEM_DIR / f"{yesterday}.md")
    if yesterday_log:
        parts.append(f"---\n## Memória de ontem ({yesterday})\n{yesterday_log}")
    parts.append(
        "---\n"
        "## Instruções de memória\n"
        "- Use a ferramenta `memory_write` para registrar eventos importantes na memória diária.\n"
        "- Use `memory_write` com `target=long_term` para salvar decisões que devem persistir para sempre.\n"
        "- Use `state_write`/`state_read` para controle de estado em automações (evitar duplicidade).\n"
        "- Registre na memória diária: decisões tomadas, problemas encontrados, tarefas concluídas, contexto relevante.\n"
        "- Registre no longo prazo: preferências do usuário, padrões obrigatórios, decisões estruturais."
    )
    return "\n\n".join(parts)


def get_system_prompt() -> str:
    prompt = build_context()
    if ENABLED_TOOLS:
        prompt += f"\n\n---\n## Ferramentas disponíveis\n{', '.join(sorted(ENABLED_TOOLS))}. Use quando necessário."
    # Tarefas ativas no contexto
    active = [t for t in (db.tasks_for_user(0) if False else [])  # placeholder
              if t["status"] in ("in_progress", "paused", "pending")]
    # Busca tarefas ativas de todos os usuários via query direta
    try:
        rows = db._conn.execute(
            "SELECT * FROM tasks WHERE status IN ('in_progress', 'paused', 'pending') "
            "ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        active = [db._row_to_task(r) for r in rows]
    except Exception:
        active = []
    if active:
        lines = ["---", "## Tarefas ativas pendentes"]
        for t in active[:5]:
            emoji = task_status_emoji(t["status"])
            step_info = f"passo {t['current_step']+1}/{len(t['steps'])}" if t["steps"] else ""
            lines.append(f"- [{t['id']}] {emoji} **{t['title']}** {step_info}")
            if t["progress"]:
                lines.append(f"  Progresso: {t['progress']}")
        lines.append("Use task_update para atualizar o progresso a cada passo.")
        prompt += "\n\n" + "\n".join(lines)
    # Agendamentos proativos no contexto (visível mesmo em modo claude-cli)
    try:
        schedules = db.schedule_list()
    except Exception:
        schedules = []
    if schedules:
        lines = ["---", "## Notificações proativas agendadas (SQLite — fonte autoritativa)"]
        lines.append("Use estes dados diretamente. NÃO use CronList nem outras ferramentas para listar notificações proativas.")
        for s in schedules:
            brt_hour = (s["hour"] - 3) % 24
            lines.append(f"- [{s['id']}] {brt_hour:02d}:{s['minute']:02d} BRT ({s['weekdays']}) → {s['message'][:80]}")
        prompt += "\n\n" + "\n".join(lines)
    else:
        prompt += "\n\n---\n## Notificações proativas agendadas (SQLite)\nNenhuma. NÃO use CronList para verificar — esta informação já é a fonte autoritativa."
    return prompt


# ── Aprovação de usuários ─────────────────────────────────────────────────────

approved_users: dict[int, dict] = db.load_approved()  # cache em memória
pending: dict[int, dict] = {}


def _sync_approve(uid: int, info: dict):
    """Aprova user no cache + DB."""
    approved_users[uid] = info
    db.approve_user(uid, info.get("name", ""), info.get("username", ""))


def _sync_revoke(uid: int):
    """Revoga user no cache + DB."""
    approved_users.pop(uid, None)
    db.revoke_user(uid)

# ── Clientes de API (Async) ───────────────────────────────────────────────────

_CLAUDE_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"

def _make_async_client() -> anthropic.AsyncAnthropic:
    """Cria cliente Anthropic ASYNC usando API key ou OAuth do Claude Code.
    Inclui retry automático com backoff para 429/500/503."""
    kwargs = {"max_retries": 3}
    if ANTHROPIC_API_KEY:
        return anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY, **kwargs)
    if _CLAUDE_CREDS_PATH.exists():
        try:
            with open(_CLAUDE_CREDS_PATH) as f:
                creds = json.load(f)
            token = creds.get("claudeAiOauth", {}).get("accessToken", "")
            if token:
                return anthropic.AsyncAnthropic(auth_token=token, **kwargs)
        except Exception as e:
            logger.warning(f"Falha ao ler credenciais do Claude Code: {e}")
    raise RuntimeError(
        "Sem credenciais Anthropic. Configure ANTHROPIC_API_KEY no config.global "
        "ou faça login no Claude Code (`claude` no terminal)."
    )

# Sessões da CLI por user_id (PROVIDER=claude-cli)
_cli_sessions: dict[int, str] = {}
# Processos CLI ativos por user_id (para suporte ao /cancel)
_cli_procs: dict[int, asyncio.subprocess.Process] = {}

def _make_openrouter_client():
    """Cria cliente OpenAI-compatible apontando para OpenRouter."""
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
    )

def _extract_reply_context(message) -> str:
    """Extrai contexto de uma mensagem citada (reply) e retorna prefixo para injetar no texto."""
    reply = getattr(message, "reply_to_message", None)
    if not reply:
        return ""
    sender = reply.from_user
    name = sender.full_name if sender else "?"
    quoted = (
        reply.text
        or reply.caption
        or (f"[sticker: {reply.sticker.emoji or '?'}]" if reply.sticker else None)
        or ("[voz]" if reply.voice else None)
        or ("[foto]" if reply.photo else None)
        or ("[documento]" if reply.document else None)
        or ("[vídeo]" if (reply.video or reply.video_note) else None)
        or "[mensagem]"
    )
    if len(quoted) > 500:
        quoted = quoted[:500] + "..."
    return f'[Em resposta a "{name}": "{quoted}"]\n'


def _extract_document_text(file_path: str, filename: str, max_chars: int = 8000) -> str:
    """Extrai texto de um documento para injetar como contexto."""
    ext = Path(filename).suffix.lower()
    text_exts = {
        ".txt", ".csv", ".json", ".py", ".js", ".ts", ".html", ".css", ".md",
        ".yaml", ".yml", ".xml", ".sh", ".bat", ".java", ".c", ".cpp", ".h",
        ".rs", ".go", ".rb", ".php", ".sql", ".env", ".cfg", ".ini", ".toml",
        ".log", ".rst", ".tex",
    }
    if ext in text_exts:
        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars + 100)
            if len(content) > max_chars:
                content = content[:max_chars] + "\n... (truncado)"
            return content
        except Exception as e:
            return f"[Erro ao ler: {e}]"
    if ext == ".pdf":
        try:
            import pdfplumber
            text_parts = []
            with pdfplumber.open(file_path) as pdf:
                for page in pdf.pages:
                    t = page.extract_text()
                    if t:
                        text_parts.append(t)
                    if sum(len(p) for p in text_parts) >= max_chars:
                        break
            full = "\n".join(text_parts)
            if len(full) > max_chars:
                full = full[:max_chars] + "\n... (truncado)"
            return full or "[PDF sem texto extraível]"
        except ImportError:
            size = Path(file_path).stat().st_size
            return f"[PDF — {size // 1024} KB — instale pdfplumber para extração de texto]"
        except Exception as e:
            return f"[Erro ao ler PDF: {e}]"
    # Binário desconhecido
    size = Path(file_path).stat().st_size
    return f"[Arquivo binário — {size // 1024} KB]"


async def _describe_image_for_cli(image_path: str) -> str:
    """Descreve imagem via Anthropic ou OpenRouter (para provider claude-cli que não suporta visão direta)."""
    try:
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()
        if ANTHROPIC_API_KEY or _CLAUDE_CREDS_PATH.exists():
            client = _make_async_client()
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                    {"type": "text", "text": "Descreva esta imagem em detalhes em português."},
                ]}],
            )
            return next((b.text for b in response.content if b.type == "text"), "[imagem]")
        elif OPENROUTER_API_KEY:
            from openai import AsyncOpenAI
            or_client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY)
            response = await or_client.chat.completions.create(
                model="google/gemini-2.0-flash",
                max_tokens=512,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_data}"}},
                    {"type": "text", "text": "Descreva esta imagem em detalhes em português."},
                ]}],
            )
            return response.choices[0].message.content or "[imagem]"
    except Exception as e:
        logger.warning(f"[vision/cli] Erro ao descrever imagem: {e}")
    return "[imagem não pôde ser descrita]"


def _has_media_content(content) -> bool:
    """True se content é lista com blocos de imagem (vision), para incluir na conversa OpenRouter."""
    if not isinstance(content, list):
        return False
    return any(isinstance(i, dict) and i.get("type") == "image" for i in content)


def _convert_content_for_openai(content):
    """Converte content formato Anthropic → formato OpenAI (texto + image_url)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    result = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            result.append({"type": "text", "text": item.get("text", "")})
        elif item.get("type") == "image":
            src = item.get("source", {})
            if src.get("type") == "base64":
                media_type = src.get("media_type", "image/jpeg")
                data = src.get("data", "")
                result.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}})
    return result or str(content)


def _anthropic_tools_to_openai(tools: list) -> list:
    """Converte definições de ferramentas do formato Anthropic para OpenAI."""
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        })
    return result

# ── Conversas com lock per-user ───────────────────────────────────────────────

conversations: dict[int, list] = {}
_user_locks: dict[int, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()  # protege criação de novos user locks
_bot_start_time = time.monotonic()


async def _get_user_lock(user_id: int) -> asyncio.Lock:
    """Retorna lock dedicado para um user. Cria se não existir."""
    if user_id not in _user_locks:
        async with _locks_lock:
            if user_id not in _user_locks:  # double-check
                _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _load_conversations_from_db():
    """Carrega todas as conversas do SQLite na inicialização."""
    rows = db._conn.execute("SELECT user_id, messages FROM conversations").fetchall()
    for row in rows:
        conversations[int(row["user_id"])] = json.loads(row["messages"])

# ── Acesso ────────────────────────────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return ADMIN_ID != 0 and uid == ADMIN_ID

def has_access(uid: int) -> bool:
    if ACCESS_MODE == "open": return True
    if is_admin(uid): return True
    if ACCESS_MODE == "closed": return False
    return uid in approved_users

async def request_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    uid = user.id
    if uid in pending:
        await update.message.reply_text("⏳ Solicitação já enviada. Aguarde aprovação.")
        return
    pending[uid] = {"name": user.full_name, "username": f"@{user.username}" if user.username else ""}
    logger.info(f"Acesso solicitado: {uid} ({user.full_name})")
    if ADMIN_ID == 0:
        await update.message.reply_text("⚠️ Sem admin configurado.")
        return
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Aprovar", callback_data=f"approve:{uid}"),
        InlineKeyboardButton("❌ Negar",   callback_data=f"deny:{uid}"),
    ]])
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"🔔 *Solicitação — {BOT_NAME}*\n\n👤 {user.full_name}\n🆔 `{uid}`\n📎 {pending[uid]['username']}",
        parse_mode="Markdown", reply_markup=kb,
    )
    await update.message.reply_text("📩 Solicitação enviada. Você será notificado quando aprovado.")

async def callback_approval(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("Sem permissão.", show_alert=True); return
    action, uid_str = query.data.split(":", 1)
    uid = int(uid_str)
    info = pending.pop(uid, {"name": str(uid), "username": ""})
    if action == "approve":
        _sync_approve(uid, info)
        await query.edit_message_text(f"✅ *{info['name']}* (`{uid}`) aprovado.", parse_mode="Markdown")
        await context.bot.send_message(uid, f"✅ Acesso aprovado! Bem-vindo ao {BOT_NAME}.\nEnvie /start para começar.")
        logger.info(f"Aprovado: {uid}")
        _append_daily_log(f"Usuário aprovado: {info['name']} (id:{uid})")
    else:
        await query.edit_message_text(f"❌ *{info['name']}* (`{uid}`) negado.", parse_mode="Markdown")
        await context.bot.send_message(uid, "❌ Acesso negado.")
        logger.info(f"Negado: {uid}")


# ═══════════════════════════════════════════════════════════════════════════════
# LOOP AGÊNTICO (ASYNC)
# ═══════════════════════════════════════════════════════════════════════════════

async def _ask_anthropic(messages: list, user_id: int = 0) -> str:
    """Loop agêntico Anthropic — suporta tool_use nativo."""
    system = get_system_prompt()
    t0 = time.monotonic()
    total_input = total_output = total_tool_calls = 0
    error_str = ""
    try:
        for _ in range(20):
            kwargs = dict(model=MODEL, max_tokens=4096, system=system, messages=messages)
            if TOOL_DEFINITIONS:
                kwargs["tools"] = TOOL_DEFINITIONS
            client = _make_async_client()
            response = await client.messages.create(**kwargs)
            total_input += getattr(response.usage, "input_tokens", 0)
            total_output += getattr(response.usage, "output_tokens", 0)
            if response.stop_reason == "end_turn":
                return next((b.text for b in response.content if b.type == "text"), "")
            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        total_tool_calls += 1
                        logger.info(f"[tool] {block.name} {json.dumps(block.input)[:120]}")
                        result = await tool_registry.execute(
                            block.name, block.input,
                            user_id=user_id, db=db, config=TOOL_CONFIG,
                        )
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                messages.append({"role": "user", "content": results})
                continue
            break
        return next((b.text for b in response.content if hasattr(b, "text")), "")
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        raise
    finally:
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, total_input, total_output,
                         total_tool_calls, latency, error_str)
        except Exception:
            pass


async def _ask_openrouter(messages: list, user_id: int = 0) -> str:
    """Loop agêntico OpenRouter (OpenAI-compatible) — suporta qualquer modelo."""
    system = get_system_prompt()
    # Constrói lista de mensagens no formato OpenAI (com system separado)
    oai_messages = [{"role": "system", "content": system}] + [
        {"role": m["role"], "content": _convert_content_for_openai(m["content"])}
        for m in messages
        if isinstance(m.get("content"), str) or _has_media_content(m.get("content"))
    ]
    oai_tools = _anthropic_tools_to_openai(TOOL_DEFINITIONS) if TOOL_DEFINITIONS else None
    client = _make_openrouter_client()
    t0 = time.monotonic()
    total_input = total_output = total_tool_calls = 0
    error_str = ""
    try:
        for _ in range(20):
            kwargs = dict(model=MODEL, messages=oai_messages, max_tokens=4096)
            if oai_tools:
                kwargs["tools"] = oai_tools
            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            total_input += getattr(response.usage, "prompt_tokens", 0)
            total_output += getattr(response.usage, "completion_tokens", 0)
            if choice.finish_reason == "stop":
                return choice.message.content or ""
            if choice.finish_reason == "tool_calls":
                # Adiciona mensagem do assistente (com tool_calls) à conversa local
                oai_messages.append(choice.message)
                for tc in choice.message.tool_calls or []:
                    total_tool_calls += 1
                    try:
                        tool_input = json.loads(tc.function.arguments)
                    except Exception:
                        tool_input = {}
                    logger.info(f"[tool/openrouter] {tc.function.name} {json.dumps(tool_input)[:120]}")
                    result = await tool_registry.execute(
                        tc.function.name, tool_input,
                        user_id=user_id, db=db, config=TOOL_CONFIG,
                    )
                    oai_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                continue
            break
        return choice.message.content or ""
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        raise
    finally:
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, total_input, total_output,
                         total_tool_calls, latency, error_str)
        except Exception:
            pass


async def _ask_cli(messages: list, user_id: int = 0, notify_fn=None) -> str:
    """Loop via `claude -p` — usa OAuth do Claude Code, sem precisar de API key.
    Mantém continuidade por session_id armazenado por user.
    Usa as ferramentas nativas do Claude Code (Bash, Read, Write, etc.)
    com acesso ao workspace do bot e todas as envs globais.
    notify_fn(name, inp): callback assíncrono chamado a cada tool_use detectado."""
    # Extrai última mensagem do usuário
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user" and isinstance(m.get("content"), str)),
        "",
    )
    if not last_user:
        raise RuntimeError("Nenhuma mensagem do usuário encontrada.")

    session_id = _cli_sessions.get(user_id)
    t0 = time.monotonic()
    error_str = ""
    try:
        env = {
            **os.environ,
            "CLAUDECODE": "",                                            # permite execução aninhada
            "CLAUDE_CODE_ENTRYPOINT": "cli",                            # necessário para modo -p funcionar
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",  # telemetria correta
        }
        # Reforço de identidade injetado em toda mensagem (incluindo resume)
        identity_reminder = f"Lembre-se: seu nome nesta plataforma é {BOT_NAME}. Apresente-se sempre como {BOT_NAME}."
        base_flags = [
            "claude", "-p",
            "--model", MODEL,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",  # executa ferramentas sem prompt interativo
        ]
        if session_id:
            cmd = [
                *base_flags,
                "--resume", session_id,
                "--append-system-prompt", identity_reminder,
                last_user,
            ]
        else:
            system = get_system_prompt()
            cmd = [
                *base_flags,
                "--system-prompt", system,
                last_user,
            ]

        logger.info(f"[cli] cmd: {' '.join(cmd[:8])}...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(WORK_DIR),
            limit=4 * 1024 * 1024,  # 4MB — evita LimitOverrunError em respostas longas
        )
        _cli_procs[user_id] = proc

        result_text = ""
        stderr_text = ""

        async def _read_stdout():
            nonlocal result_text
            buf = b""
            while True:
                chunk = await proc.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    raw_line, buf = buf.split(b"\n", 1)
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "assistant" and notify_fn:
                        for block in event.get("message", {}).get("content", []):
                            if block.get("type") == "tool_use":
                                try:
                                    await notify_fn(block.get("name", ""), block.get("input", {}))
                                except Exception:
                                    pass
                    elif etype == "result":
                        result_text = event.get("result", "")
                        if event.get("is_error"):
                            raise RuntimeError(f"claude CLI erro: {result_text}")
                        sid = event.get("session_id")
                        if sid:
                            _cli_sessions[user_id] = sid
            # processa qualquer dado restante no buffer sem newline final
            if buf:
                line = buf.decode("utf-8", errors="replace").strip()
                if line:
                    try:
                        event = json.loads(line)
                        etype = event.get("type")
                        if etype == "result":
                            result_text = event.get("result", "")
                            if event.get("is_error"):
                                raise RuntimeError(f"claude CLI erro: {result_text}")
                            sid = event.get("session_id")
                            if sid:
                                _cli_sessions[user_id] = sid
                    except json.JSONDecodeError:
                        pass

        async def _read_stderr():
            nonlocal stderr_text
            data = await proc.stderr.read()
            stderr_text = data.decode("utf-8", errors="replace").strip()

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stdout(), _read_stderr()),
                timeout=1805,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError("claude CLI excedeu limite de 30 minutos")

        await proc.wait()
        if stderr_text:
            logger.info(f"[cli] stderr: {stderr_text[:300]}")
        if proc.returncode != 0 and not result_text:
            raise RuntimeError(f"claude CLI saiu com código {proc.returncode}: {stderr_text[:300]}")

        return result_text
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        raise
    finally:
        _cli_procs.pop(user_id, None)
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, 0, 0, 0, latency, error_str)
        except Exception:
            pass


async def ask_claude(messages: list, user_id: int = 0, notify_fn=None) -> str:
    """Roteador principal: delega para Anthropic, OpenRouter ou Claude CLI conforme PROVIDER."""
    if PROVIDER == "openrouter":
        return await _ask_openrouter(messages, user_id)
    if PROVIDER == "claude-cli":
        return await _ask_cli(messages, user_id, notify_fn=notify_fn)
    return await _ask_anthropic(messages, user_id)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def _md_to_html(text: str) -> str:
    """Converte Markdown (respostas Claude) para HTML suportado pelo Telegram."""
    code_blocks: list[str] = []

    def save_fenced(m: re.Match) -> str:
        code = _html.escape(m.group(1).strip())
        code_blocks.append(f"<pre><code>{code}</code></pre>")
        return f"\x00BLOCK{len(code_blocks)-1}\x00"

    def save_inline(m: re.Match) -> str:
        code = _html.escape(m.group(1))
        code_blocks.append(f"<code>{code}</code>")
        return f"\x00BLOCK{len(code_blocks)-1}\x00"

    # 1. Fenced code blocks ```lang\n...\n```
    text = re.sub(r"```(?:[^\n]*)\n?(.*?)```", save_fenced, text, flags=re.DOTALL)
    # 2. Inline code `...`
    text = re.sub(r"`([^`\n]+)`", save_inline, text)
    # 3. Escape HTML special chars
    text = _html.escape(text, quote=False)
    # 4. Headers → bold
    text = re.sub(r"^#{1,6}\s+(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    # 5. Bold: **text** or __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"<b>\1</b>", text, flags=re.DOTALL)
    # 6. Single *text* → bold (Telegram Markdown v1 style usado internamente)
    text = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", text)
    # 7. Italic: _text_
    text = re.sub(r"_([^_\n]+)_", r"<i>\1</i>", text)
    # 8. Tables → remove separadores, manter conteúdo legível
    def convert_table_row(m: re.Match) -> str:
        line = m.group(0).strip()
        if re.match(r"^\|[\s\-:|]+\|$", line):
            return ""
        cells = [c.strip() for c in line.strip("|").split("|")]
        return " │ ".join(c for c in cells if c)
    text = re.sub(r"^\|.+\|$", convert_table_row, text, flags=re.MULTILINE)
    # 9. Restaurar blocos de código
    for i, block in enumerate(code_blocks):
        text = text.replace(f"\x00BLOCK{i}\x00", block)
    return text.strip()


def _split_html(text: str, max_len: int = 4096) -> list[str]:
    """Divide texto em chunks sem quebrar no meio de parágrafos."""
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = (current + "\n\n" + para).lstrip() if current else para
        if len(candidate) <= max_len:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(para) > max_len:
                # parágrafo muito longo: divide por linha
                current = ""
                for line in para.split("\n"):
                    cand2 = (current + "\n" + line).lstrip() if current else line
                    if len(cand2) <= max_len:
                        current = cand2
                    else:
                        if current:
                            chunks.append(current)
                        while len(line) > max_len:
                            chunks.append(line[:max_len])
                            line = line[max_len:]
                        current = line
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks or [""]


async def send_long(update_or_bot, text: str, chat_id: int = None) -> None:
    """Envia texto longo em chunks de 4096, convertendo Markdown para HTML."""
    html_text = _md_to_html(text)
    for chunk in _split_html(html_text):
        if chat_id is not None:
            await update_or_bot.send_message(chat_id=chat_id, text=chunk, parse_mode="HTML")
        else:
            await update_or_bot.message.reply_text(chunk, parse_mode="HTML")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not has_access(user.id):
        await request_access(update, context); return
    user_lock = await _get_user_lock(user.id)
    async with user_lock:
        if conversations.get(user.id):
            db.archive_conversation(user.id, conversations[user.id], BOT_NAME)
        conversations[user.id] = []
        db.clear_conversation(user.id)
        _cli_sessions.pop(user.id, None)  # nova sessão CLI com system prompt atualizado
    # Welcome message
    tools_list = ', '.join(sorted(ENABLED_TOOLS)) if ENABLED_TOOLS else "nenhuma"
    welcome_path = BOT_DIR / "welcome.md"
    welcome_text = _read_file_safe(welcome_path)
    if welcome_text and welcome_text.strip():
        # Custom welcome message with placeholders
        welcome_msg = welcome_text.replace("{user_name}", user.first_name)
        welcome_msg = welcome_msg.replace("{bot_name}", BOT_NAME)
        welcome_msg = welcome_msg.replace("{tools}", tools_list)
    else:
        # Default welcome message
        tools_info = f"\n🔧 `{', '.join(sorted(ENABLED_TOOLS))}`" if ENABLED_TOOLS else ""
        admin_extra = (
            "\n\n*Admin:*\n/users — aprovados\n/pending — pendentes\n"
            "/revoke <id> — revogar\n/memory — ver memória\n/stats — analytics"
            if is_admin(user.id) else ""
        )
        welcome_msg = (
            f"Olá, {user.first_name}! Sou o *{BOT_NAME}*.{tools_info}\n\n"
            "/clear — limpa histórico\n/cancel — cancela operação em andamento\n/info — quem sou eu\n/id — seu ID\n/tasks — minhas tarefas"
            f"{admin_extra}"
        )
    await update.message.reply_text(welcome_msg, parse_mode="Markdown")
    _append_daily_log(f"Sessão iniciada por {user.full_name} (id:{user.id})")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not has_access(uid): return
    user_lock = await _get_user_lock(uid)
    async with user_lock:
        if conversations.get(uid):
            db.archive_conversation(uid, conversations[uid], BOT_NAME)
        conversations[uid] = []
        db.clear_conversation(uid)
        _cli_sessions.pop(uid, None)  # força nova sessão CLI
    await update.message.reply_text("🗑️ Histórico limpo!")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not has_access(uid): return
    proc = _cli_procs.get(uid)
    if proc is None:
        await update.message.reply_text("Nenhuma operação em andamento.")
        return
    try:
        proc.kill()
    except Exception:
        pass
    await update.message.reply_text("🛑 Operação cancelada.")
    logger.info(f"[cancel] Processo cancelado pelo usuário {uid}")


async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not has_access(update.effective_user.id): return
    soul = _read_file_safe(BOT_DIR / "soul.md")
    await send_long(update, f"*{BOT_NAME}*\n\n{soul or '(soul.md não encontrado)'}")


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Seu ID: `{update.effective_user.id}`", parse_mode="Markdown")


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    today = date.today().isoformat()
    days = sorted(MEM_DIR.glob("*.md"), reverse=True)
    mem_long = (BOT_DIR / "MEMORY.md").exists()
    mem_today = (MEM_DIR / f"{today}.md").exists()
    lines = [
        f"🧠 *Memória — {BOT_NAME}*\n",
        f"📚 MEMORY.md: {'✅' if mem_long else '❌ (vazio)'}",
        f"📅 Hoje ({today}): {'✅' if mem_today else '❌ (vazio)'}",
        f"📁 Dias registrados: {len(days)}",
        f"\nÚltimos dias:",
    ] + [f"  • {d.stem}" for d in days[:7]]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if not approved_users:
        await update.message.reply_text("Nenhum usuário aprovado."); return
    lines = [f"👥 *Aprovados — {BOT_NAME}*\n"]
    for uid, info in approved_users.items():
        lines.append(f"• {info.get('name','?')} {info.get('username','')} — `{uid}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if not pending:
        await update.message.reply_text("Nenhuma solicitação pendente."); return
    lines = [f"⏳ *Pendentes — {BOT_NAME}*\n"]
    for uid, info in pending.items():
        lines.append(f"• {info['name']} {info['username']} — `{uid}`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    if not context.args:
        await update.message.reply_text("Uso: /revoke <user_id>"); return
    try:
        uid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID inválido."); return
    if uid in approved_users:
        info = approved_users[uid]
        _sync_revoke(uid)
        await update.message.reply_text(f"✅ Acesso de *{info.get('name',uid)}* revogado.", parse_mode="Markdown")
        try:
            await context.bot.send_message(uid, "⚠️ Seu acesso foi revogado.")
        except Exception:
            pass
        _append_daily_log(f"Acesso revogado: {info.get('name',uid)} (id:{uid})")
    else:
        await update.message.reply_text("Usuário não encontrado.")


_RESTART_FLAG = BOT_DIR / ".restart_notify"

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    uptime_s = int(time.monotonic() - _bot_start_time)
    h, rem = divmod(uptime_s, 3600)
    m, s = divmod(rem, 60)
    uptime_str = f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
    tools_str = ", ".join(sorted(ENABLED_TOOLS)) if ENABLED_TOOLS else "nenhuma"
    try:
        schedules = db.schedule_list()
        sched_count = len(schedules)
    except Exception:
        sched_count = 0
    try:
        rows = db._conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('in_progress','paused','pending')").fetchone()
        tasks_count = rows[0] if rows else 0
    except Exception:
        tasks_count = 0
    conv_count = len(conversations)
    lines = [
        f"*{BOT_NAME}*",
        f"",
        f"🤖 *Modelo:* `{MODEL}`",
        f"⚙️ *Provider:* `{PROVIDER}`",
        f"⏱ *Uptime:* `{uptime_str}`",
        f"",
        f"🔐 *Acesso:* `{ACCESS_MODE}`",
        f"👥 *Grupos:* `{GROUP_MODE}`",
        f"🛠 *Ferramentas:* `{tools_str}`",
        f"",
        f"💬 *Conversas ativas:* `{conv_count}`",
        f"📅 *Agendamentos:* `{sched_count}`",
        f"📋 *Tarefas pendentes:* `{tasks_count}`",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("🔄 Reiniciando...")
    _RESTART_FLAG.write_text(str(update.effective_user.id))
    service = f"claude-bot-{BOT_DIR.name}"
    asyncio.get_event_loop().call_later(
        0.5, lambda: __import__("subprocess").run(["sudo", "systemctl", "restart", service])
    )


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not has_access(user.id): return
    valid = ("all", "in_progress", "paused", "completed", "failed", "cancelled")
    status_filter = context.args[0] if context.args else "all"
    if status_filter not in valid:
        await update.message.reply_text(f"Status inválido. Use: {', '.join(valid)}"); return
    items = db.tasks_for_user(user.id) if status_filter == "all" else db.tasks_for_user(user.id, status=status_filter)
    if not items:
        await update.message.reply_text("📋 Nenhuma tarefa encontrada."); return
    lines = [f"📋 *Tarefas — {BOT_NAME}*\n"]
    for t in items[:20]:
        emoji = task_status_emoji(t["status"])
        steps = t.get("steps", [])
        si = f" [{t['current_step']+1}/{len(steps)}]" if steps else ""
        lines.append(f"{emoji} `{t['id']}` {t['title']}{si}")
        if t.get("progress"):
            lines.append(f"   → {t['progress'][:60]}")
    await send_long(update, "\n".join(lines))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id): return
    days_map = {"hoje": 1, "semana": 7, "mes": 30, "mês": 30}
    period = context.args[0] if context.args else "hoje"
    days = days_map.get(period, 1)
    try:
        days = int(period)
    except (ValueError, TypeError):
        pass
    s = db.get_summary(days)
    label = {"hoje": "Hoje", "semana": "Última semana", "mes": "Último mês", "mês": "Último mês"}.get(
        period if not isinstance(period, int) else "", f"Últimos {days} dia(s)"
    )
    await update.message.reply_text(
        f"📊 *Analytics — {BOT_NAME}*\n"
        f"📅 {label}\n\n"
        f"💬 Mensagens: {s['msgs']}\n"
        f"📥 Input tokens: {s['input_tokens']:,}\n"
        f"📤 Output tokens: {s['output_tokens']:,}\n"
        f"🔧 Tool calls: {s['tool_calls']}\n"
        f"❌ Erros: {s['errors']}\n"
        f"💰 Custo estimado: ${s['cost_usd']:.4f}",
        parse_mode="Markdown",
    )


async def callback_task(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    action, tid = query.data.split(":", 1)
    user_id = query.from_user.id
    if not has_access(user_id):
        await query.answer("Sem acesso.", show_alert=True); return
    t = db.task_get(tid)
    if not t:
        await query.edit_message_text(f"❌ Tarefa `{tid}` não encontrada.", parse_mode="Markdown"); return
    if t["user_id"] != user_id and not is_admin(user_id):
        await query.answer("Sem permissão.", show_alert=True); return

    if action == "cancelar":
        db.task_update(tid, status="cancelled")
        await query.edit_message_text(f"🚫 Tarefa *{t['title']}* cancelada.", parse_mode="Markdown")
        _append_daily_log(f"Tarefa cancelada: {tid} ({t['title']})")
        return

    # action == "retomar"
    db.task_update(tid, status="in_progress")
    steps = t.get("steps", [])
    step_idx = t.get("current_step", 0)
    step_txt = steps[step_idx] if steps and step_idx < len(steps) else "(sem passos)"
    progress = t.get("progress") or "(sem progresso anterior)"
    ctx_data = t.get("context", {})
    resume_msg = (
        f"[Sistema] Retomando tarefa `{tid}`: {t['title']}\n"
        f"Progresso anterior: {progress}\n"
        f"Próximo passo ({step_idx+1}/{len(steps) or 1}): {step_txt}\n"
    )
    if ctx_data:
        resume_msg += f"Contexto salvo: {json.dumps(ctx_data, ensure_ascii=False)}\n"
    resume_msg += "Continue de onde parou."

    await query.edit_message_text(f"▶️ Retomando *{t['title']}*...", parse_mode="Markdown")
    user_lock = await _get_user_lock(user_id)
    async with user_lock:
        history = conversations.setdefault(user_id, [])
        snapshot = list(history)
        history.append({"role": "user", "content": resume_msg})
        await context.bot.send_chat_action(query.message.chat_id, ChatAction.TYPING)
        try:
            reply = await ask_claude(list(history), user_id=user_id)
            history.append({"role": "assistant", "content": reply})
            await context.bot.send_message(query.message.chat_id, _md_to_html(reply), parse_mode="HTML")
            db.save_conversation(user_id, history)
        except Exception as e:
            logger.error(f"Erro ao retomar tarefa {tid}: {e}", exc_info=True)
            conversations[user_id] = snapshot
            db.save_conversation(user_id, snapshot)
            await context.bot.send_message(
                query.message.chat_id,
                f"⚠️ Erro ao retomar: `{type(e).__name__}`",
                parse_mode="Markdown",
            )


# ── Throttle de alertas admin ─────────────────────────────────────────────────

_last_admin_alert = 0.0
_ADMIN_ALERT_COOLDOWN = 60  # segundos entre alertas

# ── Whisper (transcrição de voz) ───────────────────────────────────────────────

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        logger.info("[whisper] Carregando modelo 'small'...")
        _whisper_model = whisper.load_model("small")
        logger.info("[whisper] Modelo carregado.")
    return _whisper_model


async def _transcribe(file_path: str) -> str:
    """Transcreve arquivo de áudio usando Whisper (roda em thread para não bloquear)."""
    def _run():
        model = _get_whisper_model()
        result = model.transcribe(file_path, language="pt")
        return result["text"].strip()
    return await asyncio.to_thread(_run)


# ── Helpers de grupo ──────────────────────────────────────────────────────────

def _is_group_chat(update: Update) -> bool:
    return update.effective_chat.type in ("group", "supergroup")


def _is_mentioned(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Retorna True se o bot foi mencionado (@username) ou se a msg é reply ao bot."""
    msg = update.message
    if not msg:
        return False
    # Reply direto a uma mensagem do bot
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == context.bot.id:
            return True
    # Menção @username nas entidades
    bot_username = (context.bot.username or "").lower()
    text = msg.text or msg.caption or ""
    for entity in (msg.entities or msg.caption_entities or []):
        if entity.type == "mention":
            mention = text[entity.offset:entity.offset + entity.length].lstrip("@").lower()
            if mention == bot_username:
                return True
    return False


def _should_respond_in_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_group_chat(update):
        return True
    if GROUP_MODE == "always":
        return True
    return _is_mentioned(update, context)


def _strip_mention(text: str, bot_username: str) -> str:
    """Remove @botname do início da mensagem (caso mention_only)."""
    if not text or not bot_username:
        return text
    import re
    return re.sub(rf"^@{re.escape(bot_username)}\s*", "", text, flags=re.IGNORECASE).strip()


def _group_prefix(update: Update) -> str:
    """Retorna prefixo com contexto de grupo para injetar na mensagem."""
    chat = update.effective_chat
    title = chat.title or "grupo"
    user = update.effective_user
    name = user.full_name if user else "usuário"
    return f"[Contexto: grupo Telegram — \"{title}\". Quem escreveu: {name}]\n"


# ── Handler compartilhado (texto e voz) ───────────────────────────────────────

def _tool_label(name: str, inp: dict) -> str:
    """Formata nome + input de tool_use para mensagem de status."""
    if name in ("Bash", "run_shell"):
        cmd = inp.get("command", inp.get("cmd", ""))
        return f"`{name}` · {cmd[:70]}"
    if name in ("Read", "read_file"):
        return f"`{name}` · {inp.get('file_path', inp.get('path', ''))}"
    if name in ("Write", "Edit", "write_file"):
        return f"`{name}` · {inp.get('file_path', inp.get('path', ''))}"
    if name == "github":
        action = inp.get("action", "")
        return f"`github` · {action}"
    if name in ("git_op", "git"):
        op = inp.get("operation", inp.get("op", ""))
        return f"`git` · {op}"
    if name == "http_request":
        method = inp.get("method", "GET")
        url = inp.get("url", "")[:50]
        return f"`http` · {method} {url}"
    first_val = next(iter(inp.values()), "") if inp else ""
    return f"`{name}` · {str(first_val)[:70]}" if first_val else f"`{name}`"


async def _process_message(update: Update, context: ContextTypes.DEFAULT_TYPE, content) -> None:
    """Processa uma mensagem (texto, lista multimídia ou transcrição) e responde.

    content pode ser str (texto simples) ou list (blocos Anthropic: text + image).
    Em grupos, usa chat_id como chave do histórico (mesmo bucket do scheduler).
    """
    global _last_admin_alert
    user = update.effective_user
    # Em grupos, conversa é por chat (não por usuário) para manter contexto do scheduler
    conv_id = update.effective_chat.id if _is_group_chat(update) else user.id

    user_lock = await _get_user_lock(conv_id)
    async with user_lock:
        history = conversations.setdefault(conv_id, [])
        history.append({"role": "user", "content": content})
        if len(history) > MAX_HISTORY:
            conversations[conv_id] = history[-MAX_HISTORY:]
            history = conversations[conv_id]

        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
        snapshot = list(history)

        # Mantém o "digitando..." ativo durante tarefas longas (reenvio a cada 4s)
        _typing_active = True
        async def _keep_typing():
            while _typing_active:
                await asyncio.sleep(4)
                if _typing_active:
                    try:
                        await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
                    except Exception:
                        pass
        typing_task = asyncio.create_task(_keep_typing())

        # Mensagem de status em tempo real (apenas claude-cli com stream-json)
        status_msg = None
        if PROVIDER == "claude-cli":
            try:
                status_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⏳ Pensando...",
                )
            except Exception:
                pass

        _last_notify_time = [0.0]

        async def _notify_tool(name: str, inp: dict):
            now = time.monotonic()
            if now - _last_notify_time[0] < 2.5:
                return
            _last_notify_time[0] = now
            if status_msg:
                try:
                    await context.bot.edit_message_text(
                        text=f"🔧 {_tool_label(name, inp)}",
                        chat_id=update.effective_chat.id,
                        message_id=status_msg.message_id,
                    )
                except Exception:
                    pass

        try:
            reply = await ask_claude(list(history), user_id=conv_id,
                                     notify_fn=_notify_tool if status_msg else None)
            history.append({"role": "assistant", "content": reply})
            await send_long(update, reply)
            db.save_conversation(conv_id, history)
            logger.info(f"Respondido para {user.id} ({user.username})")
            # Drena arquivos pendentes (gerados pelo tool send_telegram_file)
            for item in _pending_files.pop(conv_id, []):
                try:
                    with open(item["path"], "rb") as fh:
                        await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=fh,
                            filename=Path(item["path"]).name,
                            caption=item.get("caption") or None,
                        )
                except Exception as fe:
                    logger.error(f"[send_document] Erro ao enviar {item['path']}: {fe}")
                    await update.message.reply_text(
                        f"⚠️ Não consegui enviar o arquivo `{Path(item['path']).name}`.",
                        parse_mode="Markdown",
                    )
        except Exception as e:
            _pending_files.pop(conv_id, None)  # limpa fila para não vazar entre turnos
            logger.error(f"Erro: {e}", exc_info=True)
            # Mantém a msg do usuário no histórico + adiciona placeholder do assistente
            # para evitar dois turnos "user" consecutivos e preservar contexto
            error_history = snapshot + [
                {"role": "assistant", "content": f"[Erro interno ao processar: {type(e).__name__}. Não consegui completar a resposta anterior.]"}
            ]
            conversations[conv_id] = error_history
            db.save_conversation(conv_id, error_history)
            # Código 143 = SIGTERM (reinício do bot) — mensagem amigável, sem traceback
            if "código 143" in str(e) or "code 143" in str(e):
                await update.message.reply_text("🔄 Bot reiniciando, já volto!")
            else:
                tb = traceback.format_exc()
                tb_short = tb[-800:] if len(tb) > 800 else tb
                await update.message.reply_text(f"⚠️ `{type(e).__name__}: {e}`\n\n```\n{tb_short}\n```", parse_mode="Markdown")
            now = time.monotonic()
            if ADMIN_ID and not is_admin(user.id) and (now - _last_admin_alert) > _ADMIN_ALERT_COOLDOWN:
                _last_admin_alert = now
                try:
                    await context.bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"🚨 *Erro em {BOT_NAME}*\n\n"
                            f"👤 {user.full_name} (`{user.id}`)\n"
                            f"❌ `{type(e).__name__}: {str(e)[:200]}`"
                        ),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
        finally:
            _typing_active = False
            typing_task.cancel()
            if status_msg:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=status_msg.message_id,
                    )
                except Exception:
                    pass


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not has_access(user.id):
        await request_access(update, context); return
    if not _should_respond_in_group(update, context):
        return
    reply_prefix = _extract_reply_context(update.message)
    text = (reply_prefix + update.message.text) if reply_prefix else update.message.text
    if _is_group_chat(update):
        text = _strip_mention(text, context.bot.username or "")
        text = _group_prefix(update) + text
    await _process_message(update, context, text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not has_access(user.id):
        await request_access(update, context); return
    if not _should_respond_in_group(update, context):
        return

    photo = update.message.photo[-1]  # maior resolução disponível
    if photo.file_size and photo.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("⚠️ Imagem muito grande (>20 MB).")
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        tg_file = await context.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(tmp_path)

        caption = update.message.caption or ""
        reply_prefix = _extract_reply_context(update.message)

        if PROVIDER == "claude-cli":
            description = await _describe_image_for_cli(tmp_path)
            label = f"[Foto com legenda: {caption}]" if caption else "[Foto]"
            text = reply_prefix + label + "\n" + description
            await _process_message(update, context, text)
        else:
            with open(tmp_path, "rb") as fh:
                img_data = base64.b64encode(fh.read()).decode()
            text_part = (f"{caption}\n" if caption else "") + (reply_prefix or "[Foto]")
            if not caption and not reply_prefix:
                text_part = "[Foto]"
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data}},
                {"type": "text", "text": text_part},
            ]
            await _process_message(update, context, content)
    except Exception as e:
        logger.error(f"[photo] Erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Não consegui processar a imagem.", parse_mode="Markdown")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not has_access(user.id):
        await request_access(update, context); return
    if not _should_respond_in_group(update, context):
        return

    doc = update.message.document
    if not doc:
        return
    if doc.file_size and doc.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("⚠️ Arquivo muito grande (>20 MB).")
        return

    fname = doc.file_name or "arquivo"
    ext = Path(fname).suffix.lower() or ".bin"

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(tmp_path)

        file_content = await asyncio.to_thread(_extract_document_text, tmp_path, fname)
        caption = update.message.caption or ""
        reply_prefix = _extract_reply_context(update.message)

        parts = []
        if reply_prefix:
            parts.append(reply_prefix.rstrip("\n"))
        if caption:
            parts.append(caption)
        parts.append(f"[Arquivo: {fname}]\n{file_content}")
        await _process_message(update, context, "\n".join(parts))
    except Exception as e:
        logger.error(f"[document] Erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Não consegui processar o documento.", parse_mode="Markdown")
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not has_access(user.id):
        await request_access(update, context); return
    if not _should_respond_in_group(update, context):
        return

    video = update.message.video or update.message.video_note
    if not video:
        return
    if video.file_size and video.file_size > 20 * 1024 * 1024:
        await update.message.reply_text("⚠️ Vídeo muito grande (>20 MB).")
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        video_path = tmp.name
    audio_path = video_path + ".ogg"
    try:
        tg_file = await context.bot.get_file(video.file_id)
        await tg_file.download_to_drive(video_path)

        import subprocess as _subprocess
        ffmpeg_result = await asyncio.to_thread(
            lambda: _subprocess.run(
                ["ffmpeg", "-i", video_path, "-vn", "-acodec", "libvorbis", audio_path, "-y"],
                capture_output=True, timeout=60,
            )
        )
        if ffmpeg_result.returncode != 0 or not Path(audio_path).exists():
            await update.message.reply_text("⚠️ Não consegui extrair áudio do vídeo.")
            return

        text = await _transcribe(audio_path)
        if not text:
            await update.message.reply_text("⚠️ Não consegui transcrever o áudio do vídeo.")
            return

        caption = update.message.caption or ""
        reply_prefix = _extract_reply_context(update.message)

        logger.info(f"[whisper/video] Transcrição de {user.id}: {text[:80]}")
        await update.message.reply_text(f"🎬 _{text}_", parse_mode="Markdown")

        parts = []
        if reply_prefix:
            parts.append(reply_prefix.rstrip("\n"))
        if caption:
            parts.append(caption)
        parts.append(text)
        await _process_message(update, context, "\n".join(parts))
    except Exception as e:
        logger.error(f"[video] Erro: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Não consegui processar o vídeo.", parse_mode="Markdown")
    finally:
        try:
            os.unlink(video_path)
        except Exception:
            pass
        try:
            if Path(audio_path).exists():
                os.unlink(audio_path)
        except Exception:
            pass


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not has_access(user.id):
        await request_access(update, context); return
    if not _should_respond_in_group(update, context):
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    await context.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        tg_file = await context.bot.get_file(voice.file_id)
        await tg_file.download_to_drive(tmp_path)
        text = await _transcribe(tmp_path)
    except Exception as e:
        logger.error(f"[whisper] Erro na transcrição: {e}", exc_info=True)
        await update.message.reply_text("⚠️ Não consegui transcrever o áudio.", parse_mode="Markdown")
        return
    finally:
        os.unlink(tmp_path)

    if not text:
        await update.message.reply_text("⚠️ Áudio vazio ou inaudível.", parse_mode="Markdown")
        return

    logger.info(f"[whisper] Transcrição de {user.id}: {text[:80]}")
    await update.message.reply_text(f"🎙️ _{text}_", parse_mode="Markdown")
    reply_prefix = _extract_reply_context(update.message)
    await _process_message(update, context, reply_prefix + text if reply_prefix else text)


# ── Startup ───────────────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    # Carrega conversas do DB
    _load_conversations_from_db()
    logger.info(f"[startup] {len(conversations)} conversa(s) carregada(s) do DB")

    # Notifica quem pediu o restart
    if _RESTART_FLAG.exists():
        try:
            uid = int(_RESTART_FLAG.read_text().strip())
            await application.bot.send_message(
                chat_id=uid,
                text=f"✅ *{BOT_NAME}* online | `{PROVIDER}` · `{MODEL}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"[startup] Falha ao notificar restart: {e}")
        finally:
            _RESTART_FLAG.unlink(missing_ok=True)

    # Inicia scheduler
    asyncio.create_task(sched_mod.scheduler_loop(
        application, db, ask_claude, conversations,
        lambda uid, msgs: db.save_conversation(uid, msgs),
        ADMIN_ID, get_user_lock=_get_user_lock,
    ))
    logger.info("[scheduler] Scheduler de notificações iniciado")

    # Recovery de tarefas interrompidas
    interrupted = db.tasks_interrupted()
    if not interrupted:
        return
    logger.info(f"[startup] {len(interrupted)} tarefa(s) interrompida(s)")
    notified: dict[int, list] = {}
    for t in interrupted:
        db.task_update(t["id"], status="paused")
        notified.setdefault(t["user_id"], []).append(t)

    for user_id, user_tasks in notified.items():
        for t in user_tasks:
            steps = t.get("steps", [])
            step_idx = t.get("current_step", 0)
            step_txt = steps[step_idx] if steps and step_idx < len(steps) else "?"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("▶️ Retomar",  callback_data=f"retomar:{t['id']}"),
                InlineKeyboardButton("❌ Cancelar", callback_data=f"cancelar:{t['id']}"),
            ]])
            msg = (
                f"⚠️ *Tarefa interrompida — {BOT_NAME}*\n\n"
                f"📌 `{t['id']}` — {t['title']}\n"
                f"📍 Passo {step_idx+1}/{len(steps) or 1}: {step_txt}\n"
            )
            if t.get("progress"):
                msg += f"💾 Progresso: {t['progress'][:120]}\n"
            msg += "\nDeseja continuar?"
            try:
                await application.bot.send_message(
                    chat_id=user_id, text=msg,
                    parse_mode="Markdown", reply_markup=kb,
                )
            except Exception as e:
                logger.warning(f"[startup] Não foi possível notificar uid={user_id}: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN não definido!"); sys.exit(1)
    if PROVIDER == "openrouter":
        if not OPENROUTER_API_KEY:
            logger.error("PROVIDER=openrouter mas OPENROUTER_API_KEY não definida!"); sys.exit(1)
    elif PROVIDER == "claude-cli":
        import shutil
        if not shutil.which("claude"):
            logger.error("PROVIDER=claude-cli mas binário 'claude' não encontrado no PATH!"); sys.exit(1)
        logger.info("Usando Claude Code OAuth via CLI (sem API key)")
    elif PROVIDER == "anthropic":
        if not ANTHROPIC_API_KEY and not _CLAUDE_CREDS_PATH.exists():
            logger.error("ANTHROPIC_API_KEY não definida e OAuth não encontrado!"); sys.exit(1)
    else:
        logger.error(f"PROVIDER inválido: '{PROVIDER}'. Use 'anthropic', 'openrouter' ou 'claude-cli'."); sys.exit(1)
    if ACCESS_MODE == "approval" and ADMIN_ID == 0:
        logger.warning("ACCESS_MODE=approval sem ADMIN_ID configurado!")
    logger.info(f"Bot '{BOT_NAME}' | provider={PROVIDER} | model={MODEL} | access={ACCESS_MODE} | tools={ENABLED_TOOLS or 'none'} | admin={ADMIN_ID}")
    _append_daily_log(f"Bot iniciado | tools={list(ENABLED_TOOLS)}")

    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("clear",   cmd_clear))
    app.add_handler(CommandHandler("cancel",  cmd_cancel))
    app.add_handler(CommandHandler("info",    cmd_info))
    app.add_handler(CommandHandler("id",      cmd_id))
    app.add_handler(CommandHandler("tasks",   cmd_tasks))
    app.add_handler(CommandHandler("memory",  cmd_memory))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(CommandHandler("users",   cmd_users))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("revoke",   cmd_revoke))
    app.add_handler(CommandHandler("restart",  cmd_restart))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CallbackQueryHandler(callback_approval, pattern=r"^(approve|deny):\d+$"))
    app.add_handler(CallbackQueryHandler(callback_task,     pattern=r"^(retomar|cancelar):.+$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
