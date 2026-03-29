"""
Core compartilhado — pipeline de IA, config, memória, acesso.
Usado por bot.py (Telegram) e whatsapp_bot.py (WhatsApp).

Uso:
    import core
    core.init("/caminho/para/bots/meu-bot")
    reply = await core.ask_claude(messages, user_id="123")
"""

import os
import re
import sys
import json
import base64
import logging
import time
import asyncio
import tempfile
from datetime import date, timedelta, datetime
from pathlib import Path

import anthropic
from db import BotDB
import tools as tool_registry
from tools.tasks import task_status_emoji
import tracer
from compactor import compact_history

# ── Estado do módulo (inicializado por init()) ───────────────────────────────

_initialized = False

BOT_DIR: Path = None
BASE_DIR: Path = None
BOT_NAME: str = ""
MODEL: str = ""
PROVIDER: str = ""
ADMIN_ID = 0
MAX_HISTORY: int = 20
DEBOUNCE_SECONDS: float = 3.0
ACCESS_MODE: str = "approval"
ENABLED_TOOLS: set = set()
GROUP_MODE: str = "always"
WORK_DIR: Path = None
MEM_DIR: Path = None

ANTHROPIC_API_KEY: str = ""
OPENROUTER_API_KEY: str = ""
OPENAI_API_KEY: str = ""

COMPACTION_ENABLED: bool = False
COMPACTION_MODEL: str = "google/gemini-2.0-flash-001"
COMPACTION_KEEP: int = 10
TRACING_ENABLED: bool = True

GUARDRAILS_ENABLED: bool = True
GUARDRAILS_MODE: str = "notify"
GUARDRAILS_LEVEL: str = "dangerous"
INJECTION_THRESHOLD: float = 0.7

BEHAVIOR_LEARNING_ENABLED: bool = False
BEHAVIOR_MAX_CHARS: int = 2000

DB_URL: str = ""
GIT_TOKEN: str = ""
GIT_USER: str = ""
GIT_EMAIL: str = ""
GITHUB_TOKEN: str = ""

db: BotDB = None
TOOL_CONFIG: dict = {}
TOOL_DEFINITIONS: list = []

conversations: dict = {}
approved_users: dict = {}
pending: dict = {}
_pending_files: dict = {}
_user_locks: dict = {}
_locks_lock: asyncio.Lock = None
_injection_warnings: dict = {}
_thinking_levels: dict = {}
_cli_sessions: dict = {}
_cli_procs: dict = {}
_debounce_buffer: dict = {}
_debounce_tasks: dict = {}
_bot_start_time: float = 0

logger: logging.Logger = None

_CLAUDE_CREDS_PATH: Path = None
_CODEX_AUTH_PATH: Path = None
_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
_PROTECTED_PATHS: list = []


# ── Inicialização ────────────────────────────────────────────────────────────

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


