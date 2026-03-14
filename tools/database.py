"""Ferramenta database: queries SQL."""

import re
import json
from urllib.parse import urlparse

DEFINITIONS = [{
    "name": "db_query",
    "description": "Executa queries SQL no banco configurado.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query":  {"type": "string"},
            "params": {"type": "array", "items": {}},
        },
        "required": ["query"],
    },
}]


def execute(inp: dict, *, config: dict) -> str:
    db_url = config.get("DB_URL", "")
    if not db_url:
        return "Erro: DB_URL não configurado em secrets.env"

    query = inp["query"]
    params = inp.get("params", [])

    if re.match(r"^\s*(DROP|TRUNCATE|DELETE\s+FROM\s+\w+\s*;|ALTER\s+TABLE)", query, re.IGNORECASE):
        return "Operações destrutivas bloqueadas."

    try:
        import importlib
        parsed = urlparse(db_url)
        scheme = parsed.scheme.split("+")[0].lower()

        if scheme in ("postgresql", "postgres"):
            pg = importlib.import_module("psycopg2")
            conn = pg.connect(db_url)
        elif scheme == "mysql":
            pm = importlib.import_module("pymysql")
            conn = pm.connect(
                host=parsed.hostname, port=parsed.port or 3306,
                user=parsed.username, password=parsed.password,
                database=parsed.path.lstrip("/"),
            )
        elif scheme == "sqlite":
            import sqlite3
            conn = sqlite3.connect(parsed.path)
        else:
            return f"Banco não suportado: {scheme}"

        cur = conn.cursor()
        cur.execute(query, params or [])

        if query.strip().upper().startswith("SELECT"):
            rows = cur.fetchmany(100)
            cols = [d[0] for d in cur.description] if cur.description else []
            result = json.dumps(
                {"columns": cols, "rows": rows, "count": len(rows)},
                ensure_ascii=False, default=str,
            )
        else:
            conn.commit()
            result = json.dumps({"affected_rows": cur.rowcount})

        cur.close()
        conn.close()
        return result

    except ImportError as e:
        return f"Driver não instalado: {e}"
    except Exception as e:
        err = str(e)
        if db_url:
            err = err.replace(db_url, "***")
        return f"Erro DB: {err}"
