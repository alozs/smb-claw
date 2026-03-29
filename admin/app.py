import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent.parent
BOTS_DIR = BASE_DIR / "bots"
SUBAGENTS_DIR = BASE_DIR / "subagents"
TEMPLATES_DIR = BASE_DIR / "templates"

IN_DOCKER = Path("/.dockerenv").exists() or bool(os.environ.get("IN_DOCKER"))

FILE_WHITELIST = {"soul.md", "USER.md", "MEMORY.md", "welcome.md", "secrets.env"}
GLOBAL_WHITELIST = {"context.global", "config.global", "secrets.global"}

SENSITIVE_KEYS = {"ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY", "NOTION_API_KEY"}

# Chaves que sempre aparecem como placeholder em secrets.global, mesmo que ainda não configuradas
KNOWN_GLOBAL_SECRETS = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "NOTION_API_KEY"]

# Variáveis que o editor genérico não pode remover (apenas editar o valor)
SYSTEM_GLOBAL_KEYS: dict[str, set[str]] = {
    "config.global": {
        "PROVIDER", "ADMIN_ID", "MODEL", "ACCESS_MODE",
        "BUGFIXER_ENABLED", "BUGFIXER_TIMES_PER_DAY", "BUGFIXER_TELEGRAM_TOKEN",
        "ADMIN_PANEL_URL",
    },
    "secrets.global": set(),
}

app = FastAPI(title="Claude Bots Admin")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# ── Auth: acesso temporário por token ────────────────────────────────────────

def _load_admin_env() -> dict:
    env_path = Path(__file__).resolve().parent / ".env.admin"
    result = {}
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    return result

_admin_env = _load_admin_env()
AUTH_SECRET = _admin_env.get("ADMIN_PASSWORD", secrets.token_urlsafe(32))
TOKEN_TTL = int(_admin_env.get("TOKEN_TTL", "1800"))  # 30 min default
COOKIE_NAME = "admin_session"

# In-memory token store: {token: expiry_timestamp}
# Nota: funciona com --workers 1 (default do systemd service)
_valid_tokens: dict[str, float] = {}


def generate_access_token(ttl: int = TOKEN_TTL) -> str:
    raw = secrets.token_urlsafe(32)
    sig = hmac.new(AUTH_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    token = f"{raw}.{sig}"
    _valid_tokens[token] = time.time() + ttl
    _prune_expired_tokens()
    return token


def _prune_expired_tokens():
    now = time.time()
    expired = [t for t, exp in _valid_tokens.items() if exp <= now]
    for t in expired:
        del _valid_tokens[t]


def validate_token(token: str) -> bool:
    if token not in _valid_tokens:
        return False
    if time.time() > _valid_tokens[token]:
        del _valid_tokens[token]
        return False
    return True


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Endpoint interno de geração de token (protegido por IP no handler)
        if request.url.path == "/api/gen-token":
            return await call_next(request)

        # 1. Cookie de sessão válido
        session_token = request.cookies.get(COOKIE_NAME)
        if session_token and validate_token(session_token):
            return await call_next(request)

        # 2. Query param ?token= (link temporário)
        query_token = request.query_params.get("token")
        if query_token and validate_token(query_token):
            redirect_path = request.url.path or "/"
            response = RedirectResponse(url=redirect_path, status_code=302)
            ttl_remaining = int(_valid_tokens.get(query_token, time.time()) - time.time())
            response.set_cookie(
                COOKIE_NAME, query_token,
                max_age=max(ttl_remaining, 60),
                httponly=True, samesite="lax",
            )
            return response

        # 3. Sem autenticação — redireciona para landing page
        return RedirectResponse(url="https://alozs.github.io/smb-claw/", status_code=302)


app.add_middleware(AuthMiddleware)


# ── Admin DB (SQLite) ────────────────────────────────────────────────────────

ADMIN_DB_PATH = Path(__file__).resolve().parent / "admin.db"


def _get_admin_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ADMIN_DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _init_admin_db():
    conn = _get_admin_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS architect_conversations (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            messages TEXT NOT NULL DEFAULT '[]',
            blueprint TEXT,
            selected_models TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


_init_admin_db()


def validate_bot_name(name: str):
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise HTTPException(400, detail="Invalid bot name")
    if not (BOTS_DIR / name).is_dir():
        raise HTTPException(404, detail="Bot not found")


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_env(path: Path) -> dict:
    """Parse a .env file into a dict, skipping comments and blanks."""
    result = {}
    if not path.exists():
        return result
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def write_env(path: Path, fields: dict):
    """Write env fields preserving comments from existing file."""
    existing_lines = []
    if path.exists():
        existing_lines = path.read_text(errors="replace").splitlines()

    existing_keys = set()
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            existing_keys.add(k)
            if k in fields:
                new_lines.append(f"{k}={fields[k]}")
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    for k, v in fields.items():
        if k not in existing_keys:
            new_lines.append(f"{k}={v}")

    path.write_text("\n".join(new_lines) + "\n")


def mask_sensitive(key: str, value: str) -> str:
    if key in SENSITIVE_KEYS and value and not value.startswith("SEU_"):
        visible = value[:4] if len(value) > 8 else ""
        return visible + "****"
    return value


def unmask_sensitive(key: str, new_value: str, old_value: str) -> str:
    """If new value contains ****, keep the old value."""
    if "****" in new_value:
        return old_value
    return new_value


def get_bot_env(bot_name: str) -> dict:
    env_path = BOTS_DIR / bot_name / ".env"
    return load_env(env_path)


def get_bot_env_effective(bot_name: str) -> dict:
    """Load bot env with global defaults applied (mirrors bot.py precedence)."""
    merged = {}
    global_cfg = BASE_DIR / "config.global"
    if global_cfg.exists():
        for k, v in load_env(global_cfg).items():
            merged.setdefault(k, v)
    env_path = BOTS_DIR / bot_name / ".env"
    merged.update(load_env(env_path))
    return merged


def _format_uptime(dt: datetime) -> str:
    """Format a datetime into a human-readable uptime string."""
    delta = datetime.now() - dt
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "—"
    if total_seconds < 60:
        return f"{total_seconds}s"
    elif total_seconds < 3600:
        return f"{total_seconds // 60}m"
    elif total_seconds < 86400:
        h = total_seconds // 3600
        m = (total_seconds % 3600) // 60
        return f"{h}h {m}m"
    else:
        d = total_seconds // 86400
        h = (total_seconds % 86400) // 3600
        return f"{d}d {h}h"


def get_uptime(bot_name: str) -> str:
    """Return human-readable uptime string or '—' if inactive."""
    if IN_DOCKER:
        started_file = BOTS_DIR / bot_name / ".started"
        if not started_file.exists():
            return "—"
        try:
            dt = datetime.strptime(started_file.read_text().strip(), "%Y-%m-%dT%H:%M:%S")
            return _format_uptime(dt)
        except Exception:
            return "—"

    service = f"claude-bot-{bot_name}"
    try:
        result = subprocess.run(
            ["systemctl", "show", service, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5
        )
        line = result.stdout.strip()
        if "=" not in line:
            return "—"
        ts_str = line.split("=", 1)[1].strip()
        if not ts_str or ts_str == "n/a":
            return "—"
        parts = ts_str.split()
        if len(parts) < 3:
            return "—"
        dt_str = f"{parts[1]} {parts[2]}"
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        return _format_uptime(dt)
    except Exception:
        return "—"


def _get_bot_script(bot_name: str) -> str:
    """Returns the correct bot script (bot.py or whatsapp_bot.py) based on CHANNEL."""
    env = get_bot_env(bot_name)
    channel = env.get("CHANNEL", "telegram")
    if channel == "whatsapp":
        return str(BASE_DIR / "whatsapp_bot.py")
    return str(BASE_DIR / "bot.py")


def get_bot_summary(bot_name: str) -> dict:
    env = get_bot_env_effective(bot_name)
    if IN_DOCKER:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "--", f"--bot-dir.*bots/{bot_name}"],
                capture_output=True, text=True, timeout=5
            )
            active = result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            active = False
    else:
        service = f"claude-bot-{bot_name}"
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service],
                capture_output=True, text=True, timeout=5
            )
            active = result.stdout.strip() == "active"
        except Exception:
            active = False

    msgs_today = 0
    db_path = BOTS_DIR / bot_name / "bot_data.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("PRAGMA busy_timeout=3000")
            cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            row = conn.execute(
                "SELECT COUNT(*) FROM analytics WHERE ts >= ?", (cutoff,)
            ).fetchone()
            msgs_today = row[0] if row else 0
            conn.close()
        except Exception:
            pass

    provider = env.get("PROVIDER", "claude-cli")
    model = env.get("MODEL", "")
    tools_raw = env.get("TOOLS", "none")
    tools_list = [t.strip() for t in tools_raw.split(",") if t.strip() and t.strip() != "none"]
    access_mode = env.get("ACCESS_MODE", "open")
    bot_dir = BOTS_DIR / bot_name
    has_avatar = any(bot_dir.glob("avatar.*"))
    uptime = get_uptime(bot_name) if active else "—"

    # Subagentes com acesso a este bot
    subagents = []
    sa_dir = BASE_DIR / "subagents"
    if sa_dir.exists():
        for sa in sorted(sa_dir.iterdir()):
            if not sa.is_dir():
                continue
            sa_env_path = sa / ".env"
            if not sa_env_path.exists():
                continue
            sa_env = {}
            for line in sa_env_path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    sa_env[k.strip()] = v.strip()
            allowed = sa_env.get("ALLOWED_PARENTS", "*")
            if allowed == "*" or bot_name in [x.strip() for x in allowed.split(",")]:
                subagents.append(sa.name)

    display_name = env.get("BOT_NAME", "").strip() or bot_name
    description = env.get("DESCRIPTION", "").strip()

    channel = env.get("CHANNEL", "telegram")

    return {
        "name": bot_name,
        "display_name": display_name,
        "description": description,
        "active": active,
        "channel": channel,
        "provider": provider,
        "model": model,
        "tools": tools_list,
        "access_mode": access_mode,
        "uptime": uptime,
        "msgs_today": msgs_today,
        "has_avatar": has_avatar,
        "subagents": subagents,
    }


def get_analytics(bot_name: str, days: int) -> dict:
    db_path = BOTS_DIR / bot_name / "bot_data.db"
    if not db_path.exists():
        return {"msgs": 0, "input_tokens": 0, "output_tokens": 0,
                "tool_calls": 0, "errors": 0, "cost_usd": 0.0}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=5000")
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        row = conn.execute("""
            SELECT COUNT(*) as msgs,
                   COALESCE(SUM(input_tokens),0)  as inp,
                   COALESCE(SUM(output_tokens),0) as out,
                   COALESCE(SUM(tool_calls),0)    as tools,
                   SUM(CASE WHEN error!='' THEN 1 ELSE 0 END) as errors
            FROM analytics WHERE ts >= ?
        """, (cutoff,)).fetchone()
        conn.close()
        cost = (row[1] * 3.0 + row[2] * 15.0) / 1_000_000
        return {
            "msgs": row[0], "input_tokens": row[1], "output_tokens": row[2],
            "tool_calls": row[3], "errors": row[4], "cost_usd": round(cost, 4),
        }
    except Exception as e:
        return {"msgs": 0, "input_tokens": 0, "output_tokens": 0,
                "tool_calls": 0, "errors": 0, "cost_usd": 0.0, "error": str(e)}


# ── Routes: Auth ──────────────────────────────────────────────────────────────

@app.post("/api/gen-token")
async def gen_token(request: Request):
    """Gera token de acesso temporário. Apenas localhost."""
    client = request.client
    if not client or client.host not in ("127.0.0.1", "::1"):
        raise HTTPException(403, detail="Only localhost allowed")
    ttl = TOKEN_TTL
    try:
        body = await request.json()
        ttl = int(body.get("ttl", TOKEN_TTL))
    except Exception:
        pass
    token = generate_access_token(ttl)
    return {"token": token, "ttl": ttl}


# ── Routes: UI ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    resp = templates.TemplateResponse("index.html", {"request": request})
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ── Routes: Bots ──────────────────────────────────────────────────────────────

@app.get("/api/templates")
async def list_templates():
    templates = []
    if TEMPLATES_DIR.exists():
        for d in sorted(TEMPLATES_DIR.iterdir()):
            if d.is_dir() and (d / "meta.json").exists():
                try:
                    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                soul = ""
                soul_path = d / "soul.md"
                if soul_path.exists():
                    soul = soul_path.read_text(encoding="utf-8")
                templates.append({**meta, "soul": soul})
    return templates


