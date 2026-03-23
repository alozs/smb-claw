"""Ferramenta Notion: integração com a API REST v2022-06-28."""

import json
import time
import urllib.request
import urllib.error
import logging

from security import sanitize_output

logger = logging.getLogger(__name__)

NOTION_VERSION = "2022-06-28"
NOTION_BASE = "https://api.notion.com/v1"
OUTPUT_LIMIT = 8000

DEFINITIONS = [{
    "name": "notion",
    "description": (
        "Interage com a API do Notion. Permite buscar páginas, ler e criar conteúdo, "
        "consultar databases e manipular blocos."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "search",
                    "get_page", "create_page", "update_page",
                    "get_database", "query_database",
                    "get_blocks", "append_blocks", "delete_block",
                ],
                "description": (
                    "search: busca páginas/databases por texto. "
                    "get_page: lê propriedades de uma página. "
                    "create_page: cria nova página. "
                    "update_page: atualiza propriedades de uma página. "
                    "get_database: lê schema de um database. "
                    "query_database: consulta linhas de um database. "
                    "get_blocks: lê conteúdo (blocos) de uma página. "
                    "append_blocks: adiciona conteúdo a uma página. "
                    "delete_block: remove um bloco."
                ),
            },
            "query": {
                "type": "string",
                "description": "Texto de busca (search)",
            },
            "page_id": {
                "type": "string",
                "description": "ID da página Notion (com ou sem hífens)",
            },
            "database_id": {
                "type": "string",
                "description": "ID do database Notion (com ou sem hífens)",
            },
            "block_id": {
                "type": "string",
                "description": "ID do bloco (ou página) para get_blocks/append_blocks/delete_block",
            },
            "parent_id": {
                "type": "string",
                "description": "ID do parent (página ou database) para create_page",
            },
            "parent_type": {
                "type": "string",
                "enum": ["page_id", "database_id"],
                "description": "Tipo do parent: 'page_id' (default) ou 'database_id'",
            },
            "title": {
                "type": "string",
                "description": "Título da página (create_page)",
            },
            "properties": {
                "type": "object",
                "description": "Propriedades da página no formato Notion API (create/update_page)",
            },
            "content": {
                "type": "string",
                "description": "Texto simples para append_blocks (linhas viram parágrafos automaticamente)",
            },
            "blocks_json": {
                "type": "string",
                "description": "Array JSON de blocos Notion para append_blocks (formato avançado)",
            },
            "filter": {
                "type": "object",
                "description": "Filtro para query_database no formato Notion API",
            },
            "sorts": {
                "type": "array",
                "description": "Ordenação para query_database (array de objetos {property, direction})",
            },
            "page_size": {
                "type": "integer",
                "description": "Itens por página (default: 50, max: 100)",
            },
            "start_cursor": {
                "type": "string",
                "description": "Cursor de paginação (next_cursor de resposta anterior)",
            },
        },
        "required": ["action"],
    },
}]


