#!/usr/bin/env python3
"""Bug Fixer Agent — Detecta e corrige erros nos bots automaticamente.

Executado via cron. Parte do core do sistema SMB Claw.
Use BUGFIXER_OVERRIDE=true para forçar execução mesmo com BUGFIXER_ENABLED=false.
"""

import json
import os
import sqlite3
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path("/home/ubuntu/claude-bots")
BOTS_DIR = BASE_DIR / "bots"
STATE_FILE = BASE_DIR / ".bugfixer_state"
LOG_FILE = BASE_DIR / "logs" / "bugfixer.log"


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ── Config ────────────────────────────────────────────────────────────────────

def load_env_file(path: Path) -> dict:
    """Parse a .env / config file into a dict, skipping comments and blanks."""
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


def load_config() -> dict:
    """Load config with precedence: config.global → secrets.global."""
    cfg = {}
    for path in [BASE_DIR / "config.global", BASE_DIR / "secrets.global"]:
        for k, v in load_env_file(path).items():
            cfg[k] = v  # later files override
    return cfg


# ── State ─────────────────────────────────────────────────────────────────────

def read_state() -> str:
    """Return ISO timestamp of last run, or epoch start if no state."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return data.get("last_run", "1970-01-01T00:00:00")
        except Exception:
            pass
    return "1970-01-01T00:00:00"


def write_state():
    """Update .bugfixer_state with current UTC timestamp."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    STATE_FILE.write_text(json.dumps({"last_run": ts}, indent=2))


# ── Telegram ──────────────────────────────────────────────────────────────────

def get_telegram_token(cfg: dict) -> str:
    """Return the Telegram token to use for admin notifications.

    Priority:
    1. BUGFIXER_TELEGRAM_TOKEN in config.global (explicit, recommended)
    2. TELEGRAM_TOKEN from the first available bot (fallback)
    """
    explicit = cfg.get("BUGFIXER_TELEGRAM_TOKEN", "").strip()
    if explicit:
        return explicit
    if BOTS_DIR.exists():
        for bot_dir in sorted(BOTS_DIR.iterdir()):
            if not bot_dir.is_dir():
                continue
            env = load_env_file(bot_dir / ".env")
            token = env.get("TELEGRAM_TOKEN", "").strip()
            if token:
                log(f"[warn] BUGFIXER_TELEGRAM_TOKEN não configurado. "
                    f"Usando token de '{bot_dir.name}' como fallback.")
                return token
    return ""


def send_telegram(token: str, chat_id: str, text: str):
    """Send a plain-text Telegram message via Bot API."""
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as e:
        log(f"  [warn] Telegram send failed: {e}")


# ── Analytics ─────────────────────────────────────────────────────────────────

def get_bot_errors(bot_name: str, since: str) -> list:
    """Return list of (ts, error) tuples from analytics since timestamp."""
    db_path = BOTS_DIR / bot_name / "bot_data.db"
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA busy_timeout=5000")
        rows = conn.execute(
            "SELECT ts, error FROM analytics "
            "WHERE error != '' AND error IS NOT NULL AND ts >= ? "
            "ORDER BY ts",
            (since,),
        ).fetchall()
        conn.close()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        log(f"  [warn] DB query failed for {bot_name}: {e}")
        return []


# ── Journalctl ────────────────────────────────────────────────────────────────

def get_journalctl_errors(bot_name: str) -> str:
    """Get recent warning/error lines from journalctl for the bot service."""
    service = f"claude-bot-{bot_name}"
    try:
        result = subprocess.run(
            ["journalctl", "-u", service, "-n", "100",
             "--no-pager", "--output=short-iso", "-p", "warning"],
            capture_output=True, text=True, timeout=15,
        )
        lines = result.stdout.strip()
        if not lines:
            return "(sem logs de erro recentes)"
        lines_list = lines.splitlines()
        if len(lines_list) > 50:
            lines_list = lines_list[-50:]
        return "\n".join(lines_list)
    except Exception as e:
        return f"(erro ao obter journalctl: {e})"


# ── Prompt ────────────────────────────────────────────────────────────────────