@app.get("/api/bots")
async def list_bots():
    bots = []
    if BOTS_DIR.exists():
        for d in sorted(BOTS_DIR.iterdir()):
            if d.is_dir():
                bots.append(get_bot_summary(d.name))
    return bots


@app.get("/api/bots/{name}")
async def get_bot(name: str):
    validate_bot_name(name)
    summary = get_bot_summary(name)
    env = get_bot_env(name)
    uptime = get_uptime(name) if summary["active"] else "—"
    return {**summary, "env": env, "uptime": uptime}


AVATAR_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
               ".webp": "image/webp", ".gif": "image/gif"}


@app.get("/api/bots/{name}/avatar")
async def get_avatar(name: str):
    validate_bot_name(name)
    bot_dir = BOTS_DIR / name
    for f in bot_dir.glob("avatar.*"):
        mime = AVATAR_MIME.get(f.suffix.lower(), "image/jpeg")
        return FileResponse(str(f), media_type=mime, headers={"Cache-Control": "no-cache"})
    raise HTTPException(404, detail="No avatar")


@app.post("/api/bots/{name}/avatar")
async def upload_avatar(name: str, file: UploadFile = File(...)):
    validate_bot_name(name)
    ext = Path(file.filename).suffix.lower() if file.filename else ".jpg"
    if ext not in AVATAR_MIME:
        raise HTTPException(400, detail="Formato inválido. Use jpg, png, webp ou gif.")
    bot_dir = BOTS_DIR / name
    # Remove old avatars
    for old in bot_dir.glob("avatar.*"):
        old.unlink(missing_ok=True)
    dest = bot_dir / f"avatar{ext}"
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(400, detail="Imagem muito grande. Máximo 5 MB.")
    dest.write_bytes(contents)
    return {"ok": True, "has_avatar": True}


@app.delete("/api/bots/{name}/avatar")
async def delete_avatar(name: str):
    validate_bot_name(name)
    bot_dir = BOTS_DIR / name
    for f in bot_dir.glob("avatar.*"):
        f.unlink(missing_ok=True)
    return {"ok": True, "has_avatar": False}


# ── Routes: WhatsApp ─────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/whatsapp/qr")
async def get_whatsapp_qr(name: str):
    validate_bot_name(name)
    qr_path = BOTS_DIR / name / "whatsapp_qr.png"
    if not qr_path.exists():
        raise HTTPException(404, detail="QR code não disponível")
    return FileResponse(str(qr_path), media_type="image/png",
                        headers={"Cache-Control": "no-cache"})


@app.get("/api/bots/{name}/whatsapp/status")
async def get_whatsapp_status(name: str):
    validate_bot_name(name)
    status_path = BOTS_DIR / name / "whatsapp_status.json"
    if not status_path.exists():
        return {"status": "unknown", "connected": False}
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"status": "unknown", "connected": False}


@app.post("/api/bots/{name}/whatsapp/logout")
async def whatsapp_logout(name: str):
    validate_bot_name(name)
    # Sinaliza logout escrevendo arquivo que o whatsapp_bot.py detecta
    logout_path = BOTS_DIR / name / "whatsapp_logout"
    logout_path.write_text("logout", encoding="utf-8")
    return {"ok": True, "message": "Logout sinalizado. O bot será desconectado."}


class CreateBotRequest(BaseModel):
    name: str
    display_name: Optional[str] = ""
    description: Optional[str] = ""
    model: Optional[str] = ""
    provider: Optional[str] = "claude-cli"
    tools: Optional[list] = []
    telegram_token: Optional[str] = ""
    channel: Optional[str] = "telegram"
    soul: Optional[str] = ""