def init(bot_dir: str | Path) -> None:
    """Inicializa o módulo core com o diretório do bot. Deve ser chamado antes de qualquer uso."""
    global _initialized
    global BOT_DIR, BASE_DIR, BOT_NAME, MODEL, PROVIDER, ADMIN_ID
    global MAX_HISTORY, DEBOUNCE_SECONDS, ACCESS_MODE, ENABLED_TOOLS, GROUP_MODE
    global WORK_DIR, MEM_DIR
    global ANTHROPIC_API_KEY, OPENROUTER_API_KEY, OPENAI_API_KEY
    global COMPACTION_ENABLED, COMPACTION_MODEL, COMPACTION_KEEP, TRACING_ENABLED
    global GUARDRAILS_ENABLED, GUARDRAILS_MODE, GUARDRAILS_LEVEL, INJECTION_THRESHOLD
    global BEHAVIOR_LEARNING_ENABLED, BEHAVIOR_MAX_CHARS
    global DB_URL, GIT_TOKEN, GIT_USER, GIT_EMAIL, GITHUB_TOKEN
    global db, TOOL_CONFIG, TOOL_DEFINITIONS
    global conversations, approved_users, pending, _pending_files
    global _user_locks, _locks_lock, _injection_warnings
    global _thinking_levels, _cli_sessions, _cli_procs
    global _debounce_buffer, _debounce_tasks, _bot_start_time
    global logger
    global _CLAUDE_CREDS_PATH, _CODEX_AUTH_PATH, _PROTECTED_PATHS

    BOT_DIR = Path(bot_dir).resolve()
    if not BOT_DIR.exists():
        print(f"Erro: '{BOT_DIR}' não encontrado.")
        sys.exit(1)

    BASE_DIR = BOT_DIR.parent.parent

    # Carrega config
    _load_env_file(BASE_DIR / "config.global")
    _load_env_file(BASE_DIR / "secrets.global", override=True)
    _load_env_file(BOT_DIR / ".env", override=True)
    _load_env_file(BOT_DIR / "secrets.env", override=True)

    # Configurações
    ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
    OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    OPENAI_API_KEY     = os.environ.get("OPENAI_API_KEY", "")
    BOT_NAME           = os.environ.get("BOT_NAME", BOT_DIR.name)
    MODEL              = os.environ.get("MODEL", "claude-opus-4-6")
    MAX_HISTORY        = int(os.environ.get("MAX_HISTORY", "20"))
    DEBOUNCE_SECONDS   = float(os.environ.get("DEBOUNCE_SECONDS", "3"))
    COMPACTION_ENABLED = os.environ.get("COMPACTION_ENABLED", "false").lower() == "true"
    COMPACTION_MODEL   = os.environ.get("COMPACTION_MODEL", "google/gemini-2.0-flash-001")
    COMPACTION_KEEP    = int(os.environ.get("COMPACTION_KEEP", "10"))
    TRACING_ENABLED    = os.environ.get("TRACING_ENABLED", "true").lower() == "true"
    ADMIN_ID           = 0 if os.environ.get("ADMIN_ID", "").strip() in ("", "0", "auto") else int(os.environ.get("ADMIN_ID"))
    ACCESS_MODE        = os.environ.get("ACCESS_MODE", "approval").lower()
    PROVIDER           = os.environ.get("PROVIDER", "anthropic").lower()

    _tools_raw    = os.environ.get("TOOLS", "none").lower()
    ENABLED_TOOLS = set() if _tools_raw == "none" else {t.strip() for t in _tools_raw.split(",")}

    GROUP_MODE = os.environ.get("GROUP_MODE", "always").lower()

    GUARDRAILS_ENABLED = os.environ.get("GUARDRAILS_ENABLED", "true").lower() == "true"
    GUARDRAILS_MODE    = os.environ.get("GUARDRAILS_MODE", "notify").lower()
    GUARDRAILS_LEVEL   = os.environ.get("GUARDRAILS_LEVEL", "dangerous").lower()
    INJECTION_THRESHOLD = float(os.environ.get("INJECTION_THRESHOLD", "0.7"))

    BEHAVIOR_LEARNING_ENABLED = os.environ.get("BEHAVIOR_LEARNING_ENABLED", "false").lower() == "true"
    BEHAVIOR_MAX_CHARS        = int(os.environ.get("BEHAVIOR_MAX_CHARS", "2000"))

    WORK_DIR = Path(os.environ.get("WORK_DIR", str(BOT_DIR / "workspace")))
    MEM_DIR  = BOT_DIR / "memory"
    MEM_DIR.mkdir(exist_ok=True)

    DB_URL       = os.environ.get("DB_URL", "")
    GIT_TOKEN    = os.environ.get("GIT_TOKEN", "")
    GIT_USER     = os.environ.get("GIT_USER", "")
    GIT_EMAIL    = os.environ.get("GIT_EMAIL", "")
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "") or GIT_TOKEN

    _CLAUDE_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
    _CODEX_AUTH_PATH = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"

    _PROTECTED_PATHS = [
        str(BASE_DIR / "config.global"),
        str(BOT_DIR / ".env"), str(BOT_DIR / "secrets.env"),
        str(Path.home() / ".claude" / ".credentials.json"),
        str(_CODEX_AUTH_PATH),
        "/etc/passwd", "/etc/shadow", "/root", str(Path.home() / ".ssh"),
    ]

    # Logging
    logging.basicConfig(
        format=f"[{BOT_NAME}] %(asctime)s %(levelname)s: %(message)s",
        level=logging.INFO,
    )
    logger = logging.getLogger(BOT_NAME)

    # Database
    db = BotDB(BOT_DIR / "bot_data.db")
    db.migrate_from_json(BOT_DIR)

    # Pending files queue
    _pending_files = {}

    # Tool config
    TOOL_CONFIG = {
        "BOT_DIR": BOT_DIR, "BASE_DIR": BASE_DIR, "BOT_NAME": BOT_NAME,
        "WORK_DIR": WORK_DIR, "MEM_DIR": MEM_DIR,
        "DB_URL": DB_URL, "GIT_TOKEN": GIT_TOKEN, "GIT_USER": GIT_USER,
        "GIT_EMAIL": GIT_EMAIL, "GITHUB_TOKEN": GITHUB_TOKEN,
        "PROTECTED_PATHS": _PROTECTED_PATHS,
        "append_daily_log": _append_daily_log,
        "pending_files": _pending_files,
        "_env": os.environ,
        "GUARDRAILS_ENABLED": "true" if GUARDRAILS_ENABLED else "false",
        "GUARDRAILS_MODE": GUARDRAILS_MODE,
        "GUARDRAILS_LEVEL": GUARDRAILS_LEVEL,
        "_approval_granted": {},
        "_user_name": "",
    }

    TOOL_DEFINITIONS = tool_registry.build_definitions(
        ENABLED_TOOLS, WORK_DIR, BASE_DIR, BOT_NAME,
        guardrails_mode=GUARDRAILS_MODE if GUARDRAILS_ENABLED else "",
    )

    # Estado
    conversations = {}
    approved_users = db.load_approved()
    pending = {}
    _user_locks = {}
    _locks_lock = asyncio.Lock()
    _injection_warnings = {}
    _thinking_levels = {}
    _cli_sessions = {}
    _cli_procs = {}
    _debounce_buffer = {}
    _debounce_tasks = {}
    _bot_start_time = time.monotonic()

    _initialized = True
    logger.info(f"[core] Inicializado: bot={BOT_NAME} provider={PROVIDER} model={MODEL}")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_file_safe(path: Path, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8").strip()
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n... (truncado — {len(content)} chars total)"
    return content


def _append_daily_log(content: str, day=None):
    """Adiciona entrada ao diário do dia (com timestamp)."""
    d = day or date.today()
    path = MEM_DIR / f"{d.isoformat()}.md"
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n### {timestamp}\n{content.strip()}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
    path.chmod(0o600)


# ── Sistema de Memória ───────────────────────────────────────────────────────

def build_context() -> str:
    parts = []
    parts.append(f"# Identidade\nSeu nome é **{BOT_NAME}**. Apresente-se sempre como {BOT_NAME}, nunca como \"Claude\" ou qualquer outro nome.")
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
    if BEHAVIOR_LEARNING_ENABLED:
        behavior = _read_file_safe(BOT_DIR / "BEHAVIOR.md", max_chars=BEHAVIOR_MAX_CHARS)
        if behavior:
            parts.append(
                f"---\n## Perfil Comportamental\n{behavior}\n\n"
                "Use este perfil para antecipar necessidades. "
                "Quando detectar padrões recorrentes, sugira criar agendamento via schedule."
            )
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


def _check_env_capabilities() -> str:
    import shutil
    issues = []
    if "cron" in ENABLED_TOOLS and not shutil.which("crontab"):
        issues.append("⚠️ `manage_cron` indisponível: crontab não encontrado.")
    if "shell" in ENABLED_TOOLS or "git" in ENABLED_TOOLS:
        if not shutil.which("git"):
            issues.append("⚠️ `git_op` degradado: git não encontrado no PATH")
    if "shell" in ENABLED_TOOLS:
        if not shutil.which("bash"):
            issues.append("⚠️ `run_shell` degradado: bash não encontrado no PATH")
    if not issues:
        return ""
    return "\n\n---\n## ⚠️ Limitações do ambiente detectadas\n" + "\n".join(issues) + \
        "\n\nQuando uma ferramenta não está disponível, informe o usuário."


def get_system_prompt(user_id: int | str = 0) -> str:
    prompt = build_context()
    if ENABLED_TOOLS:
        prompt += f"\n\n---\n## Ferramentas disponíveis\n{', '.join(sorted(ENABLED_TOOLS))}. Use quando necessário."
    if GUARDRAILS_ENABLED and GUARDRAILS_MODE == "confirm":
        prompt += (
            "\n\n---\n## ⚠️ Modo de aprovação ativo\n"
            "IMPORTANTE: Antes de executar ações destrutivas ou que modifiquem dados, "
            "use `request_approval` para pedir confirmação ao usuário. "
            "NUNCA execute ações de risco sem aprovação prévia."
        )
    if user_id and user_id in _injection_warnings:
        prompt += f"\n\n---\n{_injection_warnings[user_id]}"
    env_warnings = _check_env_capabilities()
    if env_warnings:
        prompt += env_warnings
    # Tarefas ativas
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
    # Agendamentos
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
        prompt += "\n\n---\n## Notificações proativas agendadas (SQLite)\nNenhuma."
    return prompt


# ── Acesso ───────────────────────────────────────────────────────────────────

def is_admin(uid: int | str) -> bool:
    if ADMIN_ID == 0:
        return False
    # Compara como string para suportar Telegram (int) e WhatsApp (string)
    return str(uid) == str(ADMIN_ID)


def has_access(uid: int | str) -> bool:
    if ACCESS_MODE == "open":
        return True
    if is_admin(uid):
        return True
    if ACCESS_MODE == "closed":
        return False
    return uid in approved_users


def _sync_approve(uid: int | str, info: dict):
    approved_users[uid] = info
    db.approve_user(uid, info.get("name", ""), info.get("username", ""))


def _sync_revoke(uid: int | str):
    approved_users.pop(uid, None)
    db.revoke_user(uid)


# ── Conversations ────────────────────────────────────────────────────────────

async def _get_user_lock(user_id: int | str) -> asyncio.Lock:
    if user_id not in _user_locks:
        async with _locks_lock:
            if user_id not in _user_locks:
                _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _load_conversations_from_db():
    rows = db._conn.execute("SELECT user_id, messages FROM conversations").fetchall()
    for row in rows:
        try:
            uid = int(row["user_id"])
        except (ValueError, TypeError):
            uid = str(row["user_id"])
        conversations[uid] = json.loads(row["messages"])


# ── Clientes de API ──────────────────────────────────────────────────────────

def _make_async_client() -> anthropic.AsyncAnthropic:
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
        "Sem credenciais Anthropic. Configure ANTHROPIC_API_KEY ou faça login no Claude Code."
    )


