#!/bin/bash
# Extrai e atualiza o perfil comportamental (BEHAVIOR.md) de cada bot.
# Documento vivo com tamanho fixo: cada execução SUBSTITUI o BEHAVIOR.md inteiro.
# O LLM recebe perfil atual + conversas do dia e faz merge inteligente.
#
# Fallback de provedor (igual ao memory-autosave.sh):
#   1. Claude OAuth → ~/.claude/.credentials.json
#   2. Codex OAuth  → ~/.codex/auth.json
#   3. OPENROUTER_API_KEY
#   4. OPENAI_API_KEY
#
# Uso: ./behavior-extract.sh [nome-do-bot]  (sem argumento = todos os bots)

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
TODAY=$(date +%Y-%m-%d)

# ── Carrega credenciais de secrets.global e config.global ─────────────────
_load_key() {
    local key="$1"
    local val=""
    val=$(grep "^${key}=" "$BASE_DIR/secrets.global" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)
    if [ -z "$val" ]; then
        val=$(grep "^${key}=" "$BASE_DIR/config.global" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || true)
    fi
    echo "$val"
}

OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-$(_load_key OPENROUTER_API_KEY)}"
OPENAI_API_KEY="${OPENAI_API_KEY:-$(_load_key OPENAI_API_KEY)}"

CLAUDE_CREDS="$HOME/.claude/.credentials.json"
CODEX_AUTH="${CODEX_HOME:-$HOME/.codex}/auth.json"

# ── Detecta qual provedor usar ─────────────────────────────────────────────
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
    echo "Erro: nenhum provedor de IA configurado para behavior-extract."
    exit 1
fi

echo "behavior-extract — Provedor: $PROVIDER"

# ── Lista de bots a processar ─────────────────────────────────────────────
if [ -n "${1:-}" ]; then
    BOTS=("$1")
else
    mapfile -t BOTS < <(ls "$BASE_DIR/bots/")
fi