def _notion_request(url: str, token: str, method: str = "GET",
                    body_data: dict | list | None = None,
                    retries: int = 2) -> dict | list:
    """Faz requisição à API Notion com retry automático para rate limit (429)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    data = json.dumps(body_data).encode() if body_data is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)

    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read().decode(errors="replace")
                return json.loads(content) if content.strip() else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                retry_after = int(e.headers.get("Retry-After", "1"))
                time.sleep(min(retry_after, 5))
                continue
            body = e.read().decode(errors="replace")[:500]
            try:
                err_data = json.loads(body)
                return {"error": f"HTTP {e.code}: {err_data.get('message', body)}"}
            except Exception:
                return {"error": f"HTTP {e.code}: {body}"}
        except Exception as e:
            return {"error": str(e)}
    return {"error": "Rate limit excedido após retries"}


def _rich_text_to_str(rich_text: list) -> str:
    """Extrai texto plano de um array rich_text do Notion."""
    return "".join(rt.get("plain_text", "") for rt in (rich_text or []))


def _text_to_blocks(text: str) -> list:
    """Converte texto simples em blocos paragraph do Notion."""
    blocks = []
    paragraphs = text.split("\n\n") if "\n\n" in text else text.split("\n")
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": para}}]
            },
        })
    return blocks


def _blocks_to_text(blocks: list, indent: int = 0) -> str:
    """Converte blocos Notion em texto legível."""
    lines = []
    prefix = "  " * indent
    for block in blocks:
        btype = block.get("type", "unknown")
        bdata = block.get(btype, {})
        rt = _rich_text_to_str(bdata.get("rich_text", []))

        if btype == "paragraph":
            lines.append(f"{prefix}{rt}" if rt else "")
        elif btype in ("heading_1", "heading_2", "heading_3"):
            level = {"heading_1": "##", "heading_2": "###", "heading_3": "####"}[btype]
            lines.append(f"{prefix}{level} {rt}")
        elif btype == "bulleted_list_item":
            lines.append(f"{prefix}- {rt}")
        elif btype == "numbered_list_item":
            lines.append(f"{prefix}1. {rt}")
        elif btype == "to_do":
            checked = "x" if bdata.get("checked") else " "
            lines.append(f"{prefix}[{checked}] {rt}")
        elif btype == "code":
            lang = bdata.get("language", "")
            lines.append(f"{prefix}```{lang}\n{rt}\n{prefix}```")
        elif btype == "quote":
            lines.append(f"{prefix}> {rt}")
        elif btype == "callout":
            icon = bdata.get("icon", {}).get("emoji", "")
            lines.append(f"{prefix}{icon} {rt}")
        elif btype == "divider":
            lines.append(f"{prefix}---")
        elif btype == "child_page":
            title = bdata.get("title", "(sem título)")
            lines.append(f"{prefix}📄 [{title}] (sub-página, ID: {block.get('id', '')})")
        elif btype == "child_database":
            title = bdata.get("title", "(sem título)")
            lines.append(f"{prefix}🗄 [{title}] (sub-database, ID: {block.get('id', '')})")
        else:
            if rt:
                lines.append(f"{prefix}[{btype}] {rt}")
            else:
                lines.append(f"{prefix}[{btype}]")

    return "\n".join(lines)


def _get_page_title(page: dict) -> str:
    """Extrai o título de um objeto page do Notion."""
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            return _rich_text_to_str(prop.get("title", [])) or "(sem título)"
    return "(sem título)"


def _format_page(data: dict) -> str:
    """Formata um objeto page Notion em texto legível."""
    title = _get_page_title(data)
    page_id = data.get("id", "")
    url = data.get("url", "")
    created = data.get("created_time", "")[:10]
    edited = data.get("last_edited_time", "")[:10]

    lines = [
        f"Título: {title}",
        f"ID: {page_id}",
        f"URL: {url}",
        f"Criado: {created} | Editado: {edited}",
    ]

    # Propriedades (exceto title, que já mostramos)
    props = data.get("properties", {})
    prop_lines = []
    for name, prop in props.items():
        ptype = prop.get("type", "")
        if ptype == "title":
            continue
        val = _extract_prop_value(prop, ptype)
        if val:
            prop_lines.append(f"  {name}: {val}")
    if prop_lines:
        lines.append("Propriedades:")
        lines.extend(prop_lines)

    return "\n".join(lines)


def _extract_prop_value(prop: dict, ptype: str) -> str:
    """Extrai valor legível de uma propriedade Notion."""
    try:
        if ptype == "rich_text":
            return _rich_text_to_str(prop.get("rich_text", []))
        if ptype == "number":
            v = prop.get("number")
            return str(v) if v is not None else ""
        if ptype == "select":
            s = prop.get("select")
            return s["name"] if s else ""
        if ptype == "multi_select":
            return ", ".join(s["name"] for s in prop.get("multi_select", []))
        if ptype == "date":
            d = prop.get("date")
            if not d:
                return ""
            return d.get("start", "") + (f" → {d['end']}" if d.get("end") else "")
        if ptype == "checkbox":
            return "✅" if prop.get("checkbox") else "☐"
        if ptype == "url":
            return prop.get("url", "") or ""
        if ptype == "email":
            return prop.get("email", "") or ""
        if ptype == "phone_number":
            return prop.get("phone_number", "") or ""
        if ptype == "people":
            return ", ".join(
                p.get("name", p.get("id", "")) for p in prop.get("people", [])
            )
        if ptype == "files":
            files = prop.get("files", [])
            return ", ".join(
                f.get("name", f.get("external", {}).get("url", "")) for f in files
            )
        if ptype == "formula":
            formula = prop.get("formula", {})
            ftype = formula.get("type", "")
            return str(formula.get(ftype, ""))
        if ptype == "relation":
            rels = prop.get("relation", [])
            return ", ".join(r.get("id", "") for r in rels)
        if ptype in ("created_time", "last_edited_time"):
            return str(prop.get(ptype, ""))[:10]
        if ptype in ("created_by", "last_edited_by"):
            u = prop.get(ptype, {})
            return u.get("name", u.get("id", ""))
    except Exception:
        pass
    return ""


def execute(inp: dict, *, config: dict) -> str:
    token = config.get("NOTION_API_KEY", "")
    if not token:
        return "Erro: NOTION_API_KEY não configurado em secrets.env"

    bot_name = config.get("BOT_NAME", "bot")
    secrets = [token]
    append_daily_log = config.get("append_daily_log", lambda _: None)

    def _req(endpoint: str, method: str = "GET", body=None) -> dict | list:
        url = f"{NOTION_BASE}/{endpoint.lstrip('/')}"
        result = _notion_request(url, token, method, body)
        if isinstance(result, dict) and "error" in result:
            result["error"] = sanitize_output(result["error"], secrets)
        return result

    def _truncate(text: str, limit: int = OUTPUT_LIMIT) -> str:
        if len(text) > limit:
            return text[:limit] + f"\n\n(... saída truncada em {limit} chars)"
        return text

    action = inp.get("action", "")

    # ── search ────────────────────────────────────────────────────────────────
    if action == "search":
        query = inp.get("query", "")
        body: dict = {}
        if query:
            body["query"] = query
        page_size = inp.get("page_size", 20)
        body["page_size"] = min(page_size, 100)
        if inp.get("start_cursor"):
            body["start_cursor"] = inp["start_cursor"]
        data = _req("/search", "POST", body)
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        results = data.get("results", [])
        if not results:
            return "(nenhum resultado encontrado)"
        lines = []
        for r in results:
            otype = r.get("object", "")
            rid = r.get("id", "")
            if otype == "page":
                title = _get_page_title(r)
                lines.append(f"📄 [page] {title}\n   ID: {rid}")
            elif otype == "database":
                db_title = _rich_text_to_str(r.get("title", []))
                lines.append(f"🗄 [database] {db_title or '(sem título)'}\n   ID: {rid}")
        if data.get("has_more"):
            lines.append(f"\n(mais resultados — use start_cursor: {data.get('next_cursor')})")
        return _truncate("\n".join(lines))

    # ── get_page ──────────────────────────────────────────────────────────────
    if action == "get_page":
        page_id = inp.get("page_id", "")
        if not page_id:
            return "Erro: page_id obrigatório"
        data = _req(f"/pages/{page_id}")
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        return _truncate(_format_page(data))

    # ── create_page ───────────────────────────────────────────────────────────
    if action == "create_page":
        parent_id = inp.get("parent_id", "")
        if not parent_id:
            return "Erro: parent_id obrigatório"
        parent_type = inp.get("parent_type", "page_id")
        title = inp.get("title", "")
        properties = inp.get("properties", {})

        # Garante propriedade title se não veio em properties
        if title and "title" not in properties:
            properties["title"] = {
                "title": [{"type": "text", "text": {"content": title}}]
            }

        body = {
            "parent": {parent_type: parent_id},
            "properties": properties,
        }
        data = _req("/pages", "POST", body)
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        page_id = data.get("id", "")
        url = data.get("url", "")
        append_daily_log(f"Notion: página criada '{title or page_id}'")
        return f"✅ Página criada\nID: {page_id}\nURL: {url}"

    # ── update_page ───────────────────────────────────────────────────────────
    if action == "update_page":
        page_id = inp.get("page_id", "")
        if not page_id:
            return "Erro: page_id obrigatório"
        properties = inp.get("properties", {})
        title = inp.get("title", "")
        if title and "title" not in properties:
            properties["title"] = {
                "title": [{"type": "text", "text": {"content": title}}]
            }
        if not properties:
            return "Erro: forneça properties ou title para atualizar"
        body = {"properties": properties}
        data = _req(f"/pages/{page_id}", "PATCH", body)
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        append_daily_log(f"Notion: página {page_id} atualizada")
        return f"✅ Página atualizada\nID: {data.get('id', page_id)}\nURL: {data.get('url', '')}"

    # ── get_database ──────────────────────────────────────────────────────────
    if action == "get_database":
        database_id = inp.get("database_id", "")
        if not database_id:
            return "Erro: database_id obrigatório"
        data = _req(f"/databases/{database_id}")
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        db_title = _rich_text_to_str(data.get("title", []))
        props = data.get("properties", {})
        lines = [
            f"Database: {db_title or '(sem título)'}",
            f"ID: {data.get('id', '')}",
            f"URL: {data.get('url', '')}",
            "",
            "Colunas (propriedades):",
        ]
        for name, prop in props.items():
            ptype = prop.get("type", "?")
            lines.append(f"  - {name} ({ptype})")
        return _truncate("\n".join(lines))

    # ── query_database ────────────────────────────────────────────────────────
    if action == "query_database":
        database_id = inp.get("database_id", "")
        if not database_id:
            return "Erro: database_id obrigatório"
        body = {}
        if inp.get("filter"):
            body["filter"] = inp["filter"]
        if inp.get("sorts"):
            body["sorts"] = inp["sorts"]
        page_size = inp.get("page_size", 50)
        body["page_size"] = min(page_size, 100)
        if inp.get("start_cursor"):
            body["start_cursor"] = inp["start_cursor"]
        data = _req(f"/databases/{database_id}/query", "POST", body)
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        results = data.get("results", [])
        if not results:
            return "(database vazio ou sem resultados para o filtro)"
        lines = [f"{len(results)} resultado(s):"]
        for i, page in enumerate(results, 1):
            title = _get_page_title(page)
            pid = page.get("id", "")
            lines.append(f"\n{i}. {title}\n   ID: {pid}")
            # Mostra outras propriedades
            for name, prop in page.get("properties", {}).items():
                ptype = prop.get("type", "")
                if ptype == "title":
                    continue
                val = _extract_prop_value(prop, ptype)
                if val:
                    lines.append(f"   {name}: {val}")
        if data.get("has_more"):
            lines.append(f"\n(mais resultados — use start_cursor: {data.get('next_cursor')})")
        return _truncate("\n".join(lines))

    # ── get_blocks ────────────────────────────────────────────────────────────
    if action == "get_blocks":
        block_id = inp.get("block_id") or inp.get("page_id", "")
        if not block_id:
            return "Erro: block_id ou page_id obrigatório"
        page_size = min(inp.get("page_size", 100), 100)
        url_path = f"/blocks/{block_id}/children?page_size={page_size}"
        if inp.get("start_cursor"):
            url_path += f"&start_cursor={inp['start_cursor']}"
        data = _req(url_path)
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        blocks = data.get("results", [])
        if not blocks:
            return "(página sem conteúdo)"
        text = _blocks_to_text(blocks)
        if data.get("has_more"):
            text += f"\n\n(mais blocos — use start_cursor: {data.get('next_cursor')})"
        return _truncate(text)

    # ── append_blocks ─────────────────────────────────────────────────────────
    if action == "append_blocks":
        block_id = inp.get("block_id") or inp.get("page_id", "")
        if not block_id:
            return "Erro: block_id ou page_id obrigatório"
        content = inp.get("content", "")
        blocks_json_str = inp.get("blocks_json", "")
        if blocks_json_str:
            try:
                blocks = json.loads(blocks_json_str)
            except json.JSONDecodeError as e:
                return f"Erro: blocks_json inválido — {e}"
        elif content:
            blocks = _text_to_blocks(content)
        else:
            return "Erro: forneça content (texto simples) ou blocks_json"
        body = {"children": blocks}
        data = _req(f"/blocks/{block_id}/children", "PATCH", body)
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        append_daily_log(f"Notion: conteúdo adicionado ao bloco {block_id}")
        n = len(data.get("results", []))
        return f"✅ {n} bloco(s) adicionado(s) ao bloco/página {block_id}"

    # ── delete_block ──────────────────────────────────────────────────────────
    if action == "delete_block":
        block_id = inp.get("block_id", "")
        if not block_id:
            return "Erro: block_id obrigatório"
        data = _req(f"/blocks/{block_id}", "DELETE")
        if isinstance(data, dict) and "error" in data:
            return json.dumps(data)
        append_daily_log(f"Notion: bloco {block_id} deletado")
        return f"✅ Bloco {block_id} deletado"

    return f"Ação notion desconhecida: {action}"