def _make_openrouter_client():
    from openai import AsyncOpenAI
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        default_headers={"X-Title": f"SMBCLAW-{BOT_NAME}"},
    )


def _refresh_codex_token(auth: dict) -> dict | None:
    import urllib.request
    refresh_token = auth.get("tokens", {}).get("refresh_token", "")
    if not refresh_token:
        return None
    try:
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": _CODEX_CLIENT_ID,
        }).encode()
        req = urllib.request.Request(
            _CODEX_TOKEN_URL, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read())
        auth["tokens"]["access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            auth["tokens"]["refresh_token"] = tokens["refresh_token"]
        if tokens.get("id_token"):
            auth["tokens"]["id_token"] = tokens["id_token"]
        auth["last_refresh"] = datetime.now().isoformat()
        _CODEX_AUTH_PATH.write_text(json.dumps(auth, indent=2))
        os.chmod(_CODEX_AUTH_PATH, 0o600)
        logger.info("Codex OAuth token refreshed successfully")
        return auth
    except Exception as e:
        logger.warning(f"Codex token refresh failed: {e}")
        return None


def _make_codex_client():
    from openai import AsyncOpenAI
    if OPENAI_API_KEY:
        return AsyncOpenAI(api_key=OPENAI_API_KEY)
    if _CODEX_AUTH_PATH.exists():
        try:
            with open(_CODEX_AUTH_PATH) as f:
                auth = json.load(f)
            token = auth.get("tokens", {}).get("access_token", "")
            account_id = auth.get("account_id", "")
            if token:
                headers = {}
                if account_id:
                    headers["ChatGPT-Account-Id"] = account_id
                return AsyncOpenAI(
                    api_key=token,
                    base_url="https://chatgpt.com/backend-api/wham",
                    default_headers=headers,
                )
        except Exception as e:
            logger.warning(f"Falha ao ler credenciais do Codex: {e}")
    raise RuntimeError("Sem credenciais OpenAI. Configure OPENAI_API_KEY ou faça login no Codex CLI.")


def _is_codex_oauth() -> bool:
    return not OPENAI_API_KEY and _CODEX_AUTH_PATH.exists()


# ── Conversão de formatos ────────────────────────────────────────────────────

def _anthropic_tools_to_responses(tools: list) -> list:
    result = []
    for t in tools:
        result.append({
            "type": "function",
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        })
    return result


def _anthropic_tools_to_openai(tools: list) -> list:
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


def _has_media_content(content) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(i, dict) and i.get("type") == "image" for i in content)