extract_bot() {
    local BOT_NAME="$1"
    local BOT_DIR="$BASE_DIR/bots/$BOT_NAME"
    local BOT_ENV="$BOT_DIR/.env"
    local BEHAVIOR="$BOT_DIR/BEHAVIOR.md"
    local DB_PATH="$BOT_DIR/bot_data.db"

    # Verifica se BEHAVIOR_LEARNING_ENABLED=true no .env do bot
    local enabled
    enabled=$(grep "^BEHAVIOR_LEARNING_ENABLED=" "$BOT_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
    if [ "$enabled" != "true" ]; then
        echo "=== [$BOT_NAME] BEHAVIOR_LEARNING_ENABLED != true, pulando ==="
        return 0
    fi

    local max_chars
    max_chars=$(grep "^BEHAVIOR_MAX_CHARS=" "$BOT_ENV" 2>/dev/null | cut -d= -f2- | tr -d '"' || echo "2000")

    echo "=== [$BOT_NAME] Extraindo perfil comportamental (via $PROVIDER) ==="

    python3 - << PYEOF
import json, sys
from pathlib import Path

bot_name     = "$BOT_NAME"
bot_dir      = Path("$BOT_DIR")
behavior_path = Path("$BEHAVIOR")
db_path      = Path("$DB_PATH")
today        = "$TODAY"
provider     = "$PROVIDER"
max_chars    = int("$max_chars")

openrouter_key = "$OPENROUTER_API_KEY"
openai_key     = "$OPENAI_API_KEY"
claude_creds   = Path("$CLAUDE_CREDS")
codex_auth     = Path("$CODEX_AUTH")

# ── Perfil comportamental atual ────────────────────────────────────────────
current_behavior = behavior_path.read_text(encoding="utf-8").strip() if behavior_path.exists() else "(nenhum perfil ainda)"

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

# ── Diário do dia ──────────────────────────────────────────────────────────
daily_path = bot_dir / "memory" / f"{today}.md"
daily_content = daily_path.read_text(encoding="utf-8").strip() if daily_path.exists() else ""

# ── Verifica se há dados novos ─────────────────────────────────────────────
if not archived_sessions and not daily_content:
    print("  → sem sessões arquivadas nem diário hoje, pulando")
    sys.exit(0)

# ── Monta transcrição das sessões ─────────────────────────────────────────
sessions_text = ""
if archived_sessions:
    parts = []
    for i, session in enumerate(archived_sessions, 1):
        ts = session["archived_at"][:16].replace("T", " ")
        lines = [f"### Sessão {i} ({ts})"]
        for msg in session["messages"]:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                content = " ".join(text_parts).strip()
            if content and role in ("user", "assistant"):
                label = "Usuário" if role == "user" else "Bot"
                content_truncated = content[:400] + "..." if len(content) > 400 else content
                lines.append(f"**{label}:** {content_truncated}")
        parts.append("\n".join(lines))
    sessions_text = "\n\n".join(parts)

# ── Monta prompt ──────────────────────────────────────────────────────────
sections = []
sections.append(
    f'Você é um analista comportamental para o agente "{bot_name}". '
    f'Analise o perfil existente e as conversas de hoje para gerar um perfil atualizado.'
)
sections.append(f"## Perfil comportamental atual\n{current_behavior}")
if daily_content:
    sections.append(f"## Diário do dia ({today})\n{daily_content}")
if sessions_text:
    sections.append(f"## Sessões de conversa de hoje\n{sessions_text}")

sections.append(f"""## Tarefa

Gere um perfil comportamental atualizado seguindo estas regras:
- MANTENHA padrões confirmados por múltiplas conversas (são mais confiáveis)
- ADICIONE novas observações relevantes de hoje
- ATUALIZE informações que mudaram (ex: novo projeto, nova rotina)
- DESCARTE itens obsoletos ou que não se confirmaram com o tempo
- PRIORIZE informações recentes sobre antigas quando houver conflito
- NUNCA exceda {max_chars} caracteres no total
- Use bullet points concisos, sem frases longas

Formato obrigatório (seções fixas, use apenas as que tiverem conteúdo):

# Perfil Comportamental

## Comunicação
- idioma, tom, verbosidade, preferências de formato

## Rotinas
- horários de atividade, padrões recorrentes, dias da semana

## Preferências de Ferramentas
- tools mais usadas, workflows preferidos

## Tópicos e Interesses
- assuntos recorrentes, projetos ativos, áreas de foco

## Pessoas e Contexto
- contatos mencionados e seus papéis

Retorne SOMENTE o conteúdo do BEHAVIOR.md atualizado, sem explicações adicionais.
O resultado deve ter no máximo {max_chars} caracteres.""")

prompt = "\n\n".join(sections)

# ── Chama o provedor selecionado ──────────────────────────────────────────
result = None

if provider == "openrouter":
    from openai import OpenAI
    client = OpenAI(api_key=openrouter_key, base_url="https://openrouter.ai/api/v1")
    resp = client.chat.completions.create(
        model="openai/gpt-4o-mini", max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    result = resp.choices[0].message.content.strip()

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
        model="gpt-4o-mini", max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    result = resp.choices[0].message.content.strip()

elif provider == "codex_oauth":
    import urllib.request, urllib.parse, os as _os
    from openai import OpenAI

    def _refresh_codex(auth_data, auth_path):
        rt = auth_data.get("tokens", {}).get("refresh_token", "")
        if not rt:
            return None
        data = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": rt,
            "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        }).encode()
        req = urllib.request.Request(
            "https://auth.openai.com/oauth/token", data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            tokens = json.loads(resp.read())
        auth_data["tokens"]["access_token"] = tokens["access_token"]
        if tokens.get("refresh_token"):
            auth_data["tokens"]["refresh_token"] = tokens["refresh_token"]
        if tokens.get("id_token"):
            auth_data["tokens"]["id_token"] = tokens["id_token"]
        auth_path.write_text(json.dumps(auth_data, indent=2))
        _os.chmod(auth_path, 0o600)
        return auth_data

    auth = json.loads(codex_auth.read_text())

    def _make_codex_client(a):
        t = a["tokens"]["access_token"]
        aid = a.get("account_id", "")
        h = {}
        if aid:
            h["ChatGPT-Account-Id"] = aid
        return OpenAI(api_key=t, base_url="https://chatgpt.com/backend-api/wham", default_headers=h)

    client = _make_codex_client(auth)
    try:
        resp = client.responses.create(model="gpt-4o-mini", input=prompt)
    except Exception as _e:
        if "403" in str(_e):
            auth = _refresh_codex(auth, codex_auth)
            if auth:
                client = _make_codex_client(auth)
                resp = client.responses.create(model="gpt-4o-mini", input=prompt)
            else:
                raise
        else:
            raise
    result = resp.output_text.strip()

if not result:
    print("  → Erro: resposta vazia da API")
    sys.exit(1)

# Garante o limite de caracteres
if len(result) > max_chars:
    result = result[:max_chars]

behavior_path.write_text(result, encoding="utf-8")
behavior_path.chmod(0o600)

print(f"  → BEHAVIOR.md atualizado ({len(result)} chars / {max_chars} limite)")

PYEOF
}

PROCESSED=0
SKIPPED=0

for bot in "${BOTS[@]}"; do
    if [ -d "$BASE_DIR/bots/$bot" ]; then
        if extract_bot "$bot"; then
            PROCESSED=$((PROCESSED + 1))
        else
            SKIPPED=$((SKIPPED + 1))
            echo "  → Erro ao processar $bot"
        fi
    else
        echo "Bot '$bot' não encontrado, pulando"
        SKIPPED=$((SKIPPED + 1))
    fi
done

echo ""
echo "✅ behavior-extract concluído ($PROVIDER) — $PROCESSED bot(s) processado(s)"
