"""Ferramenta git: clone, pull, push, commit, etc."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from security import sanitize_output


def get_definitions(work_dir):
    return [{
        "name": "git_op",
        "description": f"Operações git no workspace {work_dir}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "action":   {"type": "string", "enum": ["clone", "pull", "status", "add", "commit", "push", "log", "diff"]},
                "repo_url": {"type": "string"},
                "path":     {"type": "string"},
                "message":  {"type": "string"},
                "files":    {"type": "string"},
            },
            "required": ["action"],
        },
    }]


def execute(inp: dict, *, config: dict) -> str:
    work_dir = config["WORK_DIR"]
    git_token = config.get("GIT_TOKEN", "")
    git_user = config.get("GIT_USER", "")
    git_email = config.get("GIT_EMAIL", "")
    secrets = [git_token, config.get("GITHUB_TOKEN", "")]
    append_daily_log = config["append_daily_log"]

    action = inp["action"]
    work_dir.mkdir(parents=True, exist_ok=True)

    def _inject(url):
        if git_token and ("github.com" in url or "gitlab.com" in url):
            p = urlparse(url)
            return p._replace(netloc=f"{git_user or 'oauth2'}:{git_token}@{p.hostname}").geturl()
        return url

    env = os.environ.copy()
    if git_user:
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = git_user
    if git_email:
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = git_email
    env["GIT_TERMINAL_PROMPT"] = "0"

    def _git(*a, cwd=work_dir):
        r = subprocess.run(
            ["git"] + list(a), capture_output=True, text=True,
            cwd=cwd, env=env, timeout=60,
        )
        out = (r.stdout + r.stderr).strip()
        return sanitize_output(out, secrets) or "(sem saída)"

    if action == "clone":
        repo_url = inp.get("repo_url", "")
        if not repo_url:
            return "Erro: repo_url obrigatório"
        dest = inp.get("path", urlparse(repo_url).path.split("/")[-1].replace(".git", ""))
        dest_path = work_dir / dest
        if dest_path.exists():
            return f"'{dest}' já existe"
        result = _git("clone", _inject(repo_url), str(dest_path), cwd=work_dir)
        append_daily_log(f"Git clone: {repo_url} → {dest}")
        return result

    repo_path = inp.get("path", "")
    if repo_path:
        cwd = (work_dir / repo_path).resolve()
        if not str(cwd).startswith(str(work_dir.resolve())):
            return "Fora do workspace"
    else:
        repos = [p for p in work_dir.iterdir() if (p / ".git").exists()]
        if not repos:
            return "Nenhum repo encontrado. Use clone primeiro."
        cwd = repos[0]

    if action == "pull":
        return _git("pull", cwd=cwd)
    if action == "status":
        return _git("status", cwd=cwd)
    if action == "log":
        return _git("log", "--oneline", "-10", cwd=cwd)
    if action == "diff":
        return _git("diff", cwd=cwd)
    if action == "add":
        return _git("add", inp.get("files", "."), cwd=cwd)
    if action == "commit":
        msg = inp.get("message", "")
        if not msg:
            return "Erro: message obrigatório"
        result = _git("commit", "-m", msg, cwd=cwd)
        append_daily_log(f"Git commit: {msg}")
        return result
    if action == "push":
        remote_r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=cwd,
        )
        remote_url = remote_r.stdout.strip()
        if remote_url:
            subprocess.run(
                ["git", "remote", "set-url", "origin", _inject(remote_url)],
                capture_output=True, cwd=cwd, env=env,
            )
        result = _git("push", cwd=cwd)
        if remote_url:
            subprocess.run(
                ["git", "remote", "set-url", "origin", remote_url],
                capture_output=True, cwd=cwd, env=env,
            )
        return result

    return f"Ação git desconhecida: {action}"