def _convert_content_for_openai(content):
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


def _extract_document_text(file_path: str, filename: str, max_chars: int = 50000) -> str:
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
    size = Path(file_path).stat().st_size
    return f"[Arquivo binário — {size // 1024} KB]"


async def _describe_image_for_cli(image_path: str) -> str:
    _EXT_TO_MIME = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".tiff": "image/tiff", ".tif": "image/tiff",
    }
    media_type = _EXT_TO_MIME.get(Path(image_path).suffix.lower(), "image/jpeg")
    try:
        with open(image_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode()
        if ANTHROPIC_API_KEY:
            client = _make_async_client()
            response = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": img_data}},
                    {"type": "text", "text": "Descreva esta imagem em detalhes em português."},
                ]}],
            )
            return next((b.text for b in response.content if b.type == "text"), "[imagem]")
        elif OPENROUTER_API_KEY or (OPENAI_API_KEY or _CODEX_AUTH_PATH.exists()):
            from openai import AsyncOpenAI
            if OPENROUTER_API_KEY:
                oai_client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=OPENROUTER_API_KEY, default_headers={"X-Title": f"SMBCLAW-{BOT_NAME}"})
                vision_model = "google/gemini-2.0-flash-001"
            else:
                oai_client = _make_codex_client()
                vision_model = "gpt-5.1-codex-mini"
            if _is_codex_oauth() and not OPENROUTER_API_KEY:
                vision_content = [
                    {"type": "input_image", "image_url": f"data:{media_type};base64,{img_data}"},
                    {"type": "input_text", "text": "Descreva esta imagem em detalhes em português."},
                ]
                stream = await oai_client.responses.create(
                    model=vision_model, store=False, stream=True,
                    input=[{"role": "user", "content": vision_content}],
                )
                vis_text = ""
                async for event in stream:
                    if getattr(event, "type", "") == "response.output_text.delta":
                        vis_text += getattr(event, "delta", "")
                return vis_text or "[imagem]"
            else:
                vision_content = [
                    {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{img_data}"}},
                    {"type": "text", "text": "Descreva esta imagem em detalhes em português."},
                ]
                response = await oai_client.chat.completions.create(
                    model=vision_model, max_tokens=512,
                    messages=[{"role": "user", "content": vision_content}],
                )
                return response.choices[0].message.content or "[imagem]"
    except Exception as e:
        logger.warning(f"[vision/cli] Erro ao descrever imagem: {e}")
    return "[imagem não pôde ser descrita]"


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE DE IA (LOOP AGÊNTICO)
# ═══════════════════════════════════════════════════════════════════════════════

async def _ask_anthropic(messages: list, user_id: int | str = 0, notify_fn=None, on_action=None) -> str:
    system = get_system_prompt(user_id)
    t0 = time.monotonic()
    total_input = total_output = total_tool_calls = 0
    error_str = ""
    _msg_content = messages[-1].get("content", "") if messages else ""
    _msg_preview = (_msg_content if isinstance(_msg_content, str) else str(_msg_content))[:200]
    _trace = tracer.start_trace(BOT_NAME, user_id, _msg_preview)
    try:
        _THINKING_BUDGETS = {"low": 2000, "medium": 6000, "high": 16000}
        _thinking_level = _thinking_levels.get(user_id, "off")
        _thinking_budget = _THINKING_BUDGETS.get(_thinking_level)
        for _iter in range(20):
            _max_tokens = 4096
            if _thinking_budget:
                _max_tokens = _thinking_budget + 4096
            kwargs = dict(model=MODEL, max_tokens=_max_tokens, system=system, messages=messages)
            if _thinking_budget:
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": _thinking_budget}
            if TOOL_DEFINITIONS:
                kwargs["tools"] = TOOL_DEFINITIONS
            client = _make_async_client()
            _llm_span = tracer.add_span(_trace, "llm:anthropic", f"iter={_iter}")
            response = await client.messages.create(**kwargs)
            _tok_in = getattr(response.usage, "input_tokens", 0)
            _tok_out = getattr(response.usage, "output_tokens", 0)
            total_input += _tok_in
            total_output += _tok_out
            if response.stop_reason == "end_turn":
                _text = next((b.text for b in response.content if b.type == "text"), "")
                tracer.end_span(_llm_span, _text[:200], tokens_in=_tok_in, tokens_out=_tok_out)
                return _text
            if response.stop_reason == "tool_use":
                _tool_names = [b.name for b in response.content if getattr(b, "type", "") == "tool_use"]
                tracer.end_span(_llm_span, f"tool_use: {_tool_names}", tokens_in=_tok_in, tokens_out=_tok_out)
                messages.append({"role": "assistant", "content": response.content})
                results = []
                for block in response.content:
                    if block.type == "tool_use":
                        total_tool_calls += 1
                        logger.info(f"[tool] {block.name} {json.dumps(block.input)[:120]}")
                        if notify_fn:
                            try:
                                await notify_fn(block.name, block.input)
                            except Exception:
                                pass
                        _tool_span = tracer.add_span(_trace, f"tool:{block.name}", json.dumps(block.input)[:200])
                        result = await tool_registry.execute(
                            block.name, block.input,
                            user_id=user_id, db=db, config=TOOL_CONFIG,
                            on_action=on_action,
                        )
                        _r = result if isinstance(result, str) else str(result)
                        if len(_r) > 12000:
                            _r = _r[:12000] + f"\n\n[...output truncado: {len(_r)} chars total]"
                        tracer.end_span(_tool_span, _r[:200])
                        results.append({"type": "tool_result", "tool_use_id": block.id, "content": _r})
                messages.append({"role": "user", "content": results})
                continue
            tracer.end_span(_llm_span, "", tokens_in=_tok_in, tokens_out=_tok_out)
            break
        return next((b.text for b in response.content if hasattr(b, "text")), "")
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        _trace.error = error_str
        raise
    finally:
        tracer.end_trace(_trace, db)
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, total_input, total_output,
                         total_tool_calls, latency, error_str)
        except Exception:
            pass


