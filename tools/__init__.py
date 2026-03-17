"""
Registry de ferramentas + dispatcher central.
Cada módulo em tools/ exporta DEFINITIONS (lista) e execute(inp, **ctx) -> str.

Todas as execuções rodam via asyncio.to_thread() para não bloquear o event loop.
"""

import asyncio
import logging

from tools import memory, tasks, shell, github_tool, git, http, database, schedule, telegram_file

logger = logging.getLogger("tools")


def build_definitions(enabled_tools: set, work_dir, base_dir=None, bot_name: str = "",
                      for_subagent: bool = False) -> list[dict]:
    """Constrói lista de tool definitions baseado nos tools habilitados.

    for_subagent: quando True, não inclui ferramentas "sempre ativas" (tasks, memory, schedule).
    Subagentes usam apenas as ferramentas declaradas em seu TOOLS no .env.
    """
    defs = []
    # Sempre disponíveis (apenas para bots pai, não para sub-agentes)
    if not for_subagent:
        defs.extend(tasks.DEFINITIONS)
        defs.extend(memory.DEFINITIONS)
        defs.extend(schedule.DEFINITIONS)
    # Condicionais
    if "shell" in enabled_tools:
        defs.extend(shell.get_definitions())
    if "cron" in enabled_tools:
        defs.extend(shell.get_cron_definitions())
    if "files" in enabled_tools:
        defs.extend(shell.get_file_definitions(work_dir))
        defs.extend(telegram_file.DEFINITIONS)
    if "http" in enabled_tools:
        defs.extend(http.DEFINITIONS)
    if "git" in enabled_tools:
        defs.extend(git.get_definitions(work_dir))
    if "github" in enabled_tools:
        defs.extend(github_tool.DEFINITIONS)
    if "database" in enabled_tools:
        defs.extend(database.DEFINITIONS)
    # Sub-agentes (apenas quando base_dir é fornecido — evita recursão em sub-agentes)
    if base_dir is not None:
        from tools import agent as agent_tool
        from pathlib import Path
        defs.extend(agent_tool.build_definitions(Path(base_dir), bot_name))
    return defs


def _execute_sync(name: str, inp: dict, *, user_id: int = 0, db, config: dict) -> str:
    """Dispatcher síncrono — roda dentro de to_thread."""
    # Tarefas (sempre disponíveis)
    if name in ("task_create", "task_update", "task_list"):
        return tasks.execute(name, inp, user_id=user_id, db=db, config=config)
    # Memória (sempre disponível)
    if name in ("memory_write", "memory_read", "state_rw"):
        return memory.execute(name, inp, config=config)
    # Schedule (sempre disponível)
    if name == "schedule":
        return schedule.execute(inp, user_id=user_id, db=db)
    # Shell / Cron / Files
    if name in ("run_shell", "manage_cron", "manage_files"):
        return shell.execute(name, inp, config=config)
    # HTTP
    if name == "http_request":
        return http.execute(inp, config=config)
    # Git
    if name == "git_op":
        return git.execute(inp, config=config)
    # GitHub
    if name == "github":
        return github_tool.execute(inp, config=config)
    # Database
    if name == "db_query":
        return database.execute(inp, config=config)
    # Telegram file (fila de envio)
    if name == "send_telegram_file":
        return telegram_file.execute(inp, user_id=user_id, config=config)
    # Sub-agentes
    if name.startswith("agent_"):
        from tools import agent as agent_tool
        return agent_tool.execute_sync(name, inp, user_id=user_id, db=db, config=config)

    return f"Ferramenta desconhecida: {name}"


async def execute(name: str, inp: dict, *, user_id: int = 0, db, config: dict) -> str:
    """Dispatcher async — roda tools em thread separada para não bloquear o event loop."""
    try:
        return await asyncio.to_thread(
            _execute_sync, name, inp, user_id=user_id, db=db, config=config,
        )
    except Exception as e:
        logger.error(f"Erro em {name}: {e}", exc_info=True)
        return f"❌ ERRO em {name}: {type(e).__name__}: {e}"
