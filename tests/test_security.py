"""Testes de segurança: shell denylist, path traversal, SQL safety."""
import re
import pytest
from tools.http import _resolve_secret_headers


# ── Shell safety ─────────────────────────────────────────────────────────────

# Reproduz a lógica de _check_shell_safety sem importar bot.py (evita dependências)
PROTECTED_PATHS_EXAMPLE = [
    "/home/ubuntu/claude-bots/config.global",
    "/home/ubuntu/claude-bots/bots/test/.env",
    "/home/ubuntu/claude-bots/bots/test/secrets.env",
    "/home/ubuntu/.claude/.credentials.json",
    "/etc/passwd", "/etc/shadow", "/root", "/home/ubuntu/.ssh",
]

BLOCKED_PATTERNS = [
    r"printenv", r"\benv\b", r"\$\{?ANTHROPIC", r"\$\{?TELEGRAM",
    r"\$\{?DB_URL", r"\$\{?GIT_TOKEN",
]


def check_shell_safety(command: str, protected_paths=None) -> str | None:
    for protected in (protected_paths or PROTECTED_PATHS_EXAMPLE):
        if protected in command:
            return f"Bloqueado: acesso a '{protected}' não permitido."
    for pat in BLOCKED_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            return "Bloqueado por política de segurança."
    return None


class TestShellSafety:
    def test_blocks_config_global(self):
        assert check_shell_safety("cat /home/ubuntu/claude-bots/config.global") is not None

    def test_blocks_env_file(self):
        assert check_shell_safety("cat /home/ubuntu/claude-bots/bots/test/.env") is not None

    def test_blocks_secrets_env(self):
        assert check_shell_safety("cat /home/ubuntu/claude-bots/bots/test/secrets.env") is not None

    def test_blocks_credentials(self):
        assert check_shell_safety("cat /home/ubuntu/.claude/.credentials.json") is not None

    def test_blocks_etc_passwd(self):
        assert check_shell_safety("cat /etc/passwd") is not None

    def test_blocks_etc_shadow(self):
        assert check_shell_safety("cat /etc/shadow") is not None

    def test_blocks_ssh(self):
        assert check_shell_safety("cat /home/ubuntu/.ssh/id_rsa") is not None

    def test_blocks_printenv(self):
        assert check_shell_safety("printenv") is not None

    def test_blocks_env_command(self):
        assert check_shell_safety("env | grep KEY") is not None

    def test_blocks_anthropic_var(self):
        assert check_shell_safety("echo $ANTHROPIC_API_KEY") is not None

    def test_blocks_telegram_var(self):
        assert check_shell_safety("echo $TELEGRAM_TOKEN") is not None

    def test_blocks_db_url_var(self):
        assert check_shell_safety("echo $DB_URL") is not None

    def test_blocks_git_token_var(self):
        assert check_shell_safety("echo $GIT_TOKEN") is not None

    def test_allows_normal_commands(self):
        assert check_shell_safety("ls -la /tmp") is None
        assert check_shell_safety("whoami") is None
        assert check_shell_safety("python3 --version") is None
        assert check_shell_safety("git status") is None
        assert check_shell_safety("df -h") is None


class TestHttpSecretHeaders:
    def test_resolves_openrouter_key_placeholder(self):
        headers = _resolve_secret_headers(
            {"Authorization": "Bearer $OPENROUTER_API_KEY", "Content-Type": "application/json"},
            {"OPENROUTER_API_KEY": "secret-token"},
        )
        assert headers["Authorization"] == "Bearer secret-token"
        assert headers["Content-Type"] == "application/json"

    def test_resolves_braced_openrouter_key_placeholder(self):
        headers = _resolve_secret_headers(
            {"Authorization": "Bearer ${OPENROUTER_API_KEY}"},
            {"OPENROUTER_API_KEY": "secret-token"},
        )
        assert headers["Authorization"] == "Bearer secret-token"

    def test_resolves_known_and_preserves_unknown_placeholders(self):
        headers = _resolve_secret_headers(
            {
                "Authorization": "Bearer $API_KEY_1",
                "X-Trace": "$UNKNOWN_PLACEHOLDER",
            },
            {"API_KEY_1": "another-secret"},
        )
        assert headers["Authorization"] == "Bearer another-secret"
        assert headers["X-Trace"] == "$UNKNOWN_PLACEHOLDER"


# ── Path traversal ───────────────────────────────────────────────────────────

from pathlib import Path

WORK_DIR = Path("/home/ubuntu/claude-bots/bots/test/workspace")


def check_path_safety(path_raw: str) -> bool:
    """Returns True if path is safe (inside workspace)."""
    target = (WORK_DIR / path_raw).resolve()
    return str(target).startswith(str(WORK_DIR.resolve()))


class TestPathTraversal:
    def test_normal_file(self):
        assert check_path_safety("file.txt") is True

    def test_subdirectory(self):
        assert check_path_safety("subdir/file.txt") is True

    def test_parent_traversal(self):
        assert check_path_safety("../../../etc/passwd") is False

    def test_double_dot(self):
        assert check_path_safety("..") is False

    def test_absolute_path_outside(self):
        assert check_path_safety("/etc/passwd") is False

    def test_dot_current(self):
        assert check_path_safety("./file.txt") is True


# ── SQL safety ───────────────────────────────────────────────────────────────

SQL_BLOCKED = re.compile(
    r"^\s*(DROP|TRUNCATE|DELETE\s+FROM\s+\w+\s*;|ALTER\s+TABLE)", re.IGNORECASE
)


class TestSQLSafety:
    def test_blocks_drop(self):
        assert SQL_BLOCKED.match("DROP TABLE users") is not None

    def test_blocks_truncate(self):
        assert SQL_BLOCKED.match("TRUNCATE TABLE users") is not None

    def test_blocks_delete_without_where(self):
        assert SQL_BLOCKED.match("DELETE FROM users;") is not None

    def test_blocks_alter_table(self):
        assert SQL_BLOCKED.match("ALTER TABLE users DROP COLUMN name") is not None

    def test_allows_select(self):
        assert SQL_BLOCKED.match("SELECT * FROM users") is None

    def test_allows_insert(self):
        assert SQL_BLOCKED.match("INSERT INTO users (name) VALUES ('test')") is None

    def test_allows_update(self):
        assert SQL_BLOCKED.match("UPDATE users SET name='test' WHERE id=1") is None