async def _ask_openrouter(messages: list, user_id: int | str = 0, notify_fn=None, on_action=None) -> str:
    system = get_system_prompt(user_id)
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
    _msg_content = messages[-1].get("content", "") if messages else ""
    _msg_preview = (_msg_content if isinstance(_msg_content, str) else str(_msg_content))[:200]
    _trace = tracer.start_trace(BOT_NAME, user_id, _msg_preview)
    try:
        for _iter in range(20):
            kwargs = dict(model=MODEL, messages=oai_messages, max_tokens=4096)
            if oai_tools:
                kwargs["tools"] = oai_tools
            _llm_span = tracer.add_span(_trace, "llm:openrouter", f"iter={_iter}")
            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            _tok_in = getattr(response.usage, "prompt_tokens", 0)
            _tok_out = getattr(response.usage, "completion_tokens", 0)
            total_input += _tok_in
            total_output += _tok_out
            if choice.finish_reason == "stop":
                _text = choice.message.content or ""
                tracer.end_span(_llm_span, _text[:200], tokens_in=_tok_in, tokens_out=_tok_out)
                return _text
            if choice.finish_reason == "tool_calls":
                _tool_names = [tc.function.name for tc in (choice.message.tool_calls or [])]
                tracer.end_span(_llm_span, f"tool_calls: {_tool_names}", tokens_in=_tok_in, tokens_out=_tok_out)
                oai_messages.append(choice.message)
                for tc in choice.message.tool_calls or []:
                    total_tool_calls += 1
                    try:
                        tool_input = json.loads(tc.function.arguments)
                    except Exception:
                        tool_input = {}
                    logger.info(f"[tool/openrouter] {tc.function.name} {json.dumps(tool_input)[:120]}")
                    if notify_fn:
                        try:
                            await notify_fn(tc.function.name, tool_input)
                        except Exception:
                            pass
                    _tool_span = tracer.add_span(_trace, f"tool:{tc.function.name}", json.dumps(tool_input)[:200])
                    result = await tool_registry.execute(
                        tc.function.name, tool_input,
                        user_id=user_id, db=db, config=TOOL_CONFIG,
                        on_action=on_action,
                    )
                    _r = result if isinstance(result, str) else str(result)
                    if len(_r) > 12000:
                        _r = _r[:12000] + f"\n\n[...output truncado: {len(_r)} chars total]"
                    tracer.end_span(_tool_span, _r[:200])
                    oai_messages.append({"role": "tool", "tool_call_id": tc.id, "content": _r})
                continue
            tracer.end_span(_llm_span, "", tokens_in=_tok_in, tokens_out=_tok_out)
            break
        return choice.message.content or ""
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        _trace.error = error_str
        raise
    finally:
        tracer.end_trace(_trace, db)
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, total_input, total_output,
                         total_tool_calls, latency, error_str)
        except Exception:
            pass


