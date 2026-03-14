"""
Sub-agent tool — permite que bots pai deleguem tarefas a sub-agentes especializados.

Descoberta automática: escaneia BASE_DIR/subagents/ e gera tool definitions Anthropic.
Anti-recursão: sub-agentes não recebem ferramentas agent_* (build_definitions sem base_dir).
Isolamento de credenciais: .env do sub-agente parseado para dict, nunca em os.environ.

Modos de execução (campo MODE no .env do sub-agente):
  simple  — uma única chamada LLM, sem tools, sem loop. Rápido, ideal para 95% dos casos.
  agentic — loop com tool use (até max_iterations). Mais lento, para tarefas complexas.
"""

import asyncio
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("tools.agent")

_SUBAGENT_TIMEOUT = 90  # segundos — timeout global por execução de sub-agente

# Tools que são "sempre disponíveis" no bot pai mas não fazem sentido para sub-agentes
# (precisam de db=None → falhariam silenciosamente)
_SUPPRESS_FOR_SUBAGENTS = {"task_create", "task_update", "task_list", "schedule", "state_rw"}


def _parse_env_file(path: Path) -> dict:
    """Parseia arquivo .env para dict sem modificar os.environ."""
    config = {}
    if not path.exists():
        return config
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                config[key.strip()] = value.strip()
    return config


def _dir_to_tool_name(dir_name: str) -> str:
    """'image-creator' → 'agent_image_creator'"""
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", dir_name)
    return f"agent_{safe}"


def _load_subagent_config(subagent_dir: Path) -> dict | None:
    """Carrega e valida configuração de um sub-agente. Retorna None se inválido."""
    env_path = subagent_dir / ".env"
    if not env_path.exists():
        logger.warning(f"Sub-agente {subagent_dir.name}: .env não encontrado, ignorando")
        return None

    config = _parse_env_file(env_path)

    required = ["DESCRIPTION", "PROVIDER", "MODEL"]
    missing = [k for k in required if not config.get(k)]
    if missing:
        logger.warning(f"Sub-agente {subagent_dir.name}: campos faltando {missing}, ignorando")
        return None

    config["_dir"] = subagent_dir
    config["_name"] = subagent_dir.name
    config.setdefault("ALLOWED_PARENTS", "*")
    config.setdefault("TOOLS", "none")
    config.setdefault("MODE", "simple")  # padrão: modo simples
    return config


def build_definitions(base_dir: Path, bot_name: str) -> list[dict]:
    """
    Escaneia BASE_DIR/subagents/ e retorna tool definitions para sub-agentes permitidos.
    """
    subagents_dir = base_dir / "subagents"
    if not subagents_dir.exists():
        return []

    defs = []
    for entry in sorted(subagents_dir.iterdir()):
        if not entry.is_dir():
            continue

        config = _load_subagent_config(entry)
        if config is None:
            continue

        allowed = config["ALLOWED_PARENTS"].strip()
        if allowed != "*":
            allowed_list = {p.strip() for p in allowed.split(",")}
            if bot_name not in allowed_list:
                continue

        tool_name = _dir_to_tool_name(entry.name)
        mode_label = " [agentic]" if config["MODE"].lower() == "agentic" else ""
        defs.append({
            "name": tool_name,
            "description": config["DESCRIPTION"] + mode_label,
            "input_schema": {
                "type": "object",
                "properties": {
                    "prompt":  {"type": "string", "description": "Tarefa para o sub-agente"},
                    "context": {"type": "string", "description": "Contexto adicional (opcional)"},
                },
                "required": ["prompt"],
            },
        })
        logger.info(f"Sub-agente registrado: {tool_name} (mode={config['MODE']})")

    return defs


def execute_sync(name: str, inp: dict, *, user_id: int, db, config: dict) -> str:
    """
    Ponto de entrada síncrono. Chamado via asyncio.to_thread() pelo dispatcher principal.
    Cria novo event loop neste thread do pool via asyncio.run().
    """
    dir_name_underscored = name[len("agent_"):]

    base_dir = config.get("BASE_DIR")
    if base_dir is None:
        return f"[Sub-agent '{name}'] Erro: BASE_DIR não configurado"

    subagents_dir = Path(base_dir) / "subagents"
    subagent_dir = None
    if subagents_dir.exists():
        for entry in subagents_dir.iterdir():
            if entry.is_dir():
                normalized = re.sub(r"[^a-zA-Z0-9_]", "_", entry.name)
                if normalized == dir_name_underscored:
                    subagent_dir = entry
                    break

    if subagent_dir is None:
        return f"[Sub-agent '{name}'] Erro: diretório não encontrado em subagents/"

    subagent_config = _load_subagent_config(subagent_dir)
    if subagent_config is None:
        return f"[Sub-agent '{name}'] Erro: configuração inválida"

    bot_name = config.get("BOT_NAME", "")
    allowed = subagent_config["ALLOWED_PARENTS"].strip()
    if allowed != "*":
        if bot_name not in {p.strip() for p in allowed.split(",")}:
            return f"[Sub-agent '{name}'] Erro: bot '{bot_name}' não tem permissão"

    prompt = inp.get("prompt", "")
    context = inp.get("context", "")
    mode = subagent_config.get("MODE", "simple").lower()

    logger.info(f"[tool] {name} — modo={mode}")

    try:
        coro = asyncio.wait_for(
            _run_subagent_async(
                subagent_config=subagent_config,
                prompt=prompt,
                context=context,
                parent_config=config,
            ),
            timeout=_SUBAGENT_TIMEOUT,
        )
        return asyncio.run(coro)
    except asyncio.TimeoutError:
        logger.warning(f"Sub-agente '{name}' excedeu timeout de {_SUBAGENT_TIMEOUT}s")
        return f"[Sub-agent '{name}'] Timeout: execução excedeu {_SUBAGENT_TIMEOUT}s"
    except Exception as e:
        logger.error(f"Sub-agente '{name}' falhou: {e}", exc_info=True)
        return f"[Sub-agent '{name}' failed: {type(e).__name__}: {e}]"


