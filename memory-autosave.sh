#!/bin/bash
# Destila a memória diária de todos os bots no MEMORY.md.
# Suporta múltiplos provedores com fallback automático:
#   1. Claude OAuth        → ~/.claude/.credentials.json
#   2. Codex OAuth         → ~/.codex/auth.json
#   3. OPENROUTER_API_KEY  → OpenRouter (gpt-4o-mini)
#   4. OPENAI_API_KEY      → OpenAI API (gpt-4o-mini)
# Uso: ./memory-autosave.sh [nome-do-bot]  (sem argumento = todos os bots)

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
TODAY=$(date +%Y-%m-%d)
STATE_FILE="$BASE_DIR/.memory_autosave_state"

# ── Carrega credenciais de secrets.global e config.global ─────────────────
_load_key() {
    local key="$1"
    local val=""
    # secrets.global tem precedência
    val=$(grep "^${key}=" "$BASE_DIR/secrets.global" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)
    if [ -z "$val" ]; then
        val=$(grep "^${key}=" "$BASE_DIR/config.global" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)
    fi
    echo "$val"
}

OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$(_load_key OPENROUTER_API_KEY)}"
OPENAI_API_KEY="${OPENAI_API_KEY:-$(_load_key OPENAI_API_KEY)}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"  # não usado na chain, mas referenciado no heredoc

CLAUDE_CREDS="$HOME/.claude/.credentials.json"
CODEX_AUTH="${CODEX_HOME:-$HOME/.codex}/auth.json"

# ── Detecta qual provedor usar (em ordem de prioridade) ───────────────────
detect_provider() {
    if [ -f "$CLAUDE_CREDS" ] && python3 -c "
import json, sys
d = json.load(open('$CLAUDE_CREDS'))
tok = d.get('claudeAiOauth', {}).get('accessToken', '')
sys.exit(0 if tok else 1)
" 2>/dev/null; then
        echo "claude_oauth"
    elif [ -f "$CODEX_AUTH" ] && python3 -c "
import json, sys
d = json.load(open('$CODEX_AUTH'))
tok = d.get('tokens', {}).get('access_token', '')
sys.exit(0 if tok else 1)
" 2>/dev/null; then
        echo "codex_oauth"
    elif [ -n "$OPENROUTER_API_KEY" ]; then
        echo "openrouter"
    elif [ -n "$OPENAI_API_KEY" ]; then
        echo "openai_key"
    else
        echo "none"
    fi
}

PROVIDER=$(detect_provider)

if [ "$PROVIDER" = "none" ]; then
    echo "Erro: nenhum provedor de IA configurado."
    echo "Configure pelo menos um: OPENROUTER_API_KEY, OPENAI_API_KEY,"
    echo "ou faça login com 'claude' (OAuth) ou 'codex' (OAuth)."
    python3 -c "
import json
from datetime import datetime
state = {'last_run': datetime.now().isoformat(timespec='seconds'),
         'provider': None, 'status': 'no_provider', 'error': 'Nenhum provedor configurado',
         'bots_processed': 0, 'bots_skipped': 0}
open('$STATE_FILE', 'w').write(json.dumps(state))
"
    exit 1
fi

echo "Provedor selecionado: $PROVIDER"

# ── Lista de bots a processar ─────────────────────────────────────────────
if [ -n "${1:-}" ]; then
    BOTS=("$1")
else
    mapfile -t BOTS < <(ls "$BASE_DIR/bots/")
fi

BOTS_PROCESSED=0
BOTS_SKIPPED=0
RUN_ERROR=""