async def _ask_codex_responses(messages: list, user_id: int | str = 0, notify_fn=None, on_action=None) -> str:
    system = get_system_prompt(user_id)
    resp_input = []
    for m in messages:
        if not (isinstance(m.get("content"), str) or _has_media_content(m.get("content"))):
            continue
        content = _convert_content_for_openai(m["content"])
        if isinstance(content, list):
            wham_content = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    wham_content.append({"type": "input_text", "text": item.get("text", "")})
                else:
                    wham_content.append(item)
            resp_input.append({"role": m["role"], "content": wham_content})
        else:
            resp_input.append({"role": m["role"], "content": content})

    resp_tools = _anthropic_tools_to_responses(TOOL_DEFINITIONS) if TOOL_DEFINITIONS else None
    client = _make_codex_client()
    t0 = time.monotonic()
    total_input = total_output = total_tool_calls = 0
    error_str = ""
    _refreshed = False
    _msg_content = messages[-1].get("content", "") if messages else ""
    _msg_preview = (_msg_content if isinstance(_msg_content, str) else str(_msg_content))[:200]
    _trace = tracer.start_trace(BOT_NAME, user_id, _msg_preview)
    try:
        for _iter in range(20):
            kwargs = dict(model=MODEL, instructions=system, input=resp_input, store=False, stream=True)
            if resp_tools:
                kwargs["tools"] = resp_tools
            _llm_span = tracer.add_span(_trace, "llm:codex-responses", f"iter={_iter}")
            try:
                stream = await client.responses.create(**kwargs)
            except Exception as _api_err:
                if not _refreshed and "403" in str(_api_err) and _CODEX_AUTH_PATH.exists():
                    auth = json.loads(_CODEX_AUTH_PATH.read_text())
                    refreshed = _refresh_codex_token(auth)
                    if refreshed:
                        client = _make_codex_client()
                        _refreshed = True
                        stream = await client.responses.create(**kwargs)
                    else:
                        raise
                else:
                    raise
            text_parts = []
            _cur_text = {}
            _final_output = []
            _iter_tok_in = _iter_tok_out = 0
            async for event in stream:
                ev_type = getattr(event, "type", "")
                if ev_type == "response.output_text.delta":
                    idx = getattr(event, "content_index", 0)
                    _cur_text[idx] = _cur_text.get(idx, "") + getattr(event, "delta", "")
                elif ev_type == "response.output_text.done":
                    idx = getattr(event, "content_index", 0)
                    text_parts.append(_cur_text.pop(idx, getattr(event, "text", "")))
                elif ev_type == "response.completed":
                    resp_obj = getattr(event, "response", None)
                    if resp_obj and hasattr(resp_obj, "usage"):
                        _iter_tok_in = getattr(resp_obj.usage, "input_tokens", 0)
                        _iter_tok_out = getattr(resp_obj.usage, "output_tokens", 0)
                        total_input += _iter_tok_in
                        total_output += _iter_tok_out
                    if resp_obj:
                        _final_output = getattr(resp_obj, "output", [])
            tool_calls = []
            for item in _final_output:
                if getattr(item, "type", "") == "function_call":
                    tool_calls.append(item)
            if not tool_calls:
                _result_text = "\n".join(text_parts) or ""
                tracer.end_span(_llm_span, _result_text[:200], tokens_in=_iter_tok_in, tokens_out=_iter_tok_out)
                return _result_text
            _tool_names = [getattr(tc, "name", "?") for tc in tool_calls]
            tracer.end_span(_llm_span, f"tool_calls: {_tool_names}", tokens_in=_iter_tok_in, tokens_out=_iter_tok_out)
            for item in _final_output:
                item_type = getattr(item, "type", "")
                if item_type == "function_call":
                    resp_input.append({
                        "type": "function_call",
                        "name": item.name,
                        "arguments": item.arguments,
                        "call_id": item.call_id,
                    })
                elif item_type == "message":
                    msg_content = []
                    for c in getattr(item, "content", []):
                        if getattr(c, "type", "") == "output_text":
                            msg_content.append({"type": "output_text", "text": c.text})
                    if msg_content:
                        resp_input.append({"type": "message", "role": "assistant", "content": msg_content})
            for tc in tool_calls:
                total_tool_calls += 1
                try:
                    tool_input = json.loads(tc.arguments)
                except Exception:
                    tool_input = {}
                logger.info(f"[tool/codex-resp] {tc.name} {json.dumps(tool_input)[:120]}")
                if notify_fn:
                    try:
                        await notify_fn(tc.name, tool_input)
                    except Exception:
                        pass
                _tool_span = tracer.add_span(_trace, f"tool:{tc.name}", json.dumps(tool_input)[:200])
                result = await tool_registry.execute(
                    tc.name, tool_input,
                    user_id=user_id, db=db, config=TOOL_CONFIG,
                    on_action=on_action,
                )
                _tool_out = result if isinstance(result, str) else str(result)
                if len(_tool_out) > 12000:
                    _tool_out = _tool_out[:12000] + f"\n\n[...output truncado: {len(_tool_out)} chars total]"
                tracer.end_span(_tool_span, _tool_out[:200])
                resp_input.append({
                    "type": "function_call_output",
                    "call_id": tc.call_id,
                    "output": _tool_out,
                })
            continue
        return "\n".join(text_parts) or ""
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        _trace.error = error_str
        raise
    finally:
        tracer.end_trace(_trace, db)
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, total_input, total_output,
                         total_tool_calls, latency, error_str)
        except Exception:
            pass