async def _run_subagent_async(
    *,
    subagent_config: dict,
    prompt: str,
    context: str,
    parent_config: dict,
) -> str:
    """
    Executa o sub-agente no novo event loop.
    Modo 'simple': uma chamada LLM, sem tools, resposta direta.
    Modo 'agentic': loop completo com tool use (até 10 iterações).
    """
    subagent_dir: Path = subagent_config["_dir"]
    provider = subagent_config["PROVIDER"].lower()
    model = subagent_config["MODEL"]
    mode = subagent_config.get("MODE", "simple").lower()

    soul_path = subagent_dir / "soul.md"
    system_prompt = (
        soul_path.read_text(encoding="utf-8").strip()
        if soul_path.exists()
        else "Você é um assistente especializado."
    )

    user_content = prompt
    if context:
        user_content = f"{prompt}\n\nContexto adicional:\n{context}"

    anthropic_key = subagent_config.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
    openrouter_key = subagent_config.get("OPENROUTER_API_KEY") or os.environ.get("OPENROUTER_API_KEY", "")

    # Modo simple: sem tools, uma chamada
    if mode == "simple":
        if provider == "anthropic":
            return await _simple_anthropic(anthropic_key, model, system_prompt, user_content)
        elif provider == "openrouter":
            return await _simple_openrouter(openrouter_key, model, system_prompt, user_content)
        elif provider == "claude-cli":
            return await _loop_cli(model=model, system=system_prompt, prompt=prompt, context=context)
        else:
            return f"[Sub-agente] Provedor desconhecido: {provider}"

    # Modo agentic: com tools e loop
    import tools as tool_registry

    tools_raw = subagent_config.get("TOOLS", "none").lower()
    enabled_tools = set() if tools_raw == "none" else {t.strip() for t in tools_raw.split(",")}
    work_dir = parent_config.get("WORK_DIR", Path("."))

    # Suprimir tools que precisam de db (falhariam silenciosamente no sub-agente)
    all_defs = tool_registry.build_definitions(enabled_tools, work_dir)
    tool_defs = [d for d in all_defs if d["name"] not in _SUPPRESS_FOR_SUBAGENTS]

    if provider == "anthropic":
        return await _loop_anthropic(
            api_key=anthropic_key, model=model, system=system_prompt,
            user_content=user_content, tool_defs=tool_defs, parent_config=parent_config,
        )
    elif provider == "openrouter":
        return await _loop_openrouter(
            api_key=openrouter_key, model=model, system=system_prompt,
            user_content=user_content, tool_defs=tool_defs, parent_config=parent_config,
        )
    elif provider == "claude-cli":
        return await _loop_cli(model=model, system=system_prompt, prompt=prompt, context=context)
    else:
        return f"[Sub-agente] Provedor desconhecido: {provider}"


# ── Modo simple ───────────────────────────────────────────────────────────────

