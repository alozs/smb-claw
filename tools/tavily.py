"""Ferramenta Tavily: busca na web e extração de conteúdo de páginas."""

import json
import urllib.request
import urllib.error

TAVILY_API_URL = "https://api.tavily.com"
OUTPUT_LIMIT = 8000


def _request(endpoint: str, payload: dict, api_key: str) -> dict:
    """Faz request à API Tavily."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{TAVILY_API_URL}{endpoint}",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode(errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {e.reason} — {body}")


DEFINITIONS = [{
    "name": "tavily",
    "description": (
        "Busca na web e extrai conteúdo limpo de páginas, incluindo sites com JavaScript. "
        "Use para: pesquisar informações atualizadas, encontrar artigos, verificar dados, "
        "ler páginas que o http_request não consegue (SPAs, React, etc.)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "get_content"],
                "description": (
                    "search: busca por query (ex: 'últimas notícias sobre X'). "
                    "get_content: extrai conteúdo limpo de uma URL específica."
                ),
            },
            "query": {
                "type": "string",
                "description": "Termos de busca (para action=search) ou URL (para action=get_content).",
            },
            "search_depth": {
                "type": "string",
                "enum": ["basic", "advanced"],
                "description": "basic: rápido e econômico. advanced: mais profundo, usa mais créditos. Padrão: basic.",
            },
            "max_results": {
                "type": "integer",
                "description": "Número máximo de resultados (1-10, padrão: 5).",
            },
            "include_answer": {
                "type": "boolean",
                "description": "Incluir resposta direta sintetizada pela Tavily (padrão: true).",
            },
            "include_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Limitar busca a estes domínios (ex: ['reuters.com', 'bbc.com']).",
            },
            "exclude_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Excluir estes domínios dos resultados.",
            },
        },
        "required": ["action", "query"],
    },
}]


def execute(inp: dict, *, config: dict) -> str:
    api_key = config.get("TAVILY_API_KEY", "")
    if not api_key:
        return "Erro: TAVILY_API_KEY não configurada. Ative a ferramenta Tavily no painel admin e insira a API key."

    action = inp.get("action", "search")

    if action == "search":
        return _search(inp, api_key)
    elif action == "get_content":
        return _get_content(inp, api_key)
    else:
        return f"Erro: action '{action}' não reconhecida. Use 'search' ou 'get_content'."


def _search(inp: dict, api_key: str) -> str:
    query = inp.get("query", "").strip()
    if not query:
        return "Erro: query é obrigatória."

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": inp.get("search_depth", "basic"),
        "max_results": min(int(inp.get("max_results", 5)), 10),
        "include_answer": inp.get("include_answer", True),
        "include_raw_content": False,
    }
    if inp.get("include_domains"):
        payload["include_domains"] = inp["include_domains"]
    if inp.get("exclude_domains"):
        payload["exclude_domains"] = inp["exclude_domains"]

    try:
        data = _request("/search", payload, api_key)
    except RuntimeError as e:
        return f"Erro na busca Tavily: {e}"

    lines = [f"## Busca: {query}\n"]

    answer = data.get("answer", "")
    if answer:
        lines.append(f"**Resposta direta:** {answer}\n")

    results = data.get("results", [])
    if not results:
        lines.append("Nenhum resultado encontrado.")
    else:
        lines.append(f"**{len(results)} resultado(s):**\n")
        for i, r in enumerate(results, 1):
            title = r.get("title", "Sem título")
            url = r.get("url", "")
            content = r.get("content", "").strip()
            score = r.get("score", 0)
            lines.append(f"### {i}. {title}")
            lines.append(f"URL: {url}")
            if score:
                lines.append(f"Relevância: {score:.2f}")
            if content:
                lines.append(f"\n{content[:600]}" + ("..." if len(content) > 600 else ""))
            lines.append("")

    output = "\n".join(lines)
    if len(output) > OUTPUT_LIMIT:
        output = output[:OUTPUT_LIMIT] + "\n...(truncado)"
    return output


def _get_content(inp: dict, api_key: str) -> str:
    url = inp.get("query", "").strip()
    if not url:
        return "Erro: informe a URL em 'query'."
    if not url.startswith("http"):
        return "Erro: 'query' deve ser uma URL válida (começando com http:// ou https://)."

    payload = {
        "api_key": api_key,
        "urls": [url],
    }

    try:
        data = _request("/extract", payload, api_key)
    except RuntimeError as e:
        return f"Erro ao extrair conteúdo: {e}"

    results = data.get("results", [])
    if not results:
        failed = data.get("failed_results", [])
        if failed:
            return f"Não foi possível extrair conteúdo de {url}: {failed[0].get('error', 'erro desconhecido')}"
        return f"Nenhum conteúdo extraído de {url}."

    r = results[0]
    title = r.get("title", "")
    raw = r.get("raw_content", "").strip()

    lines = []
    if title:
        lines.append(f"## {title}")
    lines.append(f"URL: {url}\n")
    if raw:
        lines.append(raw)
    else:
        lines.append("(conteúdo vazio)")

    output = "\n".join(lines)
    if len(output) > OUTPUT_LIMIT:
        output = output[:OUTPUT_LIMIT] + "\n...(truncado)"
    return output