async def _ask_codex(messages: list, user_id: int | str = 0, notify_fn=None, on_action=None) -> str:
    if _is_codex_oauth():
        return await _ask_codex_responses(messages, user_id, notify_fn=notify_fn, on_action=on_action)
    system = get_system_prompt(user_id)
    oai_messages = [{"role": "system", "content": system}] + [
        {"role": m["role"], "content": _convert_content_for_openai(m["content"])}
        for m in messages
        if isinstance(m.get("content"), str) or _has_media_content(m.get("content"))
    ]
    oai_tools = _anthropic_tools_to_openai(TOOL_DEFINITIONS) if TOOL_DEFINITIONS else None
    client = _make_codex_client()
    t0 = time.monotonic()
    total_input = total_output = total_tool_calls = 0
    error_str = ""
    _msg_content = messages[-1].get("content", "") if messages else ""
    _msg_preview = (_msg_content if isinstance(_msg_content, str) else str(_msg_content))[:200]
    _trace = tracer.start_trace(BOT_NAME, user_id, _msg_preview)
    try:
        for _iter in range(20):
            kwargs = dict(model=MODEL, messages=oai_messages, max_tokens=4096)
            if oai_tools:
                kwargs["tools"] = oai_tools
            _llm_span = tracer.add_span(_trace, "llm:codex", f"iter={_iter}")
            response = await client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            _tok_in = getattr(response.usage, "prompt_tokens", 0)
            _tok_out = getattr(response.usage, "completion_tokens", 0)
            total_input += _tok_in
            total_output += _tok_out
            if choice.finish_reason == "stop":
                _text = choice.message.content or ""
                tracer.end_span(_llm_span, _text[:200], tokens_in=_tok_in, tokens_out=_tok_out)
                return _text
            if choice.finish_reason == "tool_calls":
                _tool_names = [tc.function.name for tc in (choice.message.tool_calls or [])]
                tracer.end_span(_llm_span, f"tool_calls: {_tool_names}", tokens_in=_tok_in, tokens_out=_tok_out)
                oai_messages.append(choice.message)
                for tc in choice.message.tool_calls or []:
                    total_tool_calls += 1
                    try:
                        tool_input = json.loads(tc.function.arguments)
                    except Exception:
                        tool_input = {}
                    logger.info(f"[tool/codex] {tc.function.name} {json.dumps(tool_input)[:120]}")
                    _tool_span = tracer.add_span(_trace, f"tool:{tc.function.name}", json.dumps(tool_input)[:200])
                    result = await tool_registry.execute(
                        tc.function.name, tool_input,
                        user_id=user_id, db=db, config=TOOL_CONFIG,
                        on_action=on_action,
                    )
                    _r = result if isinstance(result, str) else str(result)
                    if len(_r) > 12000:
                        _r = _r[:12000] + f"\n\n[...output truncado: {len(_r)} chars total]"
                    tracer.end_span(_tool_span, _r[:200])
                    oai_messages.append({"role": "tool", "tool_call_id": tc.id, "content": _r})
                continue
            tracer.end_span(_llm_span, "", tokens_in=_tok_in, tokens_out=_tok_out)
            break
        return choice.message.content or ""
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        _trace.error = error_str
        raise
    finally:
        tracer.end_trace(_trace, db)
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, total_input, total_output,
                         total_tool_calls, latency, error_str)
        except Exception:
            pass


