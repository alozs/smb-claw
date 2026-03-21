"""
Funções de segurança: shell denylist, path traversal, detecção de injection.
Módulo puro — sem dependências de config ou estado global.
"""

import re
from pathlib import Path


# ── Detecção de Prompt Injection por Scoring ──────────────────────────────

_INJECTION_PATTERNS: list[tuple[re.Pattern, float, str]] = [
    # Override de instruções (inglês)
    (re.compile(r'ignore\s+(previous|all)\s+(instructions?|rules?|guidelines?|constraints?)', re.I), 0.4, "override"),
    (re.compile(r'you\s+are\s+now\s+', re.I), 0.4, "override"),
    (re.compile(r'new\s+system\s+prompt', re.I), 0.4, "override"),
    (re.compile(r'\bdisregard\b.{0,30}\b(instructions?|rules?|guidelines?)', re.I), 0.4, "override"),
    (re.compile(r'forget\s+(all\s+)?(previous\s+)?instructions?', re.I), 0.4, "override"),
    # Override de instruções (português)
    (re.compile(r'esqueça\s+(as\s+|todas\s+as\s+)?(instru[cç]|regras|diretrizes)', re.I), 0.4, "override"),
    (re.compile(r'ignore\s+(as\s+|todas\s+as\s+)?(instru[cç]|regras)', re.I), 0.4, "override"),
    (re.compile(r'desconsider(e|a)\s+(as\s+)?(instru[cç]|regras)', re.I), 0.4, "override"),
    # Urgência + destruição (cenário sequestro)
    (re.compile(r'(sequestrado|kidnapped|sequestro).{0,60}(delet|apag|remov)', re.I | re.S), 0.5, "urgency_destruct"),
    (re.compile(r'(delet|apag|remov).{0,60}(sequestrado|kidnapped)', re.I | re.S), 0.5, "urgency_destruct"),
    (re.compile(r'emergency.{0,40}(delete|remove|wipe)', re.I), 0.5, "urgency_destruct"),
    (re.compile(r'(urgente|urgently).{0,40}(apag|delet|remov)', re.I), 0.5, "urgency_destruct"),
    (re.compile(r'(apag|delet|remov).{0,40}(urgente|urgently)', re.I), 0.5, "urgency_destruct"),
    (re.compile(r'vida.{0,40}depende.{0,40}(delet|apag|execut)', re.I | re.S), 0.4, "urgency_destruct"),
    # Exfiltração de prompt/instruções
    (re.compile(r'show\s+.{0,20}(system|original|initial).{0,20}prompt', re.I), 0.3, "exfiltration"),
    (re.compile(r'(print|display|output|repeat|reveal)\s+(your\s+)?instructions', re.I), 0.3, "exfiltration"),
    (re.compile(r'(mostre?|exib[ae]|repita?|revele?)\s+.{0,20}(prompt|instru[cç])', re.I), 0.3, "exfiltration"),
    (re.compile(r'what\s+(are|were)\s+your\s+(original\s+)?instructions', re.I), 0.3, "exfiltration"),
    # Role-play escalation para root/admin
    (re.compile(r'pretend\s+(you\s+)?(are|have)\s+(root|admin|superuser)', re.I), 0.3, "roleplay_escalation"),
    (re.compile(r'act\s+as\s+(root|system|admin|superuser)', re.I), 0.3, "roleplay_escalation"),
    (re.compile(r'finja\s+(que\s+)?(é|ser|você\s+é)\s+(root|admin|sistema)', re.I), 0.3, "roleplay_escalation"),
    # Fake tool results / role switching
    (re.compile(r'<tool_result>', re.I), 0.5, "fake_tool_result"),
    (re.compile(r'\[tool[\s_]result\]', re.I), 0.5, "fake_tool_result"),
    (re.compile(r'role:\s*(system|tool)\b', re.I), 0.3, "role_switch"),
    # Manipulação emocional com ameaça
    (re.compile(r'(vai\s+morrer|will\s+die|life\s+depends).{0,60}se\s+não', re.I | re.S), 0.3, "emotional_manip"),
    (re.compile(r'(morrerá|morrerão).{0,30}(se\s+não|unless)', re.I), 0.3, "emotional_manip"),
]


def detect_injection(text: str, threshold: float = 0.7) -> tuple[bool, str, float]:
    """Detecta tentativas de prompt injection por scoring.

    Returns: (flagged, reason, score)
    - flagged: True se score >= threshold
    - reason: descrição dos padrões encontrados
    - score: soma dos pesos dos padrões matched (pode > 1.0)
    """
    if not text or not text.strip():
        return False, "", 0.0

    total_score = 0.0
    matched_reasons: list[str] = []

    for pattern, weight, reason in _INJECTION_PATTERNS:
        if pattern.search(text):
            total_score += weight
            if reason not in matched_reasons:
                matched_reasons.append(reason)

    flagged = total_score >= threshold
    reason_str = ", ".join(matched_reasons) if matched_reasons else ""
    return flagged, reason_str, round(total_score, 3)


def check_shell_safety(command: str, protected_paths: list[str]) -> str | None:
    """Retorna mensagem de erro se comando for bloqueado, None se ok."""
    for protected in protected_paths:
        if protected in command:
            return f"Bloqueado: acesso a '{protected}' não permitido."
    blocked = [
        r"printenv", r"\benv\b", r"\$\{?ANTHROPIC", r"\$\{?TELEGRAM",
        r"\$\{?DB_URL", r"\$\{?GIT_TOKEN", r"\$\{?GITHUB_TOKEN",
        # Impede o bot de matar/reiniciar a si mesmo ou outros bots
        r"\bkill\b", r"\bpkill\b", r"\bkillall\b",
        r"\bbot\.py\b",
        r"\bsystemctl\b",
        r"\bservice\s+claude",
    ]
    for pat in blocked:
        if re.search(pat, command, re.IGNORECASE):
            return "Bloqueado por política de segurança."
    return None


def check_path_safety(path_raw: str, work_dir: Path) -> str | None:
    """Retorna mensagem de erro se caminho escapar do workspace, None se ok."""
    target = (work_dir / path_raw).resolve()
    if not str(target).startswith(str(work_dir.resolve())):
        return "Acesso fora do workspace negado"
    return None


def resolve_safe_path(path_raw: str, work_dir: Path) -> Path | None:
    """Resolve caminho dentro do workspace. Retorna None se inseguro."""
    target = (work_dir / path_raw).resolve()
    if not str(target).startswith(str(work_dir.resolve())):
        return None
    return target


def sanitize_output(text: str, secrets: list[str]) -> str:
    """Remove tokens/secrets de texto de saída."""
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text
