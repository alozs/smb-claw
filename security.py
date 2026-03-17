"""
Funções de segurança: shell denylist, path traversal.
Módulo puro — sem dependências de config ou estado global.
"""

import re
from pathlib import Path


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
