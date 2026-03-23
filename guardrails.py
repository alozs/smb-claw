"""
Guardrails — classificação de risco de ações + bloqueio + formatação de alertas.
Módulo puro — sem dependências de config ou estado global.

Níveis de risco: safe < moderate < dangerous
Modos de operação: notify (alerta) | confirm (bloqueia sem aprovação) | block (bloqueia sempre)
"""

import re


# ── Padrões de campos sensíveis em queries ────────────────────────────────────
_SENSITIVE_FIELDS = re.compile(
    r'\b(password|passwd|senha|token|secret|api_key|apikey|credential|'
    r'private_key|auth_token|access_token|refresh_token|hash|salt|ssn|cpf)\b',
    re.IGNORECASE,
)

# ── Padrões de comandos shell que tentam exfiltrar credenciais ─────────────────
_SHELL_EXFIL = re.compile(
    r'(cat|less|head|tail|more|print|echo|curl|wget)\s+.*'
    r'(\.env|secrets\.env|credentials\.json|auth\.json|id_rsa|\.pem|\.key)',
    re.IGNORECASE,
)

# ── Padrões destrutivos em shell ──────────────────────────────────────────────
_SHELL_DESTRUCTIVE = re.compile(
    r'\brm\s+-[rRf]|\brm\b.*\*|'
    r'\bmv\b\s+.*\s+/|'
    r'\bchmod\b|\bchown\b|\bdd\b|'
    r'curl\s+.*\|\s*bash|wget\s+.*\|\s*bash|'
    r'>\s*/\w|truncate\s|\bshred\b|'
    r'\bdropdb\b|\bdroptable\b',
    re.IGNORECASE,
)


# ── Classificação de risco ────────────────────────────────────────────────────

def classify_action(tool_name: str, tool_input: dict) -> str:
    """Classifica o risco de uma ação de ferramenta.

    Returns: 'safe', 'moderate', ou 'dangerous'
    """
    # Sempre safe
    if tool_name in ("memory_write", "memory_read", "state_rw",
                     "task_create", "task_update", "task_list",
                     "send_telegram_file", "request_approval"):
        return "safe"

    if tool_name == "schedule":
        action = str(tool_input.get("action", "")).lower()
        return "safe" if action == "list" else "moderate"

    if tool_name == "http_request":
        method = str(tool_input.get("method", "GET")).upper()
        if method == "GET":
            return "safe"
        if method == "DELETE":
            return "dangerous"
        return "moderate"  # POST/PUT/PATCH

    if tool_name == "manage_files":
        op = str(tool_input.get("operation", "")).lower()
        return "safe" if op in ("read", "list") else "dangerous"

    if tool_name == "git_op":
        op = str(tool_input.get("operation", "")).lower()
        if op in ("status", "log", "diff", "fetch"):
            return "safe"
        if "push" in op or "force" in op or "reset" in op:
            return "dangerous"
        return "moderate"  # commit, pull, clone, etc.

    if tool_name == "db_query":
        query = str(tool_input.get("query", "")).strip()
        upper = query.upper()
        if upper.startswith("SELECT"):
            # SELECT em campos sensíveis → dangerous
            if _SENSITIVE_FIELDS.search(query):
                return "dangerous"
            return "safe"
        return "dangerous"  # INSERT/UPDATE/DELETE/DROP

    if tool_name == "run_shell":
        cmd = str(tool_input.get("command", ""))
        # Tentativa de exfiltração de credenciais → dangerous
        if _SHELL_EXFIL.search(cmd):
            return "dangerous"
        if _SHELL_DESTRUCTIVE.search(cmd):
            return "dangerous"
        return "moderate"

    if tool_name == "manage_cron":
        return "dangerous"

    if tool_name == "github":
        method = str(tool_input.get("method", "GET")).upper()
        if method == "GET":
            return "safe"
        if method == "DELETE":
            return "dangerous"
        return "moderate"

    if tool_name == "notion":
        action = str(tool_input.get("action", ""))
        if action in ("search", "get_page", "get_database", "query_database", "get_blocks"):
            return "safe"
        if action == "delete_block":
            return "dangerous"
        return "moderate"  # create_page, update_page, append_blocks

    if tool_name.startswith("agent_"):
        return "dangerous"

    # Default: moderate para ferramentas desconhecidas
    return "moderate"


_LEVELS = {"safe": 0, "moderate": 1, "dangerous": 2}


def should_notify(classification: str, min_level: str) -> bool:
    """Retorna True se o nível de classificação >= min_level."""
    return _LEVELS.get(classification, 0) >= _LEVELS.get(min_level, 2)


def should_block(classification: str, mode: str, is_approved: bool) -> bool:
    """Decide se a ação deve ser bloqueada.

    - mode='notify': nunca bloqueia (só alerta)
    - mode='confirm': bloqueia dangerous sem aprovação prévia
    - mode='block': bloqueia sempre ações dangerous
    """
    if classification != "dangerous":
        return False
    if mode == "block":
        return True
    if mode == "confirm" and not is_approved:
        return True
    return False


def format_alert(user_id: int, user_name: str, tool_name: str,
                 tool_input: dict, classification: str,
                 blocked: bool = False) -> str:
    """Formata alerta de guardrail para envio ao admin."""
    level_emoji = {"safe": "✅", "moderate": "⚠️", "dangerous": "🚨"}.get(classification, "❓")
    block_tag = " \\[BLOQUEADA\\]" if blocked else ""
    input_preview = str(tool_input)[:300]
    return (
        f"{level_emoji} *Guardrail \\[{classification.upper()}\\]{block_tag}*\n\n"
        f"👤 User: `{user_id}` \\({user_name}\\)\n"
        f"🔧 Tool: `{tool_name}`\n"
        f"📥 Input: `{input_preview}`"
    )


def format_block_result(tool_name: str, mode: str) -> str:
    """Retorna a mensagem de bloqueio que o LLM recebe como resultado da ferramenta."""
    if mode == "confirm":
        return (
            f"🚫 Ação `{tool_name}` bloqueada: aprovação do usuário não foi concedida. "
            f"Use `request_approval` antes de executar ações destrutivas ou sensíveis, "
            f"aguarde confirmação do usuário e tente novamente."
        )
    # mode == "block"
    return (
        f"🚫 Ação `{tool_name}` bloqueada: o administrador configurou este bot em modo de "
        f"bloqueio (GUARDRAILS_MODE=block). Ações perigosas não são permitidas. "
        f"Informe o usuário que não é possível executar esta ação."
    )


# ── Tool definition para request_approval ────────────────────────────────────

REQUEST_APPROVAL_DEFINITION = {
    "name": "request_approval",
    "description": (
        "Pede aprovação ao usuário antes de executar ação sensível. "
        "Use ANTES de ações que modifiquem dados, deletem arquivos, "
        "façam push ou enviem informações externas. "
        "Após o usuário confirmar com 'sim', a ação estará liberada."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Descrição clara da ação que será executada",
            },
            "risk": {
                "type": "string",
                "enum": ["moderate", "dangerous"],
                "description": "Nível de risco da ação",
            },
        },
        "required": ["action"],
    },
}


def execute_request_approval(inp: dict) -> str:
    """Execução do tool request_approval — retorna mensagem para o usuário confirmar."""
    action = inp.get("action", "ação não especificada")
    risk = inp.get("risk", "moderate")
    emoji = "🚨" if risk == "dangerous" else "⚠️"
    return (
        f"{emoji} **Aprovação necessária**\n\n"
        f"Estou prestes a executar: {action}\n\n"
        f"Confirme com **sim** para prosseguir ou **não** para cancelar."
    )