async def _simple_anthropic(api_key: str, model: str, system: str, user_content: str) -> str:
    """Uma única chamada Anthropic — sem tools, sem loop. Rápido."""
    import anthropic as anthropic_sdk

    if not api_key:
        creds_path = Path.home() / ".claude" / ".credentials.json"
        if creds_path.exists():
            try:
                creds = json.loads(creds_path.read_text(encoding="utf-8"))
                api_key = creds.get("claudeAiOauth", {}).get("accessToken", "")
            except Exception:
                pass

    if not api_key:
        return "[Sub-agente Anthropic] Erro: ANTHROPIC_API_KEY não configurada"

    client = anthropic_sdk.AsyncAnthropic(api_key=api_key, max_retries=2)
    response = await client.messages.create(
        model=model, max_tokens=4096, system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    text = " ".join(b.text for b in response.content if b.type == "text")
    return text or "[Sub-agente sem resposta]"


async def _simple_openrouter(api_key: str, model: str, system: str, user_content: str) -> str:
    """Uma única chamada OpenRouter — sem tools, sem loop. Rápido."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return "[Sub-agente OpenRouter] Erro: openai package não instalado"

    if not api_key:
        return "[Sub-agente OpenRouter] Erro: OPENROUTER_API_KEY não configurada"

    client = AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    response = await client.chat.completions.create(
        model=model, max_tokens=4096,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user_content}],
    )
    return response.choices[0].message.content or "[Sub-agente sem resposta]"


# ── Modo agentic ──────────────────────────────────────────────────────────────

async def _loop_anthropic(
    *, api_key: str, model: str, system: str, user_content: str,
    tool_defs: list, parent_config: dict, max_iterations: int = 10,
) -> str:
    import anthropic as anthropic_sdk

    if not api_key:
        creds_path = Path.home() / ".claude" / ".credentials.json"
        if creds_path.exists():
            try:
                creds = json.loads(creds_path.read_text(encoding="utf-8"))
                api_key = creds.get("claudeAiOauth", {}).get("accessToken", "")
            except Exception:
                pass

    if not api_key:
        return "[Sub-agente Anthropic] Erro: ANTHROPIC_API_KEY não configurada"

    client = anthropic_sdk.AsyncAnthropic(api_key=api_key, max_retries=2)
    messages: list[dict] = [{"role": "user", "content": user_content}]
    kwargs: dict = {"model": model, "max_tokens": 4096, "system": system, "messages": messages}
    if tool_defs:
        kwargs["tools"] = tool_defs

    text_parts: list[str] = []
    for _ in range(max_iterations):
        response = await client.messages.create(**kwargs)
        text_parts = [b.text for b in response.content if b.type == "text"]
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason == "end_turn" or not tool_uses:
            break

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for tu in tool_uses:
            result = await asyncio.to_thread(
                _execute_tool_sync, tu.name, tu.input, parent_config=parent_config
            )
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": str(result)})
        messages.append({"role": "user", "content": results})
        kwargs["messages"] = messages

    return "\n".join(text_parts) or "[Sub-agente sem resposta]"


async def _loop_openrouter(
    *, api_key: str, model: str, system: str, user_content: str,
    tool_defs: list, parent_config: dict, max_iterations: int = 10,
) -> str:
    try:
        from openai import AsyncOpenAI
    except ImportError:
        return "[Sub-agente OpenRouter] Erro: openai package não instalado"

    if not api_key:
        return "[Sub-agente OpenRouter] Erro: OPENROUTER_API_KEY não configurada"

    client = AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    openai_tools = [
        {"type": "function", "function": {
            "name": td["name"], "description": td.get("description", ""),
            "parameters": td.get("input_schema", {"type": "object", "properties": {}}),
        }}
        for td in tool_defs
    ] if tool_defs else []

    kwargs: dict = {"model": model, "max_tokens": 4096, "messages": messages}
    if openai_tools:
        kwargs["tools"] = openai_tools

    text_parts: list[str] = []
    for _ in range(max_iterations):
        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message
        if msg.content:
            text_parts = [msg.content]

        if choice.finish_reason != "tool_calls" or not msg.tool_calls:
            break

        messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]})
        for tc in msg.tool_calls:
            try:
                tool_input = json.loads(tc.function.arguments)
            except Exception:
                tool_input = {}
            result = await asyncio.to_thread(
                _execute_tool_sync, tc.function.name, tool_input, parent_config=parent_config
            )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
        kwargs["messages"] = messages

    return "\n".join(text_parts) or "[Sub-agente sem resposta]"


async def _loop_cli(*, model: str, system: str, prompt: str, context: str) -> str:
    """Sub-agente via claude-cli subprocess. Stateless, sem tools."""
    full_prompt = prompt
    if context:
        full_prompt = f"{prompt}\n\nContexto adicional:\n{context}"

    cmd = ["claude", "-p", full_prompt, "--system-prompt", system]
    if model:
        cmd += ["--model", model]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return f"[Sub-agente CLI erro]: {stderr.decode('utf-8', errors='replace')[:500]}"
        return stdout.decode("utf-8", errors="replace").strip()
    except asyncio.TimeoutError:
        return "[Sub-agente CLI] Timeout"
    except Exception as e:
        return f"[Sub-agente CLI falhou: {e}]"


def _execute_tool_sync(name: str, inp: dict, *, parent_config: dict) -> str:
    """Executa tool do sub-agente (modo agentic). Roda em thread pool."""
    try:
        import tools as tool_registry
        result = tool_registry._execute_sync(name, inp, user_id=0, db=None, config=parent_config)
        return str(result) if result is not None else "[tool sem resultado]"
    except Exception as e:
        logger.error(f"Tool {name} no sub-agente falhou: {e}", exc_info=True)
        return f"[Tool {name} falhou: {type(e).__name__}: {e}]"
