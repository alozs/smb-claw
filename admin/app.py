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

IN_DOCKER = Path("/.dockerenv").exists() or bool(os.environ.get("IN_DOCKER"))

FILE_WHITELIST = {"soul.md", "USER.md", "MEMORY.md", "welcome.md", "secrets.env"}
GLOBAL_WHITELIST = {"context.global", "config.global", "secrets.global"}

SENSITIVE_KEYS = {"ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"}

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

    return {
        "name": bot_name,
        "display_name": display_name,
        "description": description,
        "active": active,
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
    return templates.TemplateResponse("index.html", {"request": request})


# ── Routes: Bots ──────────────────────────────────────────────────────────────

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


class CreateBotRequest(BaseModel):
    name: str
    display_name: Optional[str] = ""
    description: Optional[str] = ""
    model: Optional[str] = ""
    provider: Optional[str] = "claude-cli"
    tools: Optional[list] = []
    telegram_token: Optional[str] = ""


@app.post("/api/bots")
async def create_bot(req: CreateBotRequest):
    if not re.match(r"^[a-zA-Z0-9_-]+$", req.name):
        raise HTTPException(400, detail="Invalid bot name")
    if (BOTS_DIR / req.name).is_dir():
        raise HTTPException(409, detail="Bot already exists")

    script = BASE_DIR / "criar-bot.sh"
    if not script.exists():
        raise HTTPException(500, detail="criar-bot.sh not found")

    result = subprocess.run(
        ["bash", str(script), req.name],
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
    if req.telegram_token:
        patches["TELEGRAM_TOKEN"] = req.telegram_token
    if req.display_name:
        patches["BOT_NAME"] = req.display_name
    if req.description:
        patches["DESCRIPTION"] = req.description
    if patches:
        write_env(env_path, patches)

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
        if req.action in ("stop", "restart"):
            subprocess.run(["pkill", "-f", "--", f"--bot-dir.*bots/{name}"],
                           capture_output=True, timeout=10)
            # Aguarda o processo morrer (até 10s) e limpa o lock file
            for _ in range(10):
                r = subprocess.run(["pgrep", "-f", "--", f"bot.py --bot-dir.*bots/{name}"],
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
                ["python3", str(BASE_DIR / "bot.py"), "--bot-dir", bot_dir],
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
                r = subprocess.run(["pgrep", "-f", "--", f"bot.py --bot-dir.*bots/{name}"],
                                   capture_output=True)
                if r.returncode != 0:
                    break
                time.sleep(1)
            for lf in (BASE_DIR / ".locks").glob(f"*{name}*"):
                lf.unlink(missing_ok=True)
            bot_dir = str(BOTS_DIR / name)
            log_path = BASE_DIR / "logs" / f"{name}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = open(log_path, "a")
            subprocess.Popen(
                ["python3", str(BASE_DIR / "bot.py"), "--bot-dir", bot_dir],
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
