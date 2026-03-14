"""Ferramenta GitHub: integração com a API REST v3."""

import json
import urllib.request
import urllib.error
import logging

from security import sanitize_output

logger = logging.getLogger(__name__)

DEFINITIONS = [{
    "name": "github",
    "description": (
        "Interage com a API do GitHub. Permite listar/criar/revisar PRs, "
        "listar/criar issues, verificar CI checks, e mais."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_prs", "get_pr", "create_pr", "merge_pr",
                    "pr_comments", "review_pr", "list_issues",
                    "create_issue", "check_runs",
                ],
            },
            "owner":  {"type": "string", "description": "Dono do repo (user ou org)"},
            "repo":   {"type": "string", "description": "Nome do repositório"},
            "number": {"type": "integer", "description": "Número do PR ou issue"},
            "title":  {"type": "string"},
            "body":   {"type": "string"},
            "head":   {"type": "string", "description": "Branch de origem (create_pr)"},
            "base":   {"type": "string", "description": "Branch de destino (default: main)"},
            "state":  {"type": "string", "enum": ["open", "closed", "all"]},
            "event":  {"type": "string", "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"]},
            "page":   {"type": "integer", "description": "Página para paginação (default: 1)"},
        },
        "required": ["action", "owner", "repo"],
    },
}]


def _gh_request(url: str, token: str, bot_name: str,
                method: str = "GET", body_data: dict = None) -> dict | list:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"claude-bot/{bot_name}",
    }
    data = json.dumps(body_data).encode() if body_data else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if body_data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            content = resp.read().decode(errors="replace")
            return json.loads(content) if content.strip() else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:500]
        return {"error": f"HTTP {e.code}: {body}"}
    except Exception as e:
        return {"error": str(e)}


