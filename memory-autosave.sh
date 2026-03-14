#!/bin/bash
# Destila a memória diária de todos os bots no MEMORY.md via OpenRouter (gpt-4o-mini).
# Lê o diário diário (memory/YYYY-MM-DD.md) + sessões arquivadas (sessions_archive).
# Uso: ./memory-autosave.sh [nome-do-bot]  (sem argumento = todos os bots)

set -euo pipefail

BASE_DIR="/home/ubuntu/claude-bots"
TODAY=$(date +%Y-%m-%d)

# Carrega OPENROUTER_API_KEY do config.global ou ambiente
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    source <(grep "^OPENROUTER_API_KEY=" "$BASE_DIR/config.global" 2>/dev/null || true)
fi

if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "Erro: OPENROUTER_API_KEY não encontrada"
    exit 1
fi

# Lista de bots a processar
if [ -n "${1:-}" ]; then
    BOTS=("$1")
else
    mapfile -t BOTS < <(ls "$BASE_DIR/bots/")
fi

distill_bot() {
    local BOT_NAME="$1"
    local BOT_DIR="$BASE_DIR/bots/$BOT_NAME"
    local DAILY="$BOT_DIR/memory/$TODAY.md"
    local MEMORY="$BOT_DIR/MEMORY.md"
    local DB_PATH="$BOT_DIR/bot_data.db"

    echo "=== [$BOT_NAME] Destilando memória de $TODAY ==="

    python3 - << PYEOF
import json, sys
from pathlib import Path
from datetime import datetime
from openai import OpenAI

bot_name    = "$BOT_NAME"
bot_dir     = Path("$BOT_DIR")
daily_path  = Path("$DAILY")
memory_path = Path("$MEMORY")
db_path     = Path("$DB_PATH")
today       = "$TODAY"
api_key     = "$OPENROUTER_API_KEY"

# ── Diário do dia ──────────────────────────────────────────────────────────
daily_content = daily_path.read_text(encoding="utf-8").strip() if daily_path.exists() and daily_path.stat().st_size > 0 else ""

# ── Sessões arquivadas do dia ──────────────────────────────────────────────
archived_sessions = []
if db_path.exists():
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT user_id, messages, archived_at FROM sessions_archive "
            "WHERE bot_name = ? AND date(archived_at) = ? ORDER BY archived_at ASC",
            (bot_name, today)
        ).fetchall()
        for row in rows:
            msgs = json.loads(row["messages"])
            archived_sessions.append({
                "archived_at": row["archived_at"],
                "messages": msgs,
            })
    except Exception as e:
        print(f"  Aviso: não foi possível ler sessions_archive: {e}", file=sys.stderr)
    finally:
        conn.close()

# ── Monta transcrição das sessões ─────────────────────────────────────────
sessions_text = ""
if archived_sessions:
    parts = []
    for i, session in enumerate(archived_sessions, 1):
        ts = session["archived_at"][:16].replace("T", " ")
        lines = [f"### Sessão {i} (arquivada em {ts} UTC)"]
        for msg in session["messages"]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            # content pode ser string ou lista (tool_use blocks)
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(text_parts).strip()
            if content and role in ("user", "assistant"):
                label = "Usuário" if role == "user" else "Bot"
                content_truncated = content[:500] + "..." if len(content) > 500 else content
                lines.append(f"**{label}:** {content_truncated}")
        parts.append("\n".join(lines))
    sessions_text = "\n\n".join(parts)

# ── Verifica se há algo para processar ────────────────────────────────────
if not daily_content and not sessions_text:
    print("  → sem memória diária nem sessões arquivadas hoje, pulando")
    sys.exit(0)

# ── Memória atual ──────────────────────────────────────────────────────────
memory_content = memory_path.read_text(encoding="utf-8").strip() if memory_path.exists() else "(vazia)"

# ── Monta prompt ──────────────────────────────────────────────────────────
sections = [f'Você é um sistema de destilação de memória para o agente "{bot_name}".',
            f"## Memória de longo prazo atual (MEMORY.md)\n{memory_content}"]

if daily_content:
    sections.append(f"## Diário do dia ({today})\n{daily_content}")

if sessions_text:
    sections.append(f"## Sessões de conversa arquivadas hoje\n{sessions_text}")

sections.append("""## Tarefa
Analise o diário do dia e as sessões arquivadas. Decida o que merece ser adicionado/atualizado na memória de longo prazo.

Regras:
- Adicione apenas o que tem valor duradouro (decisões estruturais, preferências confirmadas, padrões, contexto permanente)
- Ignore ruído diário (logs temporários, erros pontuais já resolvidos, conversas banais)
- Se algo na memória atual estiver desatualizado pelo que aconteceu hoje, corrija
- Retorne o MEMORY.md completo e atualizado (não apenas o diff)
- Se não houver nada relevante para adicionar, retorne a memória atual sem modificações
- Formato markdown, organizado por seções temáticas

Retorne SOMENTE o conteúdo do MEMORY.md atualizado, sem explicações adicionais.""")

prompt = "\n\n".join(sections)

# ── Chama OpenRouter (gpt-4o-mini) ────────────────────────────────────────
client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
response = client.chat.completions.create(
    model="openai/gpt-4o-mini",
    max_tokens=4096,
    messages=[{"role": "user", "content": prompt}]
)
result = response.choices[0].message.content.strip()

if not result:
    print("  → Erro: resposta vazia da API")
    sys.exit(1)

memory_path.write_text(result, encoding="utf-8")
memory_path.chmod(0o600)

n_sessions = len(archived_sessions)
print(f"  → MEMORY.md atualizado ({memory_path.stat().st_size} bytes, {n_sessions} sessão(ões) arquivada(s) incluída(s))")

# Registra no diário
with open(daily_path, "a", encoding="utf-8") as f:
    f.write(f"\n### 23:50 (auto-distilação)\nMemória destilada. Sessões arquivadas incluídas: {n_sessions}.\n")

PYEOF
}

for bot in "${BOTS[@]}"; do
    if [ -d "$BASE_DIR/bots/$bot" ]; then
        distill_bot "$bot" || echo "  → Erro ao processar $bot"
    else
        echo "Bot '$bot' não encontrado, pulando"
    fi
done

echo ""
echo "✅ memory-autosave concluído"