distill_bot() {
    local BOT_NAME="$1"
    local BOT_DIR="$BASE_DIR/bots/$BOT_NAME"
    local DAILY="$BOT_DIR/memory/$TODAY.md"
    local MEMORY="$BOT_DIR/MEMORY.md"
    local DB_PATH="$BOT_DIR/bot_data.db"

    echo "=== [$BOT_NAME] Destilando memória de $TODAY (via $PROVIDER) ==="

    python3 - << PYEOF
import json, sys
from pathlib import Path
from datetime import datetime

bot_name    = "$BOT_NAME"
bot_dir     = Path("$BOT_DIR")
daily_path  = Path("$DAILY")
memory_path = Path("$MEMORY")
db_path     = Path("$DB_PATH")
today       = "$TODAY"
provider    = "$PROVIDER"

openrouter_key = "$OPENROUTER_API_KEY"
anthropic_key  = "$ANTHROPIC_API_KEY"
openai_key     = "$OPENAI_API_KEY"
claude_creds   = Path("$CLAUDE_CREDS")
codex_auth     = Path("$CODEX_AUTH")

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
            archived_sessions.append({"archived_at": row["archived_at"], "messages": msgs})
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
        lines = [f"### Sessão {i} (arquivada em {ts})"]
        for msg in session["messages"]:
            role = msg.get("role", "")
            content = msg.get("content", "")
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
sections = [
    f'Você é um sistema de destilação de memória para o agente "{bot_name}".',
    f"## Memória de longo prazo atual (MEMORY.md)\n{memory_content}",
]
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

# ── Chama o provedor selecionado ──────────────────────────────────────────
result = None

if provider == "openrouter":
    from openai import OpenAI
    client = OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1")
    resp = client.chat.completions.create(
        model="openai/gpt-4o-mini", max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    result = resp.choices[0].message.content.strip()

elif provider == "anthropic_key":
    import anthropic
    client = anthropic.Anthropic(api_key=anthropic_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001", max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    result = resp.content[0].text.strip()

elif provider == "claude_oauth":
    import subprocess, shutil
    claude_bin = shutil.which("claude") or "/home/ubuntu/.npm-global/bin/claude"
    proc = subprocess.run(
        [claude_bin, "-p", prompt],
        capture_output=True, text=True, timeout=120
    )
    if proc.returncode != 0:
        raise Exception(f"claude CLI error: {proc.stderr.strip()}")
    result = proc.stdout.strip()

elif provider == "openai_key":
    from openai import OpenAI
    client = OpenAI(api_key=openai_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini", max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    result = resp.choices[0].message.content.strip()

elif provider == "codex_oauth":
    from openai import OpenAI
    auth = json.loads(codex_auth.read_text())
    token = auth["tokens"]["access_token"]
    client = OpenAI(api_key=token, base_url="https://api.openai.com/v1")
    resp = client.chat.completions.create(
        model="gpt-4o-mini", max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    result = resp.choices[0].message.content.strip()

if not result:
    print("  → Erro: resposta vazia da API")
    sys.exit(1)

memory_path.write_text(result, encoding="utf-8")
memory_path.chmod(0o600)

n_sessions = len(archived_sessions)
print(f"  → MEMORY.md atualizado ({memory_path.stat().st_size} bytes, {n_sessions} sessão(ões) arquivada(s))")

with open(daily_path, "a", encoding="utf-8") as f:
    f.write(f"\n### 23:50 (auto-distilação via {provider})\nMemória destilada. Sessões arquivadas incluídas: {n_sessions}.\n")

PYEOF
}

for bot in "${BOTS[@]}"; do
    if [ -d "$BASE_DIR/bots/$bot" ]; then
        if distill_bot "$bot"; then
            BOTS_PROCESSED=$((BOTS_PROCESSED + 1))
        else
            RUN_ERROR="Erro ao processar $bot"
            BOTS_SKIPPED=$((BOTS_SKIPPED + 1))
            echo "  → Erro ao processar $bot"
        fi
    else
        echo "Bot '$bot' não encontrado, pulando"
        BOTS_SKIPPED=$((BOTS_SKIPPED + 1))
    fi
done

# ── Salva estado para o painel admin ──────────────────────────────────────
STATUS="ok"
if [ "$BOTS_PROCESSED" -eq 0 ] && [ "$BOTS_SKIPPED" -gt 0 ]; then
    STATUS="error"
fi

python3 -c "
import json
from datetime import datetime
state = {
    'last_run': datetime.now().isoformat(timespec='seconds'),
    'provider': '$PROVIDER',
    'status': '$STATUS',
    'error': '$RUN_ERROR',
    'bots_processed': $BOTS_PROCESSED,
    'bots_skipped': $BOTS_SKIPPED,
}
open('$STATE_FILE', 'w').write(json.dumps(state))
"

echo ""
echo "✅ memory-autosave concluído ($PROVIDER) — $BOTS_PROCESSED bot(s) processado(s)"