def execute(inp: dict, *, config: dict) -> str:
    token = config.get("GITHUB_TOKEN", "")
    if not token:
        return "Erro: GITHUB_TOKEN não configurado em secrets.env"

    bot_name = config["BOT_NAME"]
    secrets = [token, config.get("GIT_TOKEN", "")]
    append_daily_log = config["append_daily_log"]

    action = inp["action"]
    owner = inp["owner"]
    repo = inp["repo"]
    base_url = f"https://api.github.com/repos/{owner}/{repo}"

    def _req(url, method="GET", body=None):
        result = _gh_request(url, token, bot_name, method, body)
        # Sanitizar token de qualquer output
        if isinstance(result, dict) and "error" in result:
            result["error"] = sanitize_output(result["error"], secrets)
        return result

    if action == "list_prs":
        state = inp.get("state", "open")
        page = inp.get("page", 1)
        data = _req(f"{base_url}/pulls?state={state}&per_page=30&page={page}")
        if isinstance(data, list):
            lines = []
            for pr in data:
                lines.append(f"#{pr['number']} [{pr['state']}] {pr['title']} ← {pr['head']['ref']}")
            if len(data) == 30:
                lines.append(f"\n(página {page} — use page={page+1} para mais)")
            return "\n".join(lines) or "(nenhum PR)"
        return json.dumps(data, ensure_ascii=False)

    if action == "get_pr":
        num = inp.get("number", 0)
        if not num:
            return "Erro: number obrigatório"
        data = _req(f"{base_url}/pulls/{num}")
        if "error" in data:
            return json.dumps(data)
        return (
            f"PR #{data['number']}: {data['title']}\n"
            f"Estado: {data['state']} | Mergeable: {data.get('mergeable', '?')}\n"
            f"Branch: {data['head']['ref']} → {data['base']['ref']}\n"
            f"Autor: {data['user']['login']}\n"
            f"Criado: {data['created_at']}\n"
            f"Alterações: +{data.get('additions', 0)} -{data.get('deletions', 0)} "
            f"({data.get('changed_files', 0)} arquivos)\n\n"
            f"{data.get('body', '')[:2000]}"
        )

    if action == "create_pr":
        title = inp.get("title", "")
        head = inp.get("head", "")
        base = inp.get("base", "main")
        body = inp.get("body", "")
        if not title or not head:
            return "Erro: title e head obrigatórios"
        data = _req(f"{base_url}/pulls", "POST",
                     {"title": title, "head": head, "base": base, "body": body})
        if "error" in data:
            return json.dumps(data)
        append_daily_log(f"PR criado: #{data.get('number')} {title}")
        return f"✅ PR #{data['number']} criado: {data.get('html_url', '')}"

    if action == "merge_pr":
        num = inp.get("number", 0)
        if not num:
            return "Erro: number obrigatório"
        data = _req(f"{base_url}/pulls/{num}/merge", "PUT", {"merge_method": "squash"})
        if "error" in data:
            return json.dumps(data)
        append_daily_log(f"PR #{num} merged")
        return f"✅ PR #{num} merged: {data.get('message', '')}"

    if action == "pr_comments":
        num = inp.get("number", 0)
        if not num:
            return "Erro: number obrigatório"
        page = inp.get("page", 1)
        data = _req(f"{base_url}/issues/{num}/comments?per_page=30&page={page}")
        if isinstance(data, list):
            lines = []
            for c in data:
                lines.append(f"@{c['user']['login']} ({c['created_at'][:10]}):\n{c['body'][:500]}")
            return "\n---\n".join(lines) or "(sem comentários)"
        return json.dumps(data, ensure_ascii=False)

    if action == "review_pr":
        num = inp.get("number", 0)
        event = inp.get("event", "COMMENT")
        body = inp.get("body", "")
        if not num:
            return "Erro: number obrigatório"
        data = _req(f"{base_url}/pulls/{num}/reviews", "POST",
                     {"event": event, "body": body})
        if "error" in data:
            return json.dumps(data)
        append_daily_log(f"PR #{num} review: {event}")
        return f"✅ Review enviado: {event}"

    if action == "list_issues":
        state = inp.get("state", "open")
        page = inp.get("page", 1)
        data = _req(f"{base_url}/issues?state={state}&per_page=30&page={page}")
        if isinstance(data, list):
            lines = []
            for issue in data:
                if "pull_request" in issue:
                    continue
                labels = ", ".join(l["name"] for l in issue.get("labels", []))
                lines.append(f"#{issue['number']} {issue['title']}" +
                             (f" [{labels}]" if labels else ""))
            if len(data) == 30:
                lines.append(f"\n(página {page} — use page={page+1} para mais)")
            return "\n".join(lines) or "(nenhuma issue)"
        return json.dumps(data, ensure_ascii=False)

    if action == "create_issue":
        title = inp.get("title", "")
        body = inp.get("body", "")
        if not title:
            return "Erro: title obrigatório"
        data = _req(f"{base_url}/issues", "POST", {"title": title, "body": body})
        if "error" in data:
            return json.dumps(data)
        append_daily_log(f"Issue criada: #{data.get('number')} {title}")
        return f"✅ Issue #{data['number']} criada: {data.get('html_url', '')}"

    if action == "check_runs":
        num = inp.get("number", 0)
        if not num:
            return "Erro: number obrigatório"
        pr_data = _req(f"{base_url}/pulls/{num}")
        if "error" in pr_data:
            return json.dumps(pr_data)
        sha = pr_data["head"]["sha"]
        data = _req(f"{base_url}/commits/{sha}/check-runs")
        if "error" in data:
            return json.dumps(data)
        runs = data.get("check_runs", [])
        lines = []
        for r in runs:
            status = r.get("conclusion") or r.get("status", "?")
            icon = "✅" if status == "success" else "❌" if status == "failure" else "⏳"
            lines.append(f"{icon} {r['name']}: {status}")
        return "\n".join(lines) or "(nenhum check run)"

    return f"Ação github desconhecida: {action}"