@app.post("/api/bots")
async def create_bot(req: CreateBotRequest):
    if not re.match(r"^[a-zA-Z0-9_-]+$", req.name):
        raise HTTPException(400, detail="Invalid bot name")
    if (BOTS_DIR / req.name).is_dir():
        raise HTTPException(409, detail="Bot already exists")

    script = BASE_DIR / "criar-bot.sh"
    if not script.exists():
        raise HTTPException(500, detail="criar-bot.sh not found")

    channel = req.channel or "telegram"
    if channel not in ("telegram", "whatsapp"):
        raise HTTPException(400, detail="Canal inválido. Use 'telegram' ou 'whatsapp'.")

    result = subprocess.run(
        ["bash", str(script), req.name, "--channel", channel],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise HTTPException(500, detail=result.stderr or result.stdout)

    # Patch .env with model/provider/tools/token
    env_path = BOTS_DIR / req.name / ".env"
    patches = {}
    if req.model:
        patches["MODEL"] = req.model
    if req.provider:
        patches["PROVIDER"] = req.provider
    if req.tools:
        patches["TOOLS"] = ",".join(req.tools)
    if req.telegram_token and channel == "telegram":
        patches["TELEGRAM_TOKEN"] = req.telegram_token
    if req.display_name:
        patches["BOT_NAME"] = req.display_name
    if req.description:
        patches["DESCRIPTION"] = req.description
    if patches:
        write_env(env_path, patches)

    if req.soul and req.soul.strip():
        soul_path = BOTS_DIR / req.name / "soul.md"
        soul_path.write_text(req.soul.strip() + "\n", encoding="utf-8")

    return get_bot_summary(req.name)


@app.delete("/api/bots/{name}")
async def delete_bot(name: str):
    validate_bot_name(name)

    if IN_DOCKER:
        subprocess.run(["pkill", "-f", "--", f"--bot-dir.*bots/{name}"],
                       capture_output=True, timeout=10)
    else:
        service = f"claude-bot-{name}"
        for cmd in ["stop", "disable"]:
            subprocess.run(["sudo", "systemctl", cmd, service],
                           capture_output=True, text=True, timeout=30)
        service_file = Path(f"/etc/systemd/system/{service}.service")
        if service_file.exists():
            subprocess.run(["sudo", "rm", str(service_file)],
                           capture_output=True, text=True, timeout=10)
        subprocess.run(["sudo", "systemctl", "daemon-reload"],
                       capture_output=True, text=True, timeout=30)

    # Remove bot directory
    bot_dir = BOTS_DIR / name
    shutil.rmtree(bot_dir, ignore_errors=True)

    return {"ok": True}



# ── Routes: Env ───────────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/env")
async def get_env(name: str):
    validate_bot_name(name)
    env = get_bot_env_effective(name)
    masked = {k: mask_sensitive(k, v) for k, v in env.items()}
    return {"fields": masked}


class EnvUpdate(BaseModel):
    fields: dict


@app.put("/api/bots/{name}/env")
async def update_env(name: str, body: EnvUpdate):
    validate_bot_name(name)
    env_path = BOTS_DIR / name / ".env"
    old_env = load_env(env_path)

    final = {}
    for k, v in body.fields.items():
        final[k] = unmask_sensitive(k, v, old_env.get(k, ""))

    write_env(env_path, final)
    return {"ok": True}


# ── Routes: Files ─────────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/file/{fname}")
async def get_file(name: str, fname: str):
    validate_bot_name(name)
    if fname not in FILE_WHITELIST:
        raise HTTPException(400, detail="File not allowed")
    fpath = BOTS_DIR / name / fname
    content = fpath.read_text(errors="replace") if fpath.exists() else ""
    return {"content": content}


class FileUpdate(BaseModel):
    content: str


@app.put("/api/bots/{name}/file/{fname}")
async def update_file(name: str, fname: str, body: FileUpdate):
    validate_bot_name(name)
    if fname not in FILE_WHITELIST:
        raise HTTPException(400, detail="File not allowed")
    fpath = BOTS_DIR / name / fname
    fpath.write_text(body.content)
    return {"ok": True}


# ── Routes: Actions ───────────────────────────────────────────────────────────

class ActionRequest(BaseModel):
    action: str


@app.post("/api/bots/{name}/action")
async def bot_action(name: str, req: ActionRequest):
    validate_bot_name(name)
    if req.action not in ("start", "stop", "restart"):
        raise HTTPException(400, detail="Invalid action")

    if IN_DOCKER:
        bot_dir = str(BOTS_DIR / name)
        bot_script = _get_bot_script(name)
        if req.action in ("stop", "restart"):
            subprocess.run(["pkill", "-f", "--", f"--bot-dir.*bots/{name}"],
                           capture_output=True, timeout=10)
            # Aguarda o processo morrer (até 10s) e limpa o lock file
            for _ in range(10):
                r = subprocess.run(["pgrep", "-f", "--", f"--bot-dir.*bots/{name}"],
                                   capture_output=True)
                if r.returncode != 0:
                    break
                time.sleep(1)
            for lf in (BASE_DIR / ".locks").glob(f"*{name}*"):
                lf.unlink(missing_ok=True)
        if req.action in ("start", "restart"):
            log_path = BASE_DIR / "logs" / f"{name}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = open(log_path, "a")
            subprocess.Popen(
                ["python3", bot_script, "--bot-dir", bot_dir],
                stdout=log_fd, stderr=log_fd, start_new_session=True,
            )
    else:
        service = f"claude-bot-{name}"
        result = subprocess.run(
            ["sudo", "systemctl", req.action, service],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            raise HTTPException(500, detail=result.stderr or f"systemctl {req.action} failed")
    return {"ok": True, "action": req.action}


# ── Routes: Logs (SSE) ────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/logs")
async def stream_logs(name: str):
    validate_bot_name(name)

    if IN_DOCKER:
        log_file = BASE_DIR / "logs" / f"{name}.log"

        async def gen_docker():
            if log_file.exists():
                proc_hist = await asyncio.create_subprocess_exec(
                    "tail", "-n", "100", str(log_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await proc_hist.communicate()
                for line in out.decode(errors="replace").splitlines():
                    yield f"data: {line}\n\n"

            proc = await asyncio.create_subprocess_exec(
                "tail", "-f", str(log_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                while True:
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
                    except asyncio.TimeoutError:
                        yield "data: \n\n"
                        continue
                    if not line:
                        break
                    yield f"data: {line.decode(errors='replace').rstrip()}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass

        return StreamingResponse(
            gen_docker(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    service = f"claude-bot-{name}"

    async def gen():
        proc_hist = await asyncio.create_subprocess_exec(
            "journalctl", "-u", service,
            "-n", "100", "--no-pager", "--output=short-iso",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc_hist.communicate()
        for line in out.decode(errors="replace").splitlines():
            yield f"data: {line}\n\n"

        proc = await asyncio.create_subprocess_exec(
            "journalctl", "-u", service,
            "-f", "--no-pager", "--output=short-iso",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            while True:
                try:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "data: \n\n"
                    continue
                if not line:
                    break
                yield f"data: {line.decode(errors='replace').rstrip()}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                proc.terminate()
            except Exception:
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Routes: Users ─────────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/users")
async def get_users(name: str):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    rows = conn.execute(
        "SELECT user_id, name, username, approved_at FROM approved_users ORDER BY approved_at DESC"
    ).fetchall()
    conn.close()
    return [{"user_id": r[0], "name": r[1], "username": r[2], "approved_at": r[3]} for r in rows]


@app.delete("/api/bots/{name}/users/{uid}")
async def revoke_user(name: str, uid: int):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        raise HTTPException(404, detail="Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("DELETE FROM approved_users WHERE user_id = ?", (uid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Routes: Analytics ─────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/analytics")
async def analytics(name: str, days: int = 1):
    validate_bot_name(name)
    return get_analytics(name, days)


# ── Routes: Traces ────────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/traces")
async def get_traces(name: str, limit: int = 20, user_id: int = 0):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        return {"traces": []}
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        if user_id:
            rows = conn.execute(
                "SELECT id, bot_name, user_id, started_at, ended_at, total_spans, "
                "total_tool_calls, total_llm_calls, total_input_tokens, total_output_tokens, "
                "total_latency_ms, error FROM traces WHERE bot_name = ? AND user_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (name, user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, bot_name, user_id, started_at, ended_at, total_spans, "
                "total_tool_calls, total_llm_calls, total_input_tokens, total_output_tokens, "
                "total_latency_ms, error FROM traces WHERE bot_name = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (name, limit),
            ).fetchall()
        traces = [dict(r) for r in rows]
    finally:
        conn.close()
    return {"traces": traces}


@app.get("/api/bots/{name}/traces/{trace_id}")
async def get_trace_detail(name: str, trace_id: str):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM traces WHERE id = ? AND bot_name = ?", (trace_id, name)).fetchone()
        if not row:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Trace not found")
        result = dict(row)
        # Parse JSON fields for API response
        import json as _json
        for field in ("spans", "metadata"):
            if result.get(field):
                try:
                    result[field] = _json.loads(result[field])
                except Exception:
                    pass
    finally:
        conn.close()
    return result


# ── Routes: Schedules ─────────────────────────────────────────────────────────

@app.get("/api/bots/{name}/schedules")
async def get_schedules(name: str):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    schedules = []
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            "SELECT id, user_id, hour, minute, weekdays, message, created_at FROM schedules ORDER BY hour, minute"
        ).fetchall()
        conn.close()
        schedules = [
            {"id": r[0], "user_id": r[1], "hour": r[2], "minute": r[3],
             "weekdays": r[4], "message": r[5], "created_at": r[6]}
            for r in rows
        ]
    return {"schedules": schedules}


class ScheduleCreate(BaseModel):
    user_id: int
    hour: int
    minute: int
    weekdays: str
    message: str


class ScheduleUpdate(BaseModel):
    hour: int
    minute: int
    weekdays: str
    message: str


@app.post("/api/bots/{name}/schedules")
async def create_schedule(name: str, body: ScheduleCreate):
    validate_bot_name(name)
    if not (0 <= body.hour <= 23):
        raise HTTPException(400, detail="hour must be 0-23")
    if not (0 <= body.minute <= 59):
        raise HTTPException(400, detail="minute must be 0-59")
    if not body.message.strip():
        raise HTTPException(400, detail="message is required")

    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        raise HTTPException(404, detail="Database not found — start the bot first")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        "INSERT INTO schedules (user_id, hour, minute, weekdays, message, created_at) VALUES (?,?,?,?,?,?)",
        (body.user_id, body.hour, body.minute, body.weekdays, body.message,
         datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.put("/api/bots/{name}/schedules/{sid}")
async def update_schedule(name: str, sid: str, body: ScheduleUpdate):
    validate_bot_name(name)
    if not (0 <= body.hour <= 23):
        raise HTTPException(400, detail="hour must be 0-23")
    if not (0 <= body.minute <= 59):
        raise HTTPException(400, detail="minute must be 0-59")
    if not body.message.strip():
        raise HTTPException(400, detail="message is required")
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        raise HTTPException(404, detail="Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.execute(
        "UPDATE schedules SET hour=?, minute=?, weekdays=?, message=? WHERE id=?",
        (body.hour, body.minute, body.weekdays, body.message, sid)
    )
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, detail="Schedule not found")
    return {"ok": True}


@app.delete("/api/bots/{name}/schedules/{sid}")
async def delete_schedule(name: str, sid: str):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        raise HTTPException(404, detail="Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("DELETE FROM schedules WHERE id = ?", (sid,))
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Routes: Messaging ─────────────────────────────────────────────────────────

class SendMessageRequest(BaseModel):
    user_id: int
    message: str

@app.post("/api/bots/{name}/send-message")
async def send_message(name: str, body: SendMessageRequest):
    validate_bot_name(name)
    env = get_bot_env(name)
    token = env.get("TELEGRAM_TOKEN")
    if not token:
        raise HTTPException(400, detail="TELEGRAM_TOKEN not configured")
    try:
        from telegram import Bot
        bot = Bot(token=token)
        await bot.send_message(chat_id=body.user_id, text=body.message)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(500, detail=str(e))


class BroadcastRequest(BaseModel):
    message: str

@app.post("/api/bots/{name}/broadcast")
async def broadcast_message(name: str, body: BroadcastRequest):
    validate_bot_name(name)
    env = get_bot_env(name)
    token = env.get("TELEGRAM_TOKEN")
    if not token:
        raise HTTPException(400, detail="TELEGRAM_TOKEN not configured")
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        raise HTTPException(404, detail="Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    rows = conn.execute("SELECT user_id FROM approved_users").fetchall()
    conn.close()
    user_ids = [r[0] for r in rows]
    if not user_ids:
        raise HTTPException(400, detail="No approved users to broadcast to")
    from telegram import Bot
    bot = Bot(token=token)
    sent = 0
    errors = []
    for uid in user_ids:
        try:
            await bot.send_message(chat_id=uid, text=body.message)
            sent += 1
        except Exception as e:
            errors.append({"user_id": uid, "error": str(e)})
    return {"ok": True, "sent": sent, "total": len(user_ids), "errors": errors}


# ── Routes: Conversations ────────────────────────────────────────────────────

@app.get("/api/bots/{name}/export")
async def export_conversations(name: str, user_id: Optional[int] = None):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        raise HTTPException(404, detail="Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conversations = []
    if user_id:
        rows = conn.execute(
            "SELECT user_id, role, content, ts FROM conversations WHERE user_id = ? ORDER BY ts",
            (user_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT user_id, role, content, ts FROM conversations ORDER BY user_id, ts"
        ).fetchall()
    for r in rows:
        conversations.append({"user_id": r[0], "role": r[1], "content": r[2], "ts": r[3]})
    # Also get archived sessions
    archives = []
    try:
        if user_id:
            arows = conn.execute(
                "SELECT user_id, messages, archived_at FROM sessions_archive WHERE user_id = ? ORDER BY archived_at",
                (user_id,)
            ).fetchall()
        else:
            arows = conn.execute(
                "SELECT user_id, messages, archived_at FROM sessions_archive ORDER BY user_id, archived_at"
            ).fetchall()
        for r in arows:
            archives.append({"user_id": r[0], "messages": r[1], "archived_at": r[2]})
    except Exception:
        pass
    conn.close()
    return {"conversations": conversations, "archives": archives}


@app.delete("/api/bots/{name}/conversations/{uid}")
async def clear_user_conversations(name: str, uid: int):
    validate_bot_name(name)
    db_path = BOTS_DIR / name / "bot_data.db"
    if not db_path.exists():
        raise HTTPException(404, detail="Database not found")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("DELETE FROM conversations WHERE user_id = ?", (uid,))
    try:
        conn.execute("DELETE FROM sessions_archive WHERE user_id = ?", (uid,))
    except Exception:
        pass
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Routes: Global files ──────────────────────────────────────────────────────

@app.get("/api/global/system-keys")
async def get_system_keys():
    return {fname: sorted(keys) for fname, keys in SYSTEM_GLOBAL_KEYS.items()}


@app.get("/api/global/context-default")
async def get_context_default():
    path = BASE_DIR / "context.global.default"
    content = path.read_text(errors="replace") if path.exists() else ""
    return {"content": content}


@app.get("/api/global/{fname}")
async def get_global(fname: str):
    if fname not in GLOBAL_WHITELIST:
        raise HTTPException(400, detail="File not allowed")
    fpath = BASE_DIR / fname
    content = fpath.read_text(errors="replace") if fpath.exists() else ""
    if fname == "secrets.global":
        # Injeta placeholders para chaves conhecidas ainda não presentes no arquivo.
        # Só afeta o response — o arquivo não é alterado até o usuário salvar explicitamente.
        existing_keys = {
            l.split("=")[0].strip()
            for l in content.splitlines()
            if "=" in l and not l.strip().startswith("#")
        }
        missing = [k for k in KNOWN_GLOBAL_SECRETS if k not in existing_keys]
        if missing:
            content = content.rstrip("\n") + "\n" + "\n".join(f"{k}=" for k in missing) + "\n"
    return {"content": content}


@app.put("/api/global/{fname}")
async def update_global(fname: str, body: FileUpdate):
    if fname not in GLOBAL_WHITELIST:
        raise HTTPException(400, detail="File not allowed")
    sys_keys = SYSTEM_GLOBAL_KEYS.get(fname, set())
    if sys_keys:
        new_keys = {
            l.partition("=")[0].strip()
            for l in body.content.splitlines()
            if l.strip() and not l.strip().startswith("#") and "=" in l
        }
        missing = sys_keys - new_keys
        if missing:
            raise HTTPException(
                422,
                detail=f"Variáveis de sistema não podem ser removidas: {', '.join(sorted(missing))}",
            )
    fpath = BASE_DIR / fname
    fpath.write_text(body.content)
    return {"ok": True}


# ── Routes: Crontab ───────────────────────────────────────────────────────────

@app.get("/api/crontab")
async def get_crontab():
    result = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True, timeout=10
    )
    content = result.stdout if result.returncode == 0 else ""
    return {"content": content}


class CrontabUpdate(BaseModel):
    content: str


@app.put("/api/crontab")
async def update_crontab(body: CrontabUpdate):
    content = body.content if body.content.endswith("\n") else body.content + "\n"
    # Protege linhas de sistema (marcadas com # [system] ou # smb-)
    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    if current.returncode == 0:
        sys_lines = [
            l for l in current.stdout.splitlines()
            if l.strip() and not l.strip().startswith("#")
            and ("# [system]" in l or "# smb-" in l)
        ]
        new_set = set(body.content.splitlines())
        missing = [l for l in sys_lines if l not in new_set]
        if missing:
            raise HTTPException(422, detail="Entradas de sistema não podem ser removidas do crontab")
    result = subprocess.run(
        ["crontab", "-"],
        input=content,
        capture_output=True, text=True, timeout=10
    )
    if result.returncode != 0:
        raise HTTPException(500, detail=result.stderr or "Failed to update crontab")
    return {"ok": True}


@app.post("/api/bots/restart-all")
async def restart_all_bots():
    bots = [d.name for d in BOTS_DIR.iterdir() if d.is_dir() and (d / ".env").exists()]
    results = []
    for name in sorted(bots):
        if IN_DOCKER:
            subprocess.run(["pkill", "-f", "--", f"--bot-dir.*bots/{name}"],
                           capture_output=True, timeout=10)
            for _ in range(10):
                r = subprocess.run(["pgrep", "-f", "--", f"--bot-dir.*bots/{name}"],
                                   capture_output=True)
                if r.returncode != 0:
                    break
                time.sleep(1)
            for lf in (BASE_DIR / ".locks").glob(f"*{name}*"):
                lf.unlink(missing_ok=True)
            bot_dir = str(BOTS_DIR / name)
            bot_script = _get_bot_script(name)
            log_path = BASE_DIR / "logs" / f"{name}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = open(log_path, "a")
            subprocess.Popen(
                ["python3", bot_script, "--bot-dir", bot_dir],
                stdout=log_fd, stderr=log_fd, start_new_session=True,
            )
            results.append({"name": name, "ok": True})
        else:
            r = subprocess.run(
                ["sudo", "systemctl", "restart", f"claude-bot-{name}"],
                capture_output=True, text=True, timeout=15
            )
            results.append({"name": name, "ok": r.returncode == 0})
    return {"results": results}


# ── Routes: Setup Wizard ──────────────────────────────────────────────────────

CLAUDE_CREDS_PATH = Path.home() / ".claude" / ".credentials.json"
CODEX_AUTH_PATH = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "auth.json"

# OAuth PKCE state (ephemeral, per-session)
import hashlib, base64, secrets as _secrets, urllib.parse
_oauth_state: dict = {}  # state -> {verifier, provider, created_at}

OAUTH_PROVIDERS = {
    "codex": {
        "authorize_url": "https://auth.openai.com/oauth/authorize",
        "token_url": "https://auth.openai.com/oauth/token",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "redirect_uri": "http://localhost:1455/auth/callback",
        "scopes": "openid profile email offline_access",
        "extra_params": {
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "pi",
        },
        "creds_path": CODEX_AUTH_PATH,
    },
    "claude": {
        "authorize_url": "https://auth.anthropic.com/oauth/authorize",
        "token_url": "https://auth.anthropic.com/oauth/token",
        "client_id": "d912a2d4-0544-4661-8498-7638e8196c55",
        "redirect_uri": "http://localhost:18217/oauth/callback",
        "scopes": "user:inference",
        "extra_params": {},
        "creds_path": CLAUDE_CREDS_PATH,
    },
}


def _check_oauth(path: Path, accessor: list[str]) -> dict:
    """Check if an OAuth credential file has a valid token."""
    if not path.exists():
        return {"status": "not_configured", "path": str(path)}
    try:
        data = json.loads(path.read_text())
        obj = data
        for key in accessor:
            obj = obj.get(key, {})
        if obj and isinstance(obj, str):
            return {"status": "active", "path": str(path)}
        return {"status": "empty_token", "path": str(path)}
    except Exception:
        return {"status": "error", "path": str(path)}


def _pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = base64.urlsafe_b64encode(_secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


class OAuthStartRequest(BaseModel):
    provider: str  # "codex" or "claude"


@app.post("/api/setup/oauth/start")
async def oauth_start(body: OAuthStartRequest):
    """Generate OAuth authorization URL with PKCE."""
    cfg = OAUTH_PROVIDERS.get(body.provider)
    if not cfg:
        raise HTTPException(400, detail=f"Unknown provider: {body.provider}")

    verifier, challenge = _pkce_pair()
    state = _secrets.token_hex(16)

    _oauth_state[state] = {
        "verifier": verifier,
        "provider": body.provider,
        "created_at": datetime.now().timestamp(),
    }

    # Cleanup old states (> 10 min)
    cutoff = datetime.now().timestamp() - 600
    _oauth_state.update({
        k: v for k, v in _oauth_state.items() if v["created_at"] > cutoff
    })

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "scope": cfg["scopes"],
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        **cfg["extra_params"],
    }

    url = cfg["authorize_url"] + "?" + urllib.parse.urlencode(params)
    return {"url": url, "state": state}


class OAuthCompleteRequest(BaseModel):
    provider: str
    redirect_url: str  # The full redirect URL pasted by user


@app.post("/api/setup/oauth/complete")
async def oauth_complete(body: OAuthCompleteRequest):
    """Exchange OAuth code from redirect URL for tokens and save credentials."""
    import urllib.request

    cfg = OAUTH_PROVIDERS.get(body.provider)
    if not cfg:
        raise HTTPException(400, detail=f"Unknown provider: {body.provider}")

    # Parse the redirect URL to extract code and state
    parsed = urllib.parse.urlparse(body.redirect_url)
    query = urllib.parse.parse_qs(parsed.query)

    code = query.get("code", [None])[0]
    state = query.get("state", [None])[0]

    if not code:
        return {"ok": False, "error": "Código de autorização não encontrado na URL."}

    # Look up PKCE verifier from state
    state_data = _oauth_state.pop(state, None) if state else None
    if not state_data:
        # Try any matching provider state as fallback
        for k, v in list(_oauth_state.items()):
            if v["provider"] == body.provider:
                state_data = _oauth_state.pop(k)
                break

    if not state_data:
        return {"ok": False, "error": "Sessão OAuth expirada. Tente novamente."}

    verifier = state_data["verifier"]

    # Exchange code for tokens
    token_data = urllib.parse.urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg["redirect_uri"],
        "client_id": cfg["client_id"],
        "code_verifier": verifier,
    }).encode()

    try:
        req = urllib.request.Request(
            cfg["token_url"],
            data=token_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read())
    except Exception as e:
        return {"ok": False, "error": f"Erro ao trocar código por token: {e}"}

    # Save credentials
    try:
        creds_path = cfg["creds_path"]
        creds_path.parent.mkdir(parents=True, exist_ok=True)

        if body.provider == "codex":
            creds = {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": tokens.get("access_token", ""),
                    "id_token": tokens.get("id_token", ""),
                    "refresh_token": tokens.get("refresh_token", ""),
                },
                "last_refresh": datetime.now().isoformat(),
            }
            creds_path.write_text(json.dumps(creds, indent=2))

        elif body.provider == "claude":
            # Read existing or create new
            existing = {}
            if creds_path.exists():
                try:
                    existing = json.loads(creds_path.read_text())
                except Exception:
                    pass
            existing["claudeAiOauth"] = {
                "accessToken": tokens.get("access_token", ""),
                "refreshToken": tokens.get("refresh_token", ""),
                "expiresAt": datetime.now().timestamp() + tokens.get("expires_in", 3600),
            }
            creds_path.write_text(json.dumps(existing, indent=2))

        os.chmod(creds_path, 0o600)
        return {"ok": True}

    except Exception as e:
        return {"ok": False, "error": f"Erro ao salvar credenciais: {e}"}


@app.get("/api/setup/status")
async def setup_status():
    """Return setup status: dependencies, auth, config, whether setup is needed."""
    # Dependencies — check in system Python (bot.py runs there, not in admin venv)
    pip_pkgs = {"anthropic": "anthropic", "openai": "openai", "telegram": "python-telegram-bot",
                "pdfplumber": "pdfplumber", "whisper": "openai-whisper"}
    deps_pip = {}
    try:
        check_code = ";".join(
            f"print('{pkg}',__import__('importlib').util.find_spec('{mod}') is not None)"
            for mod, pkg in pip_pkgs.items()
        )
        result = subprocess.run(
            ["/usr/bin/python3", "-c", check_code],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) == 2:
                deps_pip[parts[0]] = parts[1] == "True"
    except Exception:
        for pkg in pip_pkgs.values():
            deps_pip[pkg] = False

    deps_cli = {}
    for cmd in ["python3", "node", "npm", "ffmpeg", "claude", "codex"]:
        deps_cli[cmd] = shutil.which(cmd) is not None

    # Auth status
    auth = {
        "claude_oauth": _check_oauth(CLAUDE_CREDS_PATH, ["claudeAiOauth", "accessToken"]),
        "codex_oauth": _check_oauth(CODEX_AUTH_PATH, ["tokens", "access_token"]),
        "anthropic_key": bool(
            load_env(BASE_DIR / "secrets.global").get("ANTHROPIC_API_KEY")
            or load_env(BASE_DIR / "config.global").get("ANTHROPIC_API_KEY")
        ),
        "openai_key": bool(
            load_env(BASE_DIR / "secrets.global").get("OPENAI_API_KEY")
        ),
        "openrouter_key": bool(
            load_env(BASE_DIR / "secrets.global").get("OPENROUTER_API_KEY")
            or load_env(BASE_DIR / "config.global").get("OPENROUTER_API_KEY")
        ),
    }

    # Config
    cfg = load_env(BASE_DIR / "config.global")
    config = {
        "provider": cfg.get("PROVIDER", ""),
        "model": cfg.get("MODEL", ""),
        "admin_id": cfg.get("ADMIN_ID", ""),
        "access_mode": cfg.get("ACCESS_MODE", ""),
    }

    # Has bots?
    has_bots = BOTS_DIR.is_dir() and any(
        d.is_dir() and (d / ".env").exists() for d in BOTS_DIR.iterdir()
    )

    needs_setup = (
        not (BASE_DIR / "config.global").exists()
        or not config["provider"]
        or not has_bots
    )

    return {
        "needs_setup": needs_setup,
        "deps": {"pip": deps_pip, "cli": deps_cli},
        "auth": auth,
        "config": config,
        "has_bots": has_bots,
    }


class InstallDepsRequest(BaseModel):
    packages: list[str]


@app.post("/api/setup/install-deps")
async def setup_install_deps(body: InstallDepsRequest):
    """Install missing pip packages."""
    allowed = {"anthropic", "openai", "python-telegram-bot", "pdfplumber", "openai-whisper", "jinja2", "aiofiles"}
    pkgs = [p for p in body.packages if p in allowed]
    if not pkgs:
        return {"ok": True, "output": "Nothing to install."}
    try:
        result = subprocess.run(
            ["pip", "install", "--break-system-packages"] + pkgs,
            capture_output=True, text=True, timeout=180,
        )
        return {"ok": result.returncode == 0, "output": result.stdout + result.stderr}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, detail="Installation timed out")


class TestProviderRequest(BaseModel):
    provider: str
    api_key: Optional[str] = None
    model: Optional[str] = None


@app.post("/api/setup/test-provider")
async def setup_test_provider(body: TestProviderRequest):
    """Test API connection to a provider with a tiny completion request."""
    import asyncio

    provider = body.provider
    api_key = body.api_key
    model = body.model

    try:
        if provider in ("anthropic", "claude-cli"):
            import anthropic as anthropic_sdk
            if not api_key:
                # Try OAuth
                if CLAUDE_CREDS_PATH.exists():
                    creds = json.loads(CLAUDE_CREDS_PATH.read_text())
                    api_key = creds.get("claudeAiOauth", {}).get("accessToken", "")
                if not api_key:
                    api_key = (load_env(BASE_DIR / "secrets.global").get("ANTHROPIC_API_KEY")
                               or load_env(BASE_DIR / "config.global").get("ANTHROPIC_API_KEY"))
            if not api_key:
                return {"ok": False, "error": "No API key or OAuth token found."}
            kwargs = {}
            if api_key.startswith("sk-ant-"):
                kwargs["api_key"] = api_key
            else:
                kwargs["auth_token"] = api_key
            client = anthropic_sdk.Anthropic(**kwargs)
            test_model = model or "claude-haiku-4-5-20251001"
            resp = client.messages.create(
                model=test_model, max_tokens=5,
                messages=[{"role": "user", "content": "Hi"}],
            )
            return {"ok": True, "model": test_model}

        elif provider in ("codex", "openrouter"):
            from openai import OpenAI
            is_codex_oauth = False
            if provider == "codex":
                if not api_key:
                    if CODEX_AUTH_PATH.exists():
                        auth = json.loads(CODEX_AUTH_PATH.read_text())
                        api_key = auth.get("tokens", {}).get("access_token", "")
                        is_codex_oauth = bool(api_key)
                    if not api_key:
                        api_key = load_env(BASE_DIR / "secrets.global").get("OPENAI_API_KEY")
                if not api_key:
                    return {"ok": False, "error": "No API key or Codex OAuth token found."}
                if is_codex_oauth:
                    account_id = auth.get("account_id", "")
                    headers = {}
                    if account_id:
                        headers["ChatGPT-Account-Id"] = account_id
                    client = OpenAI(
                        api_key=api_key,
                        base_url="https://chatgpt.com/backend-api/wham",
                        default_headers=headers,
                    )
                else:
                    client = OpenAI(api_key=api_key)
                test_model = model or "gpt-5.1-codex-mini"
            else:  # openrouter
                if not api_key:
                    api_key = (load_env(BASE_DIR / "secrets.global").get("OPENROUTER_API_KEY")
                               or load_env(BASE_DIR / "config.global").get("OPENROUTER_API_KEY"))
                if not api_key:
                    return {"ok": False, "error": "No OpenRouter API key found."}
                client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key)
                test_model = model or "google/gemini-2.5-flash"

            if is_codex_oauth:
                resp = client.responses.create(
                    model=test_model, input="Hi",
                )
            else:
                resp = client.chat.completions.create(
                    model=test_model, max_tokens=5,
                    messages=[{"role": "user", "content": "Hi"}],
                )
            return {"ok": True, "model": test_model}

        else:
            return {"ok": False, "error": f"Unknown provider: {provider}"}

    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


class SetupSaveRequest(BaseModel):
    provider: str
    model: str
    admin_id: str = ""
    access_mode: str = "approval"
    anthropic_key: str = ""
    openai_key: str = ""
    openrouter_key: str = ""
    create_bot: Optional[dict] = None


@app.post("/api/setup/save")
async def setup_save(body: SetupSaveRequest):
    """Save global config and optionally create first bot."""
    # Preserve existing bugfixer settings
    existing_cfg = load_env(BASE_DIR / "config.global")

    cfg_content = f"""# Configurações globais compartilhadas por todos os bots.
# Gerado pelo setup wizard em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

PROVIDER={body.provider}
ADMIN_ID={body.admin_id}
MODEL={body.model}
ACCESS_MODE={body.access_mode}

BUGFIXER_ENABLED={existing_cfg.get('BUGFIXER_ENABLED', 'false')}
BUGFIXER_TIMES_PER_DAY={existing_cfg.get('BUGFIXER_TIMES_PER_DAY', '1')}
BUGFIXER_TELEGRAM_TOKEN={existing_cfg.get('BUGFIXER_TELEGRAM_TOKEN', '')}
"""
    (BASE_DIR / "config.global").write_text(cfg_content)

    # Write secrets.global
    existing_secrets = load_env(BASE_DIR / "secrets.global")
    secrets = {}
    if body.anthropic_key and "****" not in body.anthropic_key:
        secrets["ANTHROPIC_API_KEY"] = body.anthropic_key
    elif existing_secrets.get("ANTHROPIC_API_KEY"):
        secrets["ANTHROPIC_API_KEY"] = existing_secrets["ANTHROPIC_API_KEY"]

    if body.openai_key and "****" not in body.openai_key:
        secrets["OPENAI_API_KEY"] = body.openai_key
    elif existing_secrets.get("OPENAI_API_KEY"):
        secrets["OPENAI_API_KEY"] = existing_secrets["OPENAI_API_KEY"]

    if body.openrouter_key and "****" not in body.openrouter_key:
        secrets["OPENROUTER_API_KEY"] = body.openrouter_key
    elif existing_secrets.get("OPENROUTER_API_KEY"):
        secrets["OPENROUTER_API_KEY"] = existing_secrets["OPENROUTER_API_KEY"]

    # Preserve ALL existing secrets not already handled above
    for k, v in existing_secrets.items():
        if k not in secrets and v:
            secrets[k] = v

    secrets_path = BASE_DIR / "secrets.global"
    lines = ["# Credenciais sensíveis — NÃO versionar",
             f"# Gerado em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for k, v in secrets.items():
        if v:
            lines.append(f"{k}={v}")
    secrets_path.write_text("\n".join(lines) + "\n")
    os.chmod(secrets_path, 0o600)

    # Create first bot if requested
    bot_result = None
    if body.create_bot:
        bc = body.create_bot
        bot_name = bc.get("name", "")
        if bot_name and re.match(r"^[a-zA-Z0-9_-]+$", bot_name):
            result = subprocess.run(
                [str(BASE_DIR / "criar-bot.sh"), bot_name],
                capture_output=True, text=True, timeout=30,
                cwd=str(BASE_DIR),
            )
            if result.returncode == 0:
                # Patch .env with provided values
                env_path = BOTS_DIR / bot_name / ".env"
                patches = {"BOT_NAME": bc.get("display_name", bot_name)}
                if bc.get("telegram_token"):
                    patches["TELEGRAM_TOKEN"] = bc["telegram_token"]
                if bc.get("tools"):
                    patches["TOOLS"] = ",".join(bc["tools"]) if isinstance(bc["tools"], list) else bc["tools"]
                if bc.get("provider"):
                    patches["PROVIDER"] = bc["provider"]
                if bc.get("model"):
                    patches["MODEL"] = bc["model"]
                write_env(env_path, patches)

                # Write soul.md if provided
                if bc.get("soul"):
                    (BOTS_DIR / bot_name / "soul.md").write_text(bc["soul"])

                # Start the bot
                if bc.get("telegram_token"):
                    if IN_DOCKER:
                        bot_dir_path = str(BOTS_DIR / bot_name)
                        log_path = BASE_DIR / "logs" / f"{bot_name}.log"
                        log_path.parent.mkdir(parents=True, exist_ok=True)
                        log_fd = open(log_path, "a")
                        subprocess.Popen(
                            ["python3", str(BASE_DIR / "bot.py"), "--bot-dir", bot_dir_path],
                            stdout=log_fd, stderr=log_fd, start_new_session=True,
                        )
                    else:
                        subprocess.run(
                            ["sudo", "systemctl", "enable", "--now", f"claude-bot-{bot_name}"],
                            capture_output=True, timeout=15,
                        )

                bot_result = {"name": bot_name, "created": True}
            else:
                bot_result = {"name": bot_name, "created": False, "error": result.stderr[:300]}

    return {"ok": True, "bot": bot_result}


# ── Routes: Bug Fixer ─────────────────────────────────────────────────────────

BUGFIXER_STATE = BASE_DIR / ".bugfixer_state"
BUGFIXER_LOG = BASE_DIR / "logs" / "bugfixer.log"
BUGFIXER_SCRIPT = BASE_DIR / "bugfixer.py"
CRON_MARKER = "# smb-bugfixer"


def get_bugfixer_cron_schedules(times_per_day: int) -> list:
    """Calculate evenly distributed cron times across the day."""
    interval = 24 // max(1, times_per_day)
    return [f"0 {(i * interval) % 24} * * *" for i in range(times_per_day)]


def update_bugfixer_cron(enabled: bool, times_per_day: int):
    """Rewrite crontab: remove old bugfixer entries, add new ones if enabled."""
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    current = result.stdout if result.returncode == 0 else ""

    # Remove existing bugfixer lines
    lines = [l for l in current.splitlines() if CRON_MARKER not in l]

    if enabled:
        for schedule in get_bugfixer_cron_schedules(times_per_day):
            lines.append(
                f"{schedule} python3 {BUGFIXER_SCRIPT} >> {BUGFIXER_LOG} 2>&1 {CRON_MARKER}"
            )

    new_content = "\n".join(lines)
    if new_content and not new_content.endswith("\n"):
        new_content += "\n"

    subprocess.run(
        ["crontab", "-"], input=new_content, capture_output=True, text=True, timeout=10
    )


@app.get("/api/system/bugfixer")
async def get_bugfixer():
    cfg = load_env(BASE_DIR / "config.global")
    enabled = cfg.get("BUGFIXER_ENABLED", "false").lower() == "true"
    times_per_day = int(cfg.get("BUGFIXER_TIMES_PER_DAY", "3"))
    token_raw = cfg.get("BUGFIXER_TELEGRAM_TOKEN", "")
    token_masked = mask_sensitive("BUGFIXER_TELEGRAM_TOKEN", token_raw) if token_raw else ""

    last_run = None
    if BUGFIXER_STATE.exists():
        try:
            data = json.loads(BUGFIXER_STATE.read_text())
            last_run = data.get("last_run")
        except Exception:
            pass

    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    cron_content = result.stdout if result.returncode == 0 else ""
    cron_entries = [
        l for l in cron_content.splitlines() if CRON_MARKER in l and not l.startswith("#")
    ]

    return {
        "enabled": enabled,
        "times_per_day": times_per_day,
        "telegram_token": token_masked,
        "last_run": last_run,
        "cron_entries": cron_entries,
    }


class BugfixerUpdate(BaseModel):
    enabled: bool
    times_per_day: int
    telegram_token: Optional[str] = None


@app.put("/api/system/bugfixer")
async def update_bugfixer(body: BugfixerUpdate):
    if not (1 <= body.times_per_day <= 24):
        raise HTTPException(400, detail="times_per_day deve ser entre 1 e 24")

    patches = {
        "BUGFIXER_ENABLED": "true" if body.enabled else "false",
        "BUGFIXER_TIMES_PER_DAY": str(body.times_per_day),
    }
    if body.telegram_token is not None:
        old_cfg = load_env(BASE_DIR / "config.global")
        old_token = old_cfg.get("BUGFIXER_TELEGRAM_TOKEN", "")
        patches["BUGFIXER_TELEGRAM_TOKEN"] = unmask_sensitive(
            "BUGFIXER_TELEGRAM_TOKEN", body.telegram_token, old_token
        )

    write_env(BASE_DIR / "config.global", patches)

    update_bugfixer_cron(body.enabled, body.times_per_day)
    return {"ok": True}


@app.post("/api/system/bugfixer/run")
async def run_bugfixer():
    if not BUGFIXER_SCRIPT.exists():
        raise HTTPException(500, detail="bugfixer.py não encontrado")

    result = subprocess.run(
        ["python3", str(BUGFIXER_SCRIPT)],
        capture_output=True, text=True, timeout=300,
        env={**os.environ, "BUGFIXER_OVERRIDE": "true"},
    )
    output = (result.stdout + result.stderr).strip()
    return {"ok": result.returncode == 0, "output": output or "(sem output)"}


@app.get("/api/system/bugfixer/log")
async def get_bugfixer_log(lines: int = 50):
    if not BUGFIXER_LOG.exists():
        return {"content": "", "lines": 0}
    all_lines = BUGFIXER_LOG.read_text(errors="replace").splitlines()
    last_n = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {"content": "\n".join(last_n), "lines": len(last_n)}


# ── Routes: Memory Autosave ───────────────────────────────────────────────────

MEMORY_AUTOSAVE_STATE = BASE_DIR / ".memory_autosave_state"
MEMORY_AUTOSAVE_LOG = BASE_DIR / "logs" / "memory-autosave.log"
MEMORY_AUTOSAVE_SCRIPT = BASE_DIR / "memory-autosave.sh"


def _detect_autosave_provider() -> dict:
    """Detect which AI provider is available for memory-autosave, in fallback order."""
    secrets = load_env(BASE_DIR / "secrets.global")
    cfg = load_env(BASE_DIR / "config.global")

    providers = []

    # 1. Claude OAuth
    claude_oauth = _check_oauth(CLAUDE_CREDS_PATH, ["claudeAiOauth", "accessToken"])
    providers.append({
        "id": "claude_oauth", "label": "Claude OAuth", "model": "claude-haiku",
        "active": claude_oauth["status"] == "active",
    })

    # 2. Codex OAuth
    codex_oauth = _check_oauth(CODEX_AUTH_PATH, ["tokens", "access_token"])
    providers.append({
        "id": "codex_oauth", "label": "Codex OAuth", "model": "gpt-4o-mini",
        "active": codex_oauth["status"] == "active",
    })

    # 3. OpenRouter
    openrouter_key = secrets.get("OPENROUTER_API_KEY") or cfg.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        providers.append({"id": "openrouter", "label": "OpenRouter", "model": "gpt-4o-mini", "active": True})
    else:
        providers.append({"id": "openrouter", "label": "OpenRouter", "model": "gpt-4o-mini", "active": False})

    # 4. OpenAI API key
    openai_key = secrets.get("OPENAI_API_KEY", "")
    if openai_key:
        providers.append({"id": "openai_key", "label": "OpenAI API Key", "model": "gpt-4o-mini", "active": True})
    else:
        providers.append({"id": "openai_key", "label": "OpenAI API Key", "model": "gpt-4o-mini", "active": False})

    selected = next((p for p in providers if p["active"]), None)
    return {"providers": providers, "selected": selected["id"] if selected else None}


@app.get("/api/system/memory-autosave")
async def get_memory_autosave():
    provider_info = _detect_autosave_provider()

    last_run = None
    last_status = None
    last_error = None
    bots_processed = None
    if MEMORY_AUTOSAVE_STATE.exists():
        try:
            data = json.loads(MEMORY_AUTOSAVE_STATE.read_text())
            last_run = data.get("last_run")
            last_status = data.get("status")
            last_error = data.get("error") or None
            bots_processed = data.get("bots_processed")
        except Exception:
            pass

    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
    cron_content = result.stdout if result.returncode == 0 else ""
    cron_entry = next(
        (l for l in cron_content.splitlines() if "memory-autosave.sh" in l and not l.startswith("#")),
        None,
    )

    return {
        "providers": provider_info["providers"],
        "selected_provider": provider_info["selected"],
        "last_run": last_run,
        "last_status": last_status,
        "last_error": last_error,
        "bots_processed": bots_processed,
        "cron_entry": cron_entry,
    }


@app.post("/api/system/memory-autosave/run")
async def run_memory_autosave():
    if not MEMORY_AUTOSAVE_SCRIPT.exists():
        raise HTTPException(500, detail="memory-autosave.sh não encontrado")

    result = subprocess.run(
        ["bash", str(MEMORY_AUTOSAVE_SCRIPT)],
        capture_output=True, text=True, timeout=300,
    )
    output = (result.stdout + result.stderr).strip()
    return {"ok": result.returncode == 0, "output": output or "(sem output)"}


@app.get("/api/system/memory-autosave/log")
async def get_memory_autosave_log(lines: int = 50):
    if not MEMORY_AUTOSAVE_LOG.exists():
        return {"content": "", "lines": 0}
    all_lines = MEMORY_AUTOSAVE_LOG.read_text(errors="replace").splitlines()
    last_n = all_lines[-lines:] if len(all_lines) > lines else all_lines
    return {"content": "\n".join(last_n), "lines": len(last_n)}


# ── Routes: Sub-agentes ───────────────────────────────────────────────────────

def validate_subagent_name(name: str):
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise HTTPException(400, detail="Invalid subagent name")
    if not (SUBAGENTS_DIR / name).is_dir():
        raise HTTPException(404, detail="Subagent not found")


@app.get("/api/subagents")
async def list_subagents():
    if not SUBAGENTS_DIR.exists():
        return []
    result = []
    for d in sorted(SUBAGENTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        env = load_env(d / ".env")
        result.append({
            "name": d.name,
            "description": env.get("DESCRIPTION", ""),
            "provider": env.get("PROVIDER", ""),
            "model": env.get("MODEL", ""),
            "tools": env.get("TOOLS", "none"),
            "allowed_parents": env.get("ALLOWED_PARENTS", "*"),
            "mode": env.get("MODE", "simple"),
        })
    return result


class SubagentCreate(BaseModel):
    name: str
    description: str
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    mode: str = "simple"
    tools: str = "none"
    allowed_parents: str = "*"
    soul: str = ""


@app.post("/api/subagents")
async def create_subagent(req: SubagentCreate):
    if not re.match(r"^[a-zA-Z0-9_-]+$", req.name):
        raise HTTPException(400, detail="Invalid subagent name")
    d = SUBAGENTS_DIR / req.name
    if d.exists():
        raise HTTPException(409, detail="Subagent already exists")
    SUBAGENTS_DIR.mkdir(exist_ok=True)
    d.mkdir()
    env_content = (
        f"NAME={req.description}\n"
        f"DESCRIPTION={req.description}\n"
        f"PROVIDER={req.provider}\n"
        f"MODEL={req.model}\n"
        f"MODE={req.mode}\n"
        f"TOOLS={req.tools}\n"
        f"ALLOWED_PARENTS={req.allowed_parents}\n"
    )
    (d / ".env").write_text(env_content)
    soul = req.soul.strip() or f"Você é um assistente especializado: {req.description}."
    (d / "soul.md").write_text(soul)
    return {"name": req.name, "ok": True}


@app.delete("/api/subagents/{name}")
async def delete_subagent(name: str):
    validate_subagent_name(name)
    shutil.rmtree(SUBAGENTS_DIR / name, ignore_errors=True)
    return {"ok": True}


@app.get("/api/subagents/{name}/env")
async def get_subagent_env(name: str):
    validate_subagent_name(name)
    return {"fields": load_env(SUBAGENTS_DIR / name / ".env")}


@app.put("/api/subagents/{name}/env")
async def update_subagent_env(name: str, body: EnvUpdate):
    validate_subagent_name(name)
    write_env(SUBAGENTS_DIR / name / ".env", body.fields)
    return {"ok": True}


@app.get("/api/subagents/{name}/soul")
async def get_subagent_soul(name: str):
    validate_subagent_name(name)
    path = SUBAGENTS_DIR / name / "soul.md"
    return {"content": path.read_text(errors="replace") if path.exists() else ""}


@app.put("/api/subagents/{name}/soul")
async def update_subagent_soul(name: str, body: FileUpdate):
    validate_subagent_name(name)
    (SUBAGENTS_DIR / name / "soul.md").write_text(body.content)
    return {"ok": True}


# ── Routes: Architect ─────────────────────────────────────────────────────────

import httpx

# Cache de modelos do mercado
_models_cache: list[dict] = []
_models_cache_ts: float = 0
_MODELS_CACHE_TTL = 6 * 3600  # 6 horas

CURATED_PROVIDERS = {
    "anthropic", "openai", "google", "deepseek", "x-ai",
    "meta-llama", "mistralai", "moonshotai", "minimax",
    "qwen", "cohere", "amazon", "nex-agi", "stepfun",
}

# IDs que funcionam via API direta (sem OpenRouter)
# Modelos diretos (hardcoded) — mesmos usados em outras partes do código
_ANTHROPIC_MODELS = [
    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "provider": "anthropic",
     "context_length": 1000000, "price_input": 3.0, "price_output": 15.0, "tier": "premium",
     "description": "Equilíbrio ideal entre velocidade e inteligência"},
    {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "provider": "anthropic",
     "context_length": 1000000, "price_input": 5.0, "price_output": 25.0, "tier": "premium",
     "description": "O mais inteligente — raciocínio profundo e código complexo"},
    {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "provider": "anthropic",
     "context_length": 200000, "price_input": 1.0, "price_output": 5.0, "tier": "standard",
     "description": "Rápido e barato para tarefas simples"},
]

_CODEX_MODELS = [
    {"id": "gpt-5.4", "name": "GPT-5.4", "provider": "codex",
     "context_length": 128000, "price_input": 2.5, "price_output": 10.0, "tier": "premium",
     "description": "Raciocínio e código avançados"},
    {"id": "gpt-5.1-codex-mini", "name": "GPT-5.1 Codex Mini", "provider": "codex",
     "context_length": 128000, "price_input": 0.3, "price_output": 1.2, "tier": "economy",
     "description": "Leve e barato para tarefas simples"},
]

_ANTHROPIC_DIRECT_IDS = {m["id"] for m in _ANTHROPIC_MODELS}
_CODEX_DIRECT_IDS = {m["id"] for m in _CODEX_MODELS}


def _classify_tier(price_input: float) -> str:
    """Classifica tier do modelo pelo preço de input ($/MTok)."""
    if price_input >= 2.0:
        return "premium"
    if price_input >= 0.5:
        return "standard"
    return "economy"


def _normalize_openrouter_model(m: dict) -> dict | None:
    """Normaliza modelo do OpenRouter para formato interno. Retorna None se inválido."""
    mid = m.get("id", "")
    pricing = m.get("pricing") or {}
    ctx = m.get("context_length") or 0
    arch = m.get("architecture") or {}

    # Filtro técnico
    if ctx < 16000:
        return None
    if not pricing.get("prompt") or not pricing.get("completion"):
        return None
    try:
        price_in = float(pricing["prompt"]) * 1_000_000  # por MTok
        price_out = float(pricing["completion"]) * 1_000_000
    except (ValueError, TypeError):
        return None
    if price_in <= 0 and price_out <= 0:
        return None

    # Filtro deprecated
    exp = m.get("expiration_date")
    if exp:
        try:
            if datetime.fromisoformat(exp.replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return None
        except Exception:
            pass

    # Filtro fine-tune/base
    lower_id = mid.lower()
    if any(tag in lower_id for tag in [":free", "-base", "-raw", ":beta", "nitro", ":floor"]):
        return None

    # Whitelist de providers
    provider_prefix = mid.split("/")[0] if "/" in mid else ""
    if provider_prefix not in CURATED_PROVIDERS:
        return None

    # Filtro: só texto como input
    input_mods = arch.get("input_modalities", ["text"])
    if "text" not in input_mods:
        return None

    top = m.get("top_provider") or {}
    price_in_rounded = round(price_in, 4)
    return {
        "id": mid,
        "name": m.get("name", mid),
        "provider": "openrouter",
        "context_length": ctx,
        "max_output": top.get("max_completion_tokens") or 4096,
        "price_input": price_in_rounded,
        "price_output": round(price_out, 4),
        "tier": _classify_tier(price_in_rounded),
        "description": (m.get("description") or "")[:200],
        "modality": arch.get("modality", "text->text"),
    }


def _deduplicate_models(models: list[dict], max_per_family: int = 2) -> list[dict]:
    """Agrupa por família e mantém as melhores variantes."""
    families: dict[str, list[dict]] = {}
    for m in models:
        mid = m["id"]
        # Extrai família: google/gemini-2.5-flash -> google/gemini-2.5
        parts = mid.rsplit("-", 1)
        family = parts[0] if len(parts) > 1 else mid
        # Simplifica mais: remove sufixos comuns
        for suffix in ["-preview", "-latest", "-exp"]:
            family = family.replace(suffix, "")
        families.setdefault(family, []).append(m)

    result = []
    for family, members in families.items():
        # Ordena por: maior contexto primeiro, depois menor preço
        members.sort(key=lambda m: (-m["context_length"], m["price_input"]))
        result.extend(members[:max_per_family])
    return result


async def _fetch_market_models() -> list[dict]:
    """Busca modelos do mercado via OpenRouter API com cache."""
    global _models_cache, _models_cache_ts

    if _models_cache and (time.time() - _models_cache_ts) < _MODELS_CACHE_TTL:
        return _models_cache

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models")
            resp.raise_for_status()
            raw = resp.json().get("data", [])
    except Exception:
        return _models_cache  # retorna cache antigo se falhar

    # Pipeline de curadoria
    normalized = []
    for m in raw:
        n = _normalize_openrouter_model(m)
        if n:
            normalized.append(n)

    # Deduplicação
    curated = _deduplicate_models(normalized, max_per_family=2)

    # Score composto e ordenação
    now_ts = time.time()
    for m in curated:
        ctx_score = min(m["context_length"] / 1_000_000, 1.0)  # normaliza 0-1
        price_score = 1.0 / (1.0 + m["price_input"])  # menor preço = maior score
        m["_score"] = ctx_score * 0.5 + price_score * 0.5

    curated.sort(key=lambda m: -m["_score"])
    # Limita a ~30 modelos
    curated = curated[:30]
    # Remove score interno
    for m in curated:
        m.pop("_score", None)

    _models_cache = curated
    _models_cache_ts = time.time()
    return curated


def _resolve_architect_provider() -> dict:
    """Detecta providers conectados e escolhe o melhor para o arquiteto."""
    secrets_env = load_env(BASE_DIR / "secrets.global")
    cfg = load_env(BASE_DIR / "config.global")

    connected = []
    available_models: dict[str, list[str]] = {}

    # Anthropic (OAuth ou API key)
    anthropic_key = None
    claude_oauth = _check_oauth(CLAUDE_CREDS_PATH, ["claudeAiOauth", "accessToken"])
    if claude_oauth["status"] == "active":
        anthropic_key = "__oauth__"
    if not anthropic_key:
        anthropic_key = secrets_env.get("ANTHROPIC_API_KEY") or cfg.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        connected.append("anthropic")
        available_models["anthropic"] = list(_ANTHROPIC_DIRECT_IDS)

    # OpenRouter
    openrouter_key = secrets_env.get("OPENROUTER_API_KEY") or cfg.get("OPENROUTER_API_KEY")
    if openrouter_key:
        connected.append("openrouter")
        available_models["openrouter"] = []  # preenchido depois com market models

    # Codex (OAuth ou API key)
    codex_key = None
    codex_oauth = _check_oauth(CODEX_AUTH_PATH, ["tokens", "access_token"])
    if codex_oauth["status"] == "active":
        codex_key = "__oauth__"
    if not codex_key:
        codex_key = secrets_env.get("OPENAI_API_KEY")
    if codex_key:
        connected.append("codex")
        available_models["codex"] = list(_CODEX_DIRECT_IDS)

    # Escolher melhor provider para o arquiteto
    # Preferência: anthropic > openrouter > codex
    architect_provider = None
    architect_model = None
    if "anthropic" in connected:
        architect_provider = "anthropic"
        architect_model = "claude-sonnet-4-6"
    elif "openrouter" in connected:
        architect_provider = "openrouter"
        architect_model = "anthropic/claude-sonnet-4.6"
    elif "codex" in connected:
        architect_provider = "codex"
        architect_model = "gpt-5.4"

    return {
        "connected": connected,
        "available_models": available_models,
        "architect_provider": architect_provider,
        "architect_model": architect_model,
        "_anthropic_key": anthropic_key,
        "_openrouter_key": openrouter_key,
        "_codex_key": codex_key,
    }


def _format_context_size(tokens: int) -> str:
    """Formata tamanho de contexto para exibição amigável."""
    if tokens >= 1_000_000:
        return f"{tokens // 1_000_000}M"
    return f"{tokens // 1000}k"


async def _build_architect_system_prompt(provider_info: dict) -> str:
    """Monta o system prompt do arquiteto com dados dinâmicos do sistema."""
    # Nomes existentes (apenas para evitar colisão)
    existing_names = []
    if BOTS_DIR.exists():
        for d in sorted(BOTS_DIR.iterdir()):
            if d.is_dir() and (d / ".env").exists():
                existing_names.append(d.name)
    if SUBAGENTS_DIR.exists():
        for d in sorted(SUBAGENTS_DIR.iterdir()):
            if d.is_dir() and (d / ".env").exists():
                existing_names.append(d.name)

    # Catálogo: modelos diretos PRIMEIRO, depois OpenRouter
    catalog_lines = []

    # Modelos diretos (prioritários)
    if "anthropic" in provider_info["connected"]:
        catalog_lines.append("\n### Anthropic (Acesso Direto — PRIORIDADE)")
        for m in _ANTHROPIC_MODELS:
            ctx = _format_context_size(m["context_length"])
            catalog_lines.append(f"- {m['name']} ({m['id']}) | tier: {m['tier']} | contexto: {ctx} | ${m['price_input']}/MTok in")
    if "codex" in provider_info["connected"]:
        catalog_lines.append("\n### OpenAI (Acesso Direto — PRIORIDADE)")
        for m in _CODEX_MODELS:
            ctx = _format_context_size(m["context_length"])
            catalog_lines.append(f"- {m['name']} ({m['id']}) | tier: {m['tier']} | contexto: {ctx} | ${m['price_input']}/MTok in")

    # OpenRouter (secundário)
    if "openrouter" in provider_info["connected"]:
        market_models = await _fetch_market_models()
        if market_models:
            catalog_lines.append("\n### OpenRouter (via roteamento — usar se nenhum direto atender)")
            for m in market_models[:15]:
                ctx = _format_context_size(m["context_length"])
                tier = m.get("tier", _classify_tier(m["price_input"]))
                catalog_lines.append(f"- {m['name']} ({m['id']}) | tier: {tier} | contexto: {ctx} | ${m['price_input']}/MTok in")

    catalog_str = "\n".join(catalog_lines) if catalog_lines else "Nenhum modelo disponível"
    existing_names_str = ", ".join(existing_names) if existing_names else "nenhum"

    return f"""Você é o Arquiteto de Agentes da plataforma "SMB Claw".

O usuário descreve uma necessidade e você projeta a orquestração ideal de agentes e sub-agentes para resolver. Use linguagem simples — o usuário pode não ser técnico.

## Terminologia obrigatória
- Diga "agente" (nunca "bot")
- Diga "inteligência" (nunca "modelo" ou "LLM")
- Diga "capacidades" (nunca "ferramentas" ou "tools")
- Não use jargões como "token", "context window", "contexto de 1M", "provider", "MTok"
- Não mencione tamanho de contexto nem preço por token — o usuário não precisa disso

## Comunicação
- Perguntas: uma ou duas por vez, nunca questionário
- Explique POR QUE recomenda cada coisa
- Use analogias do dia a dia

## Perguntas obrigatórias antes de projetar
1. "Seus arquivos são grandes? Quantas linhas/páginas?"
2. "Precisa analisar tudo junto ou pode ser aos poucos?"
3. "Vai fazer uma vez ou repetir (diário/semanal)?"
4. "Só você vai usar ou outras pessoas?"

## Orquestração
- **Agente**: recebe mensagens via Telegram ou WhatsApp, coordena o trabalho — é o cérebro
  - **Telegram**: usa um token do @BotFather para funcionar
  - **WhatsApp**: conecta via QR code (como WhatsApp Web), sem necessidade de token
- **Sub-agente**: especialista chamado pelo agente, trabalha nos bastidores
  - Modo simples: responde uma vez, sem capacidades. Rápido e barato.
  - Modo agêntico: usa capacidades em vários passos. Mais capaz.

## Perguntas sobre canal
- Pergunte ao usuário se prefere Telegram ou WhatsApp (ou ambos, com agentes separados)
- WhatsApp é ideal quando os usuários já estão no WhatsApp e não querem instalar Telegram
- Telegram é mais flexível (botões, comandos, grupos)

## Capacidades disponíveis
📁 Arquivos (files) · 💻 Terminal (shell) · 🌐 Internet (http) · ⏰ Agendamentos (cron)
🔧 Git · 🐙 GitHub · 🗄️ Banco de dados (database) · 📓 Notion · 🔍 Busca web (tavily)

## Catálogo de inteligências (atualizado automaticamente)
Cada modelo tem um "tier" que indica sua capacidade relativa:
- **premium**: os mais inteligentes e capazes
- **standard**: bom equilíbrio entre capacidade e custo
- **economy**: rápidos e baratos, ideais para tarefas simples

Use APENAS estes dados. Nunca invente números.
{catalog_str}

## Nomes já em uso (evite ao criar novos agentes)
{existing_names_str}

## Regras de projeto
1. Sempre crie agentes NOVOS com nomes descritivos (ex: "analista-vendas", "gerador-relatorio")
2. Sugira um nome criativo e amigável para cada agente (ex: "Luna", "Atlas", "Bolt"). Use o campo "suggested_bot_name" no JSON. Esse será o nome do bot no Telegram — deve ser curto, memorável e combinar com a função do agente.
3. Use SOMENTE modelos de Acesso Direto listados acima — eles são prioritários e estão conectados
4. Agente principal: use o melhor modelo direto disponível (Sonnet 4.6, Opus 4.6, ou GPT-5.4)
5. Sub-agentes: podem usar modelos diretos mais econômicos (Haiku 4.5, GPT-5.1 Mini)
6. Modelos do OpenRouter: use APENAS se nenhum modelo direto atender à necessidade (ex: contexto insuficiente)
7. Para cada agente/sub-agente, recomende 2-3 opções rankeadas (rank 1 = recomendado)
8. Considere tamanho do contexto vs volume de dados
9. Se dados são grandes demais, recomende pré-processamento

## Formato do blueprint
Quando tiver informações suficientes, gere um RESUMO em linguagem simples e depois inclua um bloco JSON técnico dentro de um bloco ```json:

{{
  "title": "Nome do Projeto",
  "agents": [{{
    "name": "nome-slug-descritivo",
    "display_name": "Nome Amigável",
    "suggested_bot_name": "Nome Criativo (ex: Luna, Atlas, Bolt)",
    "description": "O que faz",
    "channel": "telegram ou whatsapp",
    "tools": ["files", "shell"],
    "role": "Papel do agente",
    "models": [
      {{"rank": 1, "id": "modelo-id", "provider": "provider", "tier": "premium"}},
      {{"rank": 2, "id": "modelo-id", "provider": "provider", "tier": "standard"}}
    ]
  }}],
  "subagents": [{{
    "name": "nome-slug-descritivo",
    "display_name": "Nome Amigável",
    "description": "O que faz",
    "tools": ["files"],
    "mode": "simple",
    "parent": "nome-do-agente-pai",
    "models": [...]
  }}],
  "connections": [
    {{"from": "nome-agente", "to": "nome-subagente", "label": "envia planilhas para"}}
  ],
  "warnings": [
    "Dica técnica útil (requisitos do servidor, limitações, boas práticas)"
  ]
}}

Responda sempre no mesmo idioma que o usuário usar."""


class ArchitectChatRequest(BaseModel):
    messages: list[dict]


@app.get("/api/architect/providers")
async def architect_providers():
    """Retorna providers conectados e info do arquiteto."""
    info = _resolve_architect_provider()
    # Preencher modelos OpenRouter se conectado
    if "openrouter" in info["connected"]:
        market = await _fetch_market_models()
        info["available_models"]["openrouter"] = [m["id"] for m in market[:15]]
    # Remover chaves internas
    return {
        "connected": info["connected"],
        "available_models": info["available_models"],
        "architect_provider": info["architect_provider"],
        "architect_model": info["architect_model"],
    }


@app.get("/api/architect/models")
async def architect_models():
    """Retorna catálogo curado de modelos do mercado."""
    market = await _fetch_market_models()
    all_models = market if market else (_ANTHROPIC_MODELS + _CODEX_MODELS)
    return {"models": all_models, "total": len(all_models)}


def _load_curated_openrouter_models() -> dict:
    """Carrega catálogo curado de modelos OpenRouter do JSON externo."""
    json_path = Path(__file__).resolve().parent / "openrouter-models.json"
    try:
        import json as _json
        return _json.loads(json_path.read_text())
    except Exception:
        return {"models": [], "price_legend": {}, "last_updated": ""}


_model_catalog_cache: dict | None = None
_model_catalog_mtime: float = 0


def _load_model_catalog() -> dict:
    """Carrega catálogo unificado de modelos de admin/model-catalog.json. Cacheia por mtime."""
    global _model_catalog_cache, _model_catalog_mtime
    catalog_path = Path(__file__).resolve().parent / "model-catalog.json"
    if not catalog_path.exists():
        return {}
    try:
        current_mtime = catalog_path.stat().st_mtime
        if _model_catalog_cache is not None and current_mtime == _model_catalog_mtime:
            return _model_catalog_cache
        _model_catalog_cache = json.loads(catalog_path.read_text())
        _model_catalog_mtime = current_mtime
    except Exception:
        if _model_catalog_cache is None:
            _model_catalog_cache = {}
    return _model_catalog_cache


@app.get("/api/architect/available-models")
async def architect_available_models():
    """Retorna modelos de todos os provedores conectados a partir do model-catalog.json."""
    info = _resolve_architect_provider()
    catalog = _load_model_catalog()
    providers_cat = catalog.get("providers", {})
    caps_emoji = catalog.get("caps_emoji", {})
    price_legend = catalog.get("price_legend", {})
    last_updated = catalog.get("last_updated", "")

    # Detectar claude-cli (OAuth Claude Code)
    connected = list(info["connected"])
    claude_oauth = _check_oauth(CLAUDE_CREDS_PATH, ["claudeAiOauth", "accessToken"])
    if claude_oauth["status"] == "active" and "claude-cli" not in connected:
        connected.append("claude-cli")

    # Labels curtos para exibição no dropdown
    _short_labels = {"claude-cli": "Anthropic", "anthropic": "Anthropic", "codex": "OpenAI"}

    # Modelos diretos (tudo que não é OpenRouter)
    # Se claude-cli e anthropic ambos conectados, pular anthropic (mesmos modelos)
    direct: list[dict] = []
    seen_ids: set[str] = set()
    for prov_key in ["claude-cli", "anthropic", "codex"]:
        if prov_key not in connected:
            continue
        prov_data = providers_cat.get(prov_key, {})
        prov_label = _short_labels.get(prov_key, prov_key)
        for m in prov_data.get("models", []):
            if m["id"] in seen_ids:
                continue
            seen_ids.add(m["id"])
            direct.append({
                "id": m["id"],
                "name": m["name"],
                "provider": prov_label,
                "caps": m.get("caps", []),
                "price": m.get("price", 2),
                "intelligence": m.get("intelligence", 3),
                "rec": m.get("rec", ""),
            })

    # Modelos OpenRouter (do catálogo unificado)
    has_openrouter = "openrouter" in connected
    openrouter_models = []
    if has_openrouter:
        prov_data = providers_cat.get("openrouter", {})
        for m in prov_data.get("models", []):
            openrouter_models.append({
                "id": m["id"],
                "name": m["name"],
                "specialty": m.get("rec", ""),
                "price_level": m.get("price", 2),
                "intelligence": m.get("intelligence", 3),
                "caps": m.get("caps", []),
            })

    return {
        "direct": direct,
        "openrouter": openrouter_models,
        "has_openrouter": has_openrouter,
        "caps_emoji": caps_emoji,
        "price_legend": price_legend,
        "last_updated": last_updated,
    }


def _build_provider_chain(provider_info: dict) -> list[tuple[str, str, dict]]:
    """Constrói cadeia de fallback: [(provider, model, client_kwargs), ...]"""
    chain = []

    # Anthropic (API key apenas, OAuth pode não funcionar para streaming)
    anthropic_key = provider_info["_anthropic_key"]
    if anthropic_key and anthropic_key != "__oauth__":
        chain.append(("anthropic", "claude-sonnet-4-6", {"api_key": anthropic_key}))
    elif anthropic_key == "__oauth__":
        try:
            creds = json.loads(CLAUDE_CREDS_PATH.read_text())
            token = creds.get("claudeAiOauth", {}).get("accessToken", "")
            if token:
                chain.append(("anthropic", "claude-sonnet-4-6", {"auth_token": token}))
        except Exception:
            pass

    # OpenRouter
    openrouter_key = provider_info["_openrouter_key"]
    if openrouter_key:
        chain.append(("openrouter", "anthropic/claude-sonnet-4.6", {"api_key": openrouter_key}))

    # Codex
    codex_key = provider_info["_codex_key"]
    if codex_key:
        if codex_key == "__oauth__":
            try:
                auth = json.loads(CODEX_AUTH_PATH.read_text())
                api_key = auth.get("tokens", {}).get("access_token", "")
                account_id = auth.get("account_id", "")
                if api_key:
                    chain.append(("codex", "gpt-5.4", {
                        "api_key": api_key, "account_id": account_id, "is_oauth": True,
                    }))
            except Exception:
                pass
        else:
            chain.append(("codex", "gpt-5.4", {"api_key": codex_key, "is_oauth": False}))

    return chain


def _stream_anthropic(model, system_prompt, messages, client_kwargs):
    """Streaming generator for Anthropic."""
    import anthropic as anthropic_sdk
    client = anthropic_sdk.Anthropic(**{k: v for k, v in client_kwargs.items() if k in ("api_key", "auth_token")})
    with client.messages.stream(
        model=model, max_tokens=4096, system=system_prompt,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text


def _stream_openrouter(model, system_prompt, messages, client_kwargs):
    """Streaming generator for OpenRouter."""
    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=client_kwargs["api_key"])
    oai_messages = [{"role": "system", "content": system_prompt}] + messages
    resp = client.chat.completions.create(model=model, max_tokens=4096, messages=oai_messages, stream=True)
    for chunk in resp:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


def _stream_codex(model, system_prompt, messages, client_kwargs):
    """Streaming generator for Codex/OpenAI."""
    from openai import OpenAI
    if client_kwargs.get("is_oauth"):
        headers = {}
        if client_kwargs.get("account_id"):
            headers["ChatGPT-Account-Id"] = client_kwargs["account_id"]
        client = OpenAI(
            api_key=client_kwargs["api_key"],
            base_url="https://chatgpt.com/backend-api/wham",
            default_headers=headers,
        )
    else:
        client = OpenAI(api_key=client_kwargs["api_key"])
    oai_messages = [{"role": "system", "content": system_prompt}] + messages
    resp = client.chat.completions.create(model=model, max_tokens=4096, messages=oai_messages, stream=True)
    for chunk in resp:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


_STREAM_FNS = {
    "anthropic": _stream_anthropic,
    "openrouter": _stream_openrouter,
    "codex": _stream_codex,
}


@app.post("/api/architect/chat")
async def architect_chat(req: ArchitectChatRequest):
    """Chat com o arquiteto via SSE streaming, com fallback entre providers."""
    provider_info = _resolve_architect_provider()
    chain = _build_provider_chain(provider_info)

    if not chain:
        async def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'content': 'Nenhum provedor de IA configurado. Acesse Configurações para adicionar uma chave de API.'})}\n\n"
        return StreamingResponse(error_stream(), media_type="text/event-stream")

    system_prompt = await _build_architect_system_prompt(provider_info)
    messages = [m for m in req.messages if m.get("role") in ("user", "assistant") and m.get("content")]

    async def stream_sse():
        last_error = None
        for prov, model, kwargs in chain:
            full_text = ""
            try:
                stream_fn = _STREAM_FNS.get(prov)
                if not stream_fn:
                    continue
                # Roda o generator sync em thread e coleta chunks
                import queue, threading
                q = queue.Queue()
                error_holder = [None]

                def run_stream():
                    try:
                        for chunk in stream_fn(model, system_prompt, messages, kwargs):
                            q.put(("token", chunk))
                        q.put(("end", None))
                    except Exception as e:
                        q.put(("error", e))

                t = threading.Thread(target=run_stream, daemon=True)
                t.start()

                success = False
                while True:
                    try:
                        kind, val = q.get(timeout=120)
                    except queue.Empty:
                        last_error = "Timeout: resposta demorou mais de 2 minutos"
                        break
                    if kind == "token":
                        full_text += val
                        yield f"data: {json.dumps({'type': 'token', 'content': val})}\n\n"
                    elif kind == "end":
                        success = True
                        break
                    elif kind == "error":
                        last_error = str(val)[:500]
                        break

                t.join(timeout=5)

                if success:
                    yield f"data: {json.dumps({'type': 'done', 'content': full_text})}\n\n"
                    return
                # Se falhou, tenta próximo provider
                if full_text:
                    # Se já enviou tokens parciais, não dá para fallback limpo
                    yield f"data: {json.dumps({'type': 'error', 'content': last_error or 'Erro desconhecido'})}\n\n"
                    return
                continue

            except Exception as e:
                last_error = str(e)[:500]
                continue

        yield f"data: {json.dumps({'type': 'error', 'content': last_error or 'Todos os provedores falharam.'})}\n\n"

    return StreamingResponse(stream_sse(), media_type="text/event-stream")


# ── Architect Conversations CRUD ─────────────────────────────────────────────


class ArchitectConversationSave(BaseModel):
    id: str
    title: str = ""
    messages: list[dict] = []
    blueprint: Optional[dict] = None
    selected_models: Optional[dict] = None


@app.get("/api/architect/conversations")
async def architect_conversations_list():
    """Lista conversas do arquiteto, ordenadas por atualização."""
    conn = _get_admin_db()
    try:
        rows = conn.execute(
            "SELECT id, title, blueprint, created_at, updated_at FROM architect_conversations ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "title": r["title"],
                "has_blueprint": r["blueprint"] is not None and r["blueprint"] != "null",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/api/architect/conversations/{conv_id}")
async def architect_conversation_get(conv_id: str):
    """Retorna uma conversa completa."""
    conn = _get_admin_db()
    try:
        row = conn.execute(
            "SELECT * FROM architect_conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, detail="Conversa não encontrada")
        return {
            "id": row["id"],
            "title": row["title"],
            "messages": json.loads(row["messages"]),
            "blueprint": json.loads(row["blueprint"]) if row["blueprint"] else None,
            "selected_models": json.loads(row["selected_models"]) if row["selected_models"] else {},
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


@app.post("/api/architect/conversations")
async def architect_conversation_save(conv: ArchitectConversationSave):
    """Cria ou atualiza uma conversa do arquiteto."""
    now = datetime.now(timezone.utc).isoformat()
    conn = _get_admin_db()
    try:
        existing = conn.execute(
            "SELECT id FROM architect_conversations WHERE id = ?", (conv.id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE architect_conversations
                   SET title=?, messages=?, blueprint=?, selected_models=?, updated_at=?
                   WHERE id=?""",
                (
                    conv.title,
                    json.dumps(conv.messages, ensure_ascii=False),
                    json.dumps(conv.blueprint, ensure_ascii=False) if conv.blueprint else None,
                    json.dumps(conv.selected_models, ensure_ascii=False) if conv.selected_models else None,
                    now,
                    conv.id,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO architect_conversations (id, title, messages, blueprint, selected_models, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    conv.id,
                    conv.title,
                    json.dumps(conv.messages, ensure_ascii=False),
                    json.dumps(conv.blueprint, ensure_ascii=False) if conv.blueprint else None,
                    json.dumps(conv.selected_models, ensure_ascii=False) if conv.selected_models else None,
                    now,
                    now,
                ),
            )
        conn.commit()
        return {"ok": True, "id": conv.id}
    finally:
        conn.close()


@app.delete("/api/architect/conversations/{conv_id}")
async def architect_conversation_delete(conv_id: str):
    """Remove uma conversa do arquiteto."""
    conn = _get_admin_db()
    try:
        conn.execute("DELETE FROM architect_conversations WHERE id = ?", (conv_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ── Architect: Create Agents from Blueprint ──────────────────────────────────

class ArchitectCreateRequest(BaseModel):
    blueprint: dict
    tokens: dict                       # {agent_slug: telegram_token}
    selected_models: dict              # {agent_slug: model_id}
    channels: dict = {}                # {agent_slug: 'telegram'|'whatsapp'}
    conversation_id: Optional[str] = None


def _resolve_provider_for_model(model_id: str) -> str:
    """Resolve provider string for .env based on model_id."""
    if model_id in _ANTHROPIC_DIRECT_IDS:
        oauth = _check_oauth(CLAUDE_CREDS_PATH, ["claudeAiOauth", "accessToken"])
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        return "claude-cli" if (oauth.get("status") == "active" and not api_key) else "anthropic"
    if model_id in _CODEX_DIRECT_IDS:
        return "codex"
    return "openrouter"


def _generate_soul(agent: dict, is_subagent: bool = False) -> str:
    """Generate soul.md content from blueprint agent data."""
    lines = [f"# {agent.get('display_name', agent.get('name', 'Assistente'))}"]
    if agent.get('suggested_bot_name'):
        lines.append(f"\nVocê se chama **{agent['suggested_bot_name']}**.")
    if agent.get('role'):
        lines.append(f"\n## Papel\n{agent['role']}")
    lines.append(f"\n## Descrição\n{agent.get('description', 'Um assistente útil.')}")
    if is_subagent:
        lines.append("\nVocê é um sub-agente especializado. Recebe tarefas do agente principal e foca na sua área de expertise.")
    return "\n".join(lines) + "\n"


def _start_bot_process(name: str):
    """Start a bot process (same logic as bot_action start)."""
    if IN_DOCKER:
        bot_dir = str(BOTS_DIR / name)
        bot_script = _get_bot_script(name)
        log_path = BASE_DIR / "logs" / f"{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fd = open(log_path, "a")
        subprocess.Popen(
            ["python3", bot_script, "--bot-dir", bot_dir],
            stdout=log_fd, stderr=log_fd, start_new_session=True,
        )
    else:
        service = f"claude-bot-{name}"
        subprocess.run(
            ["sudo", "systemctl", "start", service],
            capture_output=True, text=True, timeout=30,
        )


@app.post("/api/architect/create-agents")
async def architect_create_agents(req: ArchitectCreateRequest):
    """Create all agents and subagents from an architect blueprint."""
    blueprint = req.blueprint
    agents = blueprint.get("agents", [])
    subagents = blueprint.get("subagents", [])
    results = []

    # ── Validation ──
    all_agent_names = [a["name"] for a in agents]
    all_sub_names = [s["name"] for s in subagents]

    # Check name format
    for name in all_agent_names + all_sub_names:
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            raise HTTPException(400, detail=f"Nome inválido: {name}")

    # Check collisions
    collisions = []
    for name in all_agent_names:
        if (BOTS_DIR / name).is_dir():
            collisions.append(f"Agente '{name}' já existe")
    for name in all_sub_names:
        if (SUBAGENTS_DIR / name).exists():
            collisions.append(f"Sub-agente '{name}' já existe")
    if collisions:
        raise HTTPException(409, detail="; ".join(collisions))

    # Check all Telegram agents have tokens (WhatsApp uses QR code, no token needed)
    missing_tokens = [
        n for n in all_agent_names
        if req.channels.get(n, "telegram") == "telegram" and not req.tokens.get(n, "").strip()
    ]
    if missing_tokens:
        raise HTTPException(400, detail=f"Token faltando para: {', '.join(missing_tokens)}")

    # Check all have models selected
    missing_models = [n for n in all_agent_names + all_sub_names if not req.selected_models.get(n, "").strip()]
    if missing_models:
        raise HTTPException(400, detail=f"Modelo não selecionado para: {', '.join(missing_models)}")

    # ── Create main agents ──
    script = BASE_DIR / "criar-bot.sh"
    for agent in agents:
        name = agent["name"]
        channel = req.channels.get(name, "telegram")
        try:
            if not script.exists():
                raise RuntimeError("criar-bot.sh not found")

            cmd = ["bash", str(script), name, "--channel", channel]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr or result.stdout)

            # Resolve model & provider
            model_id = req.selected_models[name]
            provider = _resolve_provider_for_model(model_id)

            # Patch .env
            env_path = BOTS_DIR / name / ".env"
            patches = {
                "BOT_NAME": agent.get("display_name", agent.get("suggested_bot_name", name)),
                "MODEL": model_id,
                "PROVIDER": provider,
                "TOOLS": ",".join(agent.get("tools", [])) or "none",
                "DESCRIPTION": agent.get("description", ""),
            }
            if channel == "telegram":
                patches["TELEGRAM_TOKEN"] = req.tokens[name].strip()
            write_env(env_path, patches)

            # Write soul.md
            soul = _generate_soul(agent)
            (BOTS_DIR / name / "soul.md").write_text(soul, encoding="utf-8")

            # Start bot
            _start_bot_process(name)

            results.append({"name": name, "type": "agent", "ok": True})
        except Exception as e:
            results.append({"name": name, "type": "agent", "ok": False, "error": str(e)})

    # ── Create subagents ──
    SUBAGENTS_DIR.mkdir(exist_ok=True)
    for sub in subagents:
        name = sub["name"]
        try:
            d = SUBAGENTS_DIR / name
            d.mkdir()

            model_id = req.selected_models[name]
            provider = _resolve_provider_for_model(model_id)

            env_content = (
                f"NAME={sub.get('display_name', name)}\n"
                f"DESCRIPTION={sub.get('description', '')}\n"
                f"PROVIDER={provider}\n"
                f"MODEL={model_id}\n"
                f"MODE={sub.get('mode', 'simple')}\n"
                f"TOOLS={','.join(sub.get('tools', [])) or 'none'}\n"
                f"ALLOWED_PARENTS={sub.get('parent', '*')}\n"
            )
            (d / ".env").write_text(env_content)

            soul = _generate_soul(sub, is_subagent=True)
            (d / "soul.md").write_text(soul, encoding="utf-8")

            results.append({"name": name, "type": "subagent", "ok": True})
        except Exception as e:
            results.append({"name": name, "type": "subagent", "ok": False, "error": str(e)})

    created = sum(1 for r in results if r["ok"])
    failed = sum(1 for r in results if not r["ok"])
    return {"results": results, "created": created, "failed": failed}
