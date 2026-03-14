"""Ferramentas de tarefas persistentes: task_create, task_update, task_list."""

import uuid
import logging

logger = logging.getLogger(__name__)

DEFINITIONS = [
    {
        "name": "task_create",
        "description": (
            "Cria uma tarefa persistente. Use quando o usuário pedir algo complexo "
            "com múltiplos passos ou que pode levar tempo. "
            "A tarefa sobrevive a crashes e reinicializações do bot."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title":       {"type": "string", "description": "Título curto da tarefa"},
                "description": {"type": "string", "description": "Descrição completa do que precisa ser feito"},
                "steps":       {"type": "array", "items": {"type": "string"},
                                "description": "Lista ordenada de passos para completar a tarefa"},
            },
            "required": ["title", "description", "steps"],
        },
    },
    {
        "name": "task_update",
        "description": (
            "Atualiza o progresso de uma tarefa. Chame após completar cada passo. "
            "Salva estado em disco — se o bot reiniciar, o progresso é preservado."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":      {"type": "string"},
                "current_step": {"type": "integer"},
                "progress":     {"type": "string"},
                "status":       {"type": "string", "enum": ["in_progress", "completed", "failed", "paused"]},
                "context":      {"type": "object"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "task_list",
        "description": "Lista tarefas do usuário atual, opcionalmente filtradas por status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string",
                           "enum": ["all", "in_progress", "paused", "completed", "failed", "cancelled"]},
            },
            "required": [],
        },
    },
]


def task_status_emoji(status: str) -> str:
    return {"pending": "⏳", "in_progress": "▶️", "paused": "⏸️",
            "completed": "✅", "failed": "❌", "cancelled": "🚫"}.get(status, "❓")


def execute(name: str, inp: dict, *, user_id: int, db, config: dict) -> str:
    if name == "task_create":
        if not user_id:
            return "Erro: contexto de usuário indisponível para criar tarefa"
        tid = str(uuid.uuid4())[:8]
        db.task_create(
            user_id=user_id, tid=tid,
            title=inp["title"],
            description=inp.get("description", ""),
            steps=inp.get("steps", []),
        )
        n = len(inp.get("steps", []))
        logger.info(f"[task] criada {tid}: {inp['title']}")
        return f"✅ Tarefa criada: `{tid}`{f' ({n} passos)' if n else ''}\nTítulo: {inp['title']}"

    if name == "task_update":
        tid = inp["task_id"]
        kwargs = {k: v for k, v in inp.items() if k != "task_id"}
        if not db.task_update(tid, **kwargs):
            return f"Tarefa `{tid}` não encontrada."
        status = inp.get("status", "")
        if status == "completed":
            return f"✅ Tarefa `{tid}` concluída!"
        if status == "failed":
            return f"❌ Tarefa `{tid}` marcada como falha."
        return f"✅ Tarefa `{tid}` atualizada."

    if name == "task_list":
        if not user_id:
            return "Erro: contexto de usuário indisponível"
        sf = inp.get("status", "all")
        items = db.tasks_for_user(user_id) if sf == "all" else db.tasks_for_user(user_id, status=sf)
        if not items:
            return "(nenhuma tarefa)"
        lines = []
        for t in items[:20]:
            emoji = task_status_emoji(t["status"])
            steps = t.get("steps", [])
            si = f" [{t['current_step']+1}/{len(steps)}]" if steps else ""
            lines.append(f"{emoji} [{t['id']}] {t['title']}{si}")
            if t.get("progress"):
                lines.append(f"   → {t['progress'][:80]}")
        return "\n".join(lines)

    return f"Ferramenta desconhecida: {name}"
