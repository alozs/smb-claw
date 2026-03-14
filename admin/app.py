import asyncio
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional

# ── Config ────────────────────────────────────────────────────────────────────

BASE_DIR = Path("/home/ubuntu/claude-bots")
BOTS_DIR = BASE_DIR / "bots"

FILE_WHITELIST = {"soul.md", "USER.md", "MEMORY.md", "welcome.md"}
GLOBAL_WHITELIST = {"context.global", "config.global", "secrets.global"}

SENSITIVE_KEYS = {"ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"}

app = FastAPI(title="Claude Bots Admin")
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
    return {
        "name": bot_name,
        "active": active,
        "provider": provider,
        "model": model,
        "msgs_today": msgs_today,
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


class CreateBotRequest(BaseModel):
    name: str
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