def build_prompt(bot_name: str, errors: list, journal_logs: str) -> str:
    """Build the Claude prompt for fixing a bot's errors."""
    error_lines = "\n".join(f"  {ts}: {err}" for ts, err in errors[:20])
    return f"""Você é o Bug Fixer Agent do sistema SMB Claw.

Bot: {bot_name}
Base: /home/ubuntu/claude-bots/

Erros detectados:
{error_lines}

Logs journalctl:
{journal_logs}

Tarefa:
1. Leia os arquivos fonte relevantes (bot.py, db.py, scheduler.py, tools/)
2. Identifique a causa raiz
3. Faça a correção mínima necessária
4. Reinicie: sudo systemctl restart claude-bot-{bot_name}
5. Verifique: systemctl is-active claude-bot-{bot_name}
6. Responda com: causa raiz | correção | arquivo(s) modificado(s) | status

REGRAS:
- Corrija apenas o necessário. Não refatore nem melhore código não relacionado ao bug.
- Se o erro for transitório (timeout de rede, etc.), diga isso e NÃO modifique arquivos.
- Se o erro não tiver correção óbvia, descreva o problema e aguarde intervenção humana.
"""


# ── Claude ────────────────────────────────────────────────────────────────────

def invoke_claude(prompt: str) -> str:
    """Invoke claude CLI with bypassPermissions and return the response."""
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", "claude-sonnet-4-6",
             "--permission-mode", "bypassPermissions"],
            input=prompt,
            capture_output=True, text=True,
            timeout=1800,  # 30 minutes max
        )
        if result.returncode != 0:
            return (
                f"[Erro ao invocar Claude: código {result.returncode}]\n"
                + result.stderr[:500]
            )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return "[Erro: Claude excedeu 30 minutos]"
    except FileNotFoundError:
        return "[Erro: claude CLI não encontrado no PATH]"
    except Exception as e:
        return f"[Erro inesperado: {e}]"


# ── Bot processing ────────────────────────────────────────────────────────────

def process_bot(bot_name: str, since: str, cfg: dict, token: str):
    """Process a single bot: detect errors, invoke Claude, notify admin."""
    admin_id = cfg.get("ADMIN_ID", "")

    log(f"  Verificando bot: {bot_name}")
    errors = get_bot_errors(bot_name, since)

    if not errors:
        log(f"  → Sem erros novos. Pulando.")
        return

    log(f"  → {len(errors)} erro(s) detectado(s). Analisando...")

    if admin_id and token:
        send_telegram(
            token, admin_id,
            f"🔍 Bug Fixer — detectei {len(errors)} erro(s) em {bot_name}. Analisando...",
        )

    journal_logs = get_journalctl_errors(bot_name)
    prompt = build_prompt(bot_name, errors, journal_logs)

    log(f"  → Invocando Claude...")
    response = invoke_claude(prompt)
    log(f"  → Claude respondeu ({len(response)} chars)")

    if admin_id and token:
        msg = f"🔧 Bug Fixer — {bot_name}\n\n{response[:3800]}"
        send_telegram(token, admin_id, msg)

    log(f"  → Resposta (primeiros 500 chars):\n{response[:500]}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log("=" * 60)
    log("Bug Fixer Agent iniciado")

    cfg = load_config()

    # Check if enabled (config file or env var override for manual runs)
    enabled = cfg.get("BUGFIXER_ENABLED", "false").lower() == "true"
    override = os.environ.get("BUGFIXER_OVERRIDE", "false").lower() == "true"

    if not enabled and not override:
        log("BUGFIXER_ENABLED != true. Saindo.")
        return

    since = read_state()
    log(f"Analisando erros desde: {since}")

    token = get_telegram_token(cfg)
    if not token:
        log("[warn] Nenhum TELEGRAM_TOKEN encontrado. Configure BUGFIXER_TELEGRAM_TOKEN em config.global.")

    if not BOTS_DIR.exists():
        log("Diretório bots/ não encontrado. Saindo.")
        write_state()
        return

    bot_dirs = sorted(d for d in BOTS_DIR.iterdir() if d.is_dir())
    log(f"Bots encontrados: {[d.name for d in bot_dirs]}")

    for bot_dir in bot_dirs:
        try:
            process_bot(bot_dir.name, since, cfg, token)
        except Exception as e:
            log(f"  [erro] Falha ao processar {bot_dir.name}: {e}")

    write_state()
    log("Estado atualizado. Bug Fixer concluído.")
    log("=" * 60)


if __name__ == "__main__":
    main()