async def _ask_cli(messages: list, user_id: int | str = 0, notify_fn=None) -> str:
    last_user = next(
        (m["content"] for m in reversed(messages) if m["role"] == "user" and isinstance(m.get("content"), str)),
        "",
    )
    if not last_user:
        raise RuntimeError("Nenhuma mensagem do usuário encontrada.")

    session_id = _cli_sessions.get(user_id)
    t0 = time.monotonic()
    error_str = ""
    _trace = tracer.start_trace(BOT_NAME, user_id, last_user[:200])
    _cli_span = tracer.add_span(_trace, "llm:claude-cli", last_user[:200])
    try:
        env = {
            **os.environ,
            "CLAUDECODE": "",
            "CLAUDE_CODE_ENTRYPOINT": "cli",
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "delta",
        }
        identity_reminder = f"Lembre-se: seu nome nesta plataforma é {BOT_NAME}. Apresente-se sempre como {BOT_NAME}."
        _CLI_THINKING_BUDGETS = {"low": 2000, "medium": 6000, "high": 16000}
        _cli_thinking = _thinking_levels.get(user_id, "off")
        base_flags = [
            "claude", "-p",
            "--model", MODEL,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
        ]
        if _cli_thinking in _CLI_THINKING_BUDGETS:
            base_flags += ["--thinking", str(_CLI_THINKING_BUDGETS[_cli_thinking])]
        if session_id:
            cmd = [*base_flags, "--resume", session_id, "--append-system-prompt", identity_reminder, last_user]
        else:
            system = get_system_prompt()
            cmd = [*base_flags, "--system-prompt", system, last_user]

        logger.info(f"[cli] cmd: {' '.join(cmd[:8])}...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(WORK_DIR),
            limit=4 * 1024 * 1024,
            start_new_session=True,
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

        def _kill_proc_group():
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except ProcessLookupError:
                pass

        try:
            await asyncio.wait_for(
                asyncio.gather(_read_stdout(), _read_stderr()),
                timeout=1805,
            )
        except asyncio.TimeoutError:
            _kill_proc_group()
            await proc.wait()
            raise RuntimeError("claude CLI excedeu limite de 30 minutos")
        except asyncio.CancelledError:
            _kill_proc_group()
            raise

        await proc.wait()
        if stderr_text:
            logger.info(f"[cli] stderr: {stderr_text[:300]}")
        if proc.returncode != 0 and not result_text:
            raise RuntimeError(f"claude CLI saiu com código {proc.returncode}: {stderr_text[:300]}")

        tracer.end_span(_cli_span, result_text[:200])
        return result_text
    except Exception as e:
        error_str = f"{type(e).__name__}: {e}"
        _trace.error = error_str
        tracer.end_span(_cli_span, "", error=error_str[:200])
        raise
    finally:
        tracer.end_trace(_trace, db)
        _cli_procs.pop(user_id, None)
        latency = int((time.monotonic() - t0) * 1000)
        try:
            db.log_event(BOT_NAME, user_id, 0, 0, 0, latency, error_str)
        except Exception:
            pass


async def ask_claude(messages: list, user_id: int | str = 0, notify_fn=None, on_action=None) -> str:
    """Roteador principal: delega para o provider configurado."""
    if PROVIDER == "openrouter":
        return await _ask_openrouter(messages, user_id, notify_fn=notify_fn, on_action=on_action)
    if PROVIDER == "codex":
        return await _ask_codex(messages, user_id, notify_fn=notify_fn, on_action=on_action)
    if PROVIDER == "claude-cli":
        return await _ask_cli(messages, user_id, notify_fn=notify_fn)
    return await _ask_anthropic(messages, user_id, notify_fn=notify_fn, on_action=on_action)
