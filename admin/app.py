import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR = Path("/home/ubuntu/claude-bots")
BOTS_DIR = BASE_DIR / "bots"
SUBAGENTS_DIR = BASE_DIR / "subagents"

FILE_WHITELIST = {"soul.md", "USER.md", "MEMORY.md", "welcome.md"}
GLOBAL_WHITELIST = {"context.global", "config.global", "secrets.global"}

SENSITIVE_KEYS = {"ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"}

app = FastAPI(title="Claude Bots Admin")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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


def get_uptime(bot_name: str) -> str:
    """Return human-readable uptime string or '—' if inactive."""
    service = f"claude-bot-{bot_name}"
    try:
        result = subprocess.run(
            ["systemctl", "show", service, "--property=ActiveEnterTimestamp"],
            capture_output=True, text=True, timeout=5
        )
        line = result.stdout.strip()
        # Format: "ActiveEnterTimestamp=Fri 2026-03-13 15:42:39 UTC"
        if "=" not in line:
            return "—"
        ts_str = line.split("=", 1)[1].strip()
        if not ts_str or ts_str == "n/a":
            return "—"
        # Parse the timestamp — systemd uses format like "Fri 2026-03-13 15:42:39 UTC"
        parts = ts_str.split()
        # parts: ['Fri', '2026-03-13', '15:42:39', 'UTC']
        if len(parts) < 3:
            return "—"
        dt_str = f"{parts[1]} {parts[2]}"
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        total_seconds = int(delta.total_seconds())
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
    except Exception:
        return "—"


def get_bot_summary(bot_name: str) -> dict:
    env = get_bot_env(bot_name)
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
    service = f"claude-bot-{name}"

    # Stop and disable service
    for cmd in ["stop", "disable"]:
        subprocess.run(["sudo", "systemctl", cmd, service],
                       capture_output=True, text=True, timeout=30)

    # Remove service file
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
    env = get_bot_env(name)
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


@app.delete("/api/bots/{name}/schedules/{sid}")
async def delete_schedule(name: str, sid: int):
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
        r = subprocess.run(
            ["sudo", "systemctl", "restart", f"claude-bot-{name}"],
            capture_output=True, text=True, timeout=15
        )
        results.append({"name": name, "ok": r.returncode == 0})
    return {"results": results}


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
