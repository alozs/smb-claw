"""Ferramentas de memória: memory_write, memory_read, state_rw."""

import json
import re
from datetime import date, timedelta, datetime
from pathlib import Path

DEFINITIONS = [
    {
        "name": "memory_write",
        "description": (
            "Salva informação na memória do bot. "
            "Use 'daily' para registrar eventos do dia (decisões, problemas, tarefas). "
            "Use 'long_term' para salvar algo que deve ser lembrado permanentemente. "
            "Use 'long_term_replace' para reescrever toda a memória longa (use com cuidado)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target":  {"type": "string", "enum": ["daily", "long_term", "long_term_replace"]},
                "content": {"type": "string", "description": "Conteúdo a salvar"},
            },
            "required": ["target", "content"],
        },
    },
    {
        "name": "memory_read",
        "description": "Lê arquivos de memória do bot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "enum": ["long_term", "today", "yesterday", "user_profile", "list_days"],
                },
                "date": {"type": "string", "description": "Data específica YYYY-MM-DD (opcional)"},
            },
            "required": ["target"],
        },
    },
    {
        "name": "state_rw",
        "description": (
            "Lê ou escreve arquivos de estado JSON (heartbeat-state, cron-state, locks, etc.). "
            "Use para controle de idempotência em automações."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write", "merge"]},
                "name":   {"type": "string", "description": "Nome do arquivo de estado (sem .json)"},
                "data":   {"type": "object", "description": "Dados a salvar (para write/merge)"},
            },
            "required": ["action", "name"],
        },
    },
]


def _read_file_safe(path: Path, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    content = path.read_text(encoding="utf-8").strip()
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n... (truncado — {len(content)} chars total)"
    return content


def execute(name: str, inp: dict, *, config: dict) -> str:
    bot_dir = config["BOT_DIR"]
    mem_dir = config["MEM_DIR"]

    if name == "memory_write":
        target = inp["target"]
        content = inp["content"]
        if target == "daily":
            _append_daily_log(content, mem_dir)
            return f"✅ Salvo na memória diária ({date.today().isoformat()})"
        if target == "long_term":
            _append_long_term(content, bot_dir)
            return "✅ Adicionado à memória de longo prazo"
        if target == "long_term_replace":
            path = bot_dir / "MEMORY.md"
            path.write_text(content.strip() + "\n", encoding="utf-8")
            path.chmod(0o600)
            return "✅ Memória de longo prazo substituída"

    if name == "memory_read":
        target = inp["target"]
        date_str = inp.get("date", "")

        if target == "list_days":
            files = sorted(mem_dir.glob("*.md"), reverse=True)
            return "\n".join(f.stem for f in files) or "(nenhum arquivo de memória diária)"
        if target == "long_term":
            return _read_file_safe(bot_dir / "MEMORY.md") or "(MEMORY.md vazio)"
        if target == "user_profile":
            return _read_file_safe(bot_dir / "USER.md") or "(USER.md não encontrado)"
        if target in ("today", "yesterday") or date_str:
            if date_str:
                try:
                    d = date.fromisoformat(date_str)
                except ValueError:
                    return f"Data inválida: {date_str}"
            elif target == "today":
                d = date.today()
            else:
                d = date.today() - timedelta(days=1)
            content = _read_file_safe(mem_dir / f"{d.isoformat()}.md")
            return content or f"(sem memória para {d.isoformat()})"

    if name == "state_rw":
        action = inp["action"]
        sname = inp["name"]
        if not re.match(r'^[\w\-]+$', sname):
            return "Erro: nome de estado inválido"
        path = bot_dir / f"{sname}.json"

        if action == "read":
            if path.exists():
                try:
                    data = json.loads(path.read_text())
                    return json.dumps(data, ensure_ascii=False, indent=2)
                except Exception:
                    return "{}"
            return "{}"
        if action == "write":
            path.write_text(json.dumps(inp.get("data", {}), ensure_ascii=False, indent=2))
            path.chmod(0o600)
            return f"✅ Estado '{sname}' salvo"
        if action == "merge":
            existing = {}
            if path.exists():
                try:
                    existing = json.loads(path.read_text())
                except Exception:
                    pass
            existing.update(inp.get("data", {}))
            path.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
            path.chmod(0o600)
            return f"✅ Estado '{sname}' mesclado"

    return f"Ação desconhecida para {name}"


def _append_daily_log(content: str, mem_dir: Path, day=None):
    d = day or date.today()
    path = mem_dir / f"{d.isoformat()}.md"
    timestamp = datetime.now().strftime("%H:%M")
    entry = f"\n### {timestamp}\n{content.strip()}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
    path.chmod(0o600)


def _append_long_term(content: str, bot_dir: Path):
    path = bot_dir / "MEMORY.md"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"\n## [{timestamp}]\n{content.strip()}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
    path.chmod(0o600)
