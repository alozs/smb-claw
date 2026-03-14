"""Testes de carregamento de configuração."""
import os
import tempfile
from pathlib import Path
import pytest


def load_env_file(path: Path, override: bool = False, env: dict = None) -> dict:
    """Simula _load_env_file retornando dict ao invés de setar os.environ."""
    result = dict(env) if env else {}
    if not path.exists():
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if override or key not in result:
                    result[key] = value
    return result


class TestConfigLoading:
    def test_load_basic_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY1=value1\nKEY2=value2\n")
        result = load_env_file(env_file)
        assert result == {"KEY1": "value1", "KEY2": "value2"}

    def test_skip_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nKEY=value\n")
        result = load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_skip_empty_lines(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("\n\nKEY=value\n\n")
        result = load_env_file(env_file)
        assert result == {"KEY": "value"}

    def test_setdefault_does_not_override(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=new_value\n")
        result = load_env_file(env_file, override=False, env={"KEY": "old_value"})
        assert result["KEY"] == "old_value"

    def test_override_replaces(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("KEY=new_value\n")
        result = load_env_file(env_file, override=True, env={"KEY": "old_value"})
        assert result["KEY"] == "new_value"

    def test_missing_file_returns_empty(self, tmp_path):
        result = load_env_file(tmp_path / "nonexistent")
        assert result == {}

    def test_value_with_equals(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("DB_URL=postgresql://user:pass@host:5432/db\n")
        result = load_env_file(env_file)
        assert result["DB_URL"] == "postgresql://user:pass@host:5432/db"

    def test_precedence_global_then_bot_then_secrets(self, tmp_path):
        """Simula a ordem: config.global (setdefault) → .env (override) → secrets.env (override)."""
        global_cfg = tmp_path / "config.global"
        global_cfg.write_text("MODEL=claude-sonnet-4-6\nADMIN_ID=123\n")

        bot_env = tmp_path / ".env"
        bot_env.write_text("MODEL=claude-opus-4-6\nBOT_NAME=test\n")

        secrets = tmp_path / "secrets.env"
        secrets.write_text("DB_URL=sqlite:///test.db\n")

        env = load_env_file(global_cfg, override=False)
        env = load_env_file(bot_env, override=True, env=env)
        env = load_env_file(secrets, override=True, env=env)

        assert env["MODEL"] == "claude-opus-4-6"  # bot .env overrides global
        assert env["ADMIN_ID"] == "123"  # from global
        assert env["BOT_NAME"] == "test"  # from bot .env
        assert env["DB_URL"] == "sqlite:///test.db"  # from secrets
