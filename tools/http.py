"""Ferramenta HTTP: requisições a APIs externas."""

import urllib.request
import urllib.error
from urllib.parse import urlparse

DEFINITIONS = [{
    "name": "http_request",
    "description": "Faz requisições HTTP para APIs externas.",
    "input_schema": {
        "type": "object",
        "properties": {
            "method":  {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "GET"},
            "url":     {"type": "string"},
            "headers": {"type": "object"},
            "body":    {"type": "string"},
            "timeout": {"type": "integer", "description": "Timeout em segundos (padrão: 60, máx: 300)", "default": 60},
            "max_response_bytes": {"type": "integer", "description": "Limite de bytes da resposta (padrão: 32768, máx: 131072)", "default": 32768},
        },
        "required": ["url"],
    },
}]


def execute(inp: dict, *, config: dict) -> str:
    bot_name = config["BOT_NAME"]
    url = inp["url"]
    method = inp.get("method", "GET").upper()
    headers = inp.get("headers", {})
    body = inp.get("body", "")
    timeout = min(int(inp.get("timeout", 60)), 300)
    max_bytes = min(int(inp.get("max_response_bytes", 32768)), 131072)

    parsed = urlparse(url)
    blocked = ["169.254.169.254", "metadata.google.internal", "localhost", "127.0.0.1"]
    if any(b in parsed.netloc for b in blocked):
        return "Erro: acesso a endereços internos bloqueado"

    data = body.encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", f"claude-bot/{bot_name}")
    for k, v in headers.items():
        req.add_header(k, v)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode(errors="replace")
            if len(content) > max_bytes:
                content = content[:max_bytes] + "\n...(truncado)"
            return f"status: {resp.status}\n\n{content}"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"
    except Exception as e:
        return f"Erro: {e}"
