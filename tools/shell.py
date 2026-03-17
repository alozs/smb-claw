"""Ferramentas: run_shell, manage_cron, manage_files."""

import subprocess
import logging
from pathlib import Path

from security import check_shell_safety, resolve_safe_path

logger = logging.getLogger(__name__)


def get_definitions():
    return [{
        "name": "run_shell",
        "description": "Executa comando shell na VPS.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["command"],
        },
    }]


def get_cron_definitions():
    return [{
        "name": "manage_cron",
        "description": "Gerencia cron jobs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action":   {"type": "string", "enum": ["list", "add", "remove"]},
                "schedule": {"type": "string"},
                "command":  {"type": "string"},
                "comment":  {"type": "string"},
            },
            "required": ["action"],
        },
    }]


def get_file_definitions(work_dir):
    return [{
        "name": "manage_files",
        "description": f"Gerencia arquivos no workspace isolado: {work_dir}",
        "input_schema": {
            "type": "object",
            "properties": {
                "action":  {"type": "string", "enum": ["read", "write", "list", "delete"]},
                "path":    {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["action"],
        },
    }]


def execute(name: str, inp: dict, *, config: dict) -> str:
    bot_name = config["BOT_NAME"]
    work_dir = config["WORK_DIR"]
    protected_paths = config["PROTECTED_PATHS"]
    append_daily_log = config["append_daily_log"]

    if name == "run_shell":
        cmd = inp["command"]
        err = check_shell_safety(cmd, protected_paths)
        if err:
            return err
        timeout = inp.get("timeout", 30)
        logger.info(f"[shell] {cmd}")
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = ""
        if r.stdout:
            out += f"stdout:\n{r.stdout}"
        if r.stderr:
            out += f"stderr:\n{r.stderr}"
        return (out + f"\nexit: {r.returncode}").strip() or "(sem saída)"

    if name == "manage_cron":
        import shutil as _shutil
        if not _shutil.which("crontab"):
            return "Erro: crontab não disponível neste ambiente. Em Docker, instale com: apt-get install -y cron && service cron start"
        action = inp["action"]
        if action == "list":
            r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            return r.stdout.strip() or "(nenhum cron)"
        if action == "add":
            schedule = inp.get("schedule", "")
            command = inp.get("command", "")
            comment = inp.get("comment", f"bot-{bot_name}")
            if not schedule or not command:
                return "Erro: schedule e command obrigatórios"
            existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            current = existing.stdout if existing.returncode == 0 else ""
            r = subprocess.run(
                ["crontab", "-"],
                input=current + f"# {comment}\n{schedule} {command}\n",
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                append_daily_log(f"Cron adicionado: `{schedule} {command}` ({comment})")
                return f"✅ Cron adicionado: {schedule} {command}"
            return f"Erro: {r.stderr}"
        if action == "remove":
            command = inp.get("command", "")
            comment = inp.get("comment", "")
            existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            if existing.returncode != 0:
                return "Nenhum cron"
            filtered, skip = [], False
            for line in existing.stdout.splitlines():
                if skip:
                    skip = False
                    continue
                if (comment and comment in line and line.startswith("#")) or \
                   (command and command in line and not line.startswith("#")):
                    skip = True
                    continue
                filtered.append(line)
            r = subprocess.run(
                ["crontab", "-"], input="\n".join(filtered) + "\n",
                capture_output=True, text=True,
            )
            if r.returncode == 0:
                append_daily_log(f"Cron removido: {command or comment}")
            return "✅ Cron removido" if r.returncode == 0 else f"Erro: {r.stderr}"

    if name == "manage_files":
        action = inp["action"]
        if action == "list":
            work_dir.mkdir(parents=True, exist_ok=True)
            files = sorted(work_dir.iterdir()) if work_dir.exists() else []
            return "\n".join(f.name for f in files) or "(workspace vazio)"
        path_raw = inp.get("path", "")
        if not path_raw:
            return "Erro: path obrigatório"
        target = resolve_safe_path(path_raw, work_dir)
        if target is None:
            return "Acesso fora do workspace negado"
        if action == "read":
            return target.read_text(encoding="utf-8") if target.exists() else "Não encontrado"
        if action == "write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(inp.get("content", ""), encoding="utf-8")
            return f"✅ Salvo: {path_raw}"
        if action == "delete":
            if target.exists():
                target.unlink()
                return f"✅ Removido: {path_raw}"
            return "Não encontrado"

    return f"Ação desconhecida: {name}"
