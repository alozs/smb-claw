#!/bin/bash
# release.sh — Bump versão, gera changelog com IA, commita, taga, pusha,
#              cria GitHub Release e atualiza index.html
# Uso: ./release.sh [patch|minor|major]  (padrão: patch)
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

BUMP="${1:-patch}"

# ── Carrega config ──────────────────────────────────────────────────────────
load_env() {
    [ ! -f "$1" ] && return
    while IFS= read -r line; do
        line="${line%%#*}"
        line="$(echo "$line" | xargs 2>/dev/null)" || continue
        [ -z "$line" ] && continue
        key="${line%%=*}"
        val="${line#*=}"
        [ -z "${!key}" ] && export "$key=$val"
    done < "$1"
}
load_env "$BASE_DIR/secrets.global"
load_env "$BASE_DIR/config.global"

# ── Telegram helper ─────────────────────────────────────────────────────────
get_tg_token() {
    if [ -n "$BUGFIXER_TELEGRAM_TOKEN" ]; then
        echo "$BUGFIXER_TELEGRAM_TOKEN"
    else
        grep -m1 '^TELEGRAM_TOKEN=' "$BASE_DIR"/bots/*/.env 2>/dev/null | head -1 | cut -d= -f2
    fi
}

send_telegram() {
    local token
    token="$(get_tg_token)"
    [ -z "$token" ] && echo "⚠️  Sem token Telegram, notificação não enviada." && return
    local text="$1"
    [ ${#text} -gt 4000 ] && text="${text:0:3997}..."
    curl -s -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d chat_id="${ADMIN_ID}" \
        -d text="$text" \
        -d parse_mode="Markdown" > /dev/null 2>&1
}

# ── Verifica se há algo pra lançar ──────────────────────────────────────────
if [ -z "$(git status --porcelain)" ]; then
    LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
    if [ -n "$LAST_TAG" ]; then
        COMMITS=$(git log --oneline "$LAST_TAG..HEAD" 2>/dev/null | wc -l)
    else
        COMMITS=$(git log --oneline 2>/dev/null | wc -l)
    fi
    if [ "$COMMITS" -eq 0 ]; then
        echo "Nada para lançar — sem commits novos."
        exit 0
    fi
fi

# ── Lê versão atual e bumpa ─────────────────────────────────────────────────
VERSION_FILE="$BASE_DIR/VERSION"
CURRENT=$(cat "$VERSION_FILE" 2>/dev/null || echo "0.0.0")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
case "$BUMP" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    *)     PATCH=$((PATCH + 1)) ;;
esac
NEW_VERSION="$MAJOR.$MINOR.$PATCH"
echo "$NEW_VERSION" > "$VERSION_FILE"
echo "📦 Versão: $CURRENT → $NEW_VERSION"

# ── Coleta commits desde a última tag ───────────────────────────────────────
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [ -n "$LAST_TAG" ]; then
    RAW_LOG=$(git log --pretty=format:"- %s" "$LAST_TAG..HEAD" 2>/dev/null)
else
    RAW_LOG=$(git log --pretty=format:"- %s" 2>/dev/null)
fi
RAW_LOG=$(echo "$RAW_LOG" | grep -v "^- release:" | grep -viE "^- (docs|chore|style|refactor).*index\.html|gitignore|\.gitignore|landing|page|secrets?\.env|credentials?|token|api.?key|password|passwd|auth|security fix|vulnerab|expose|leak|hardcod" || true)

# Se não há commits mas há mudanças não commitadas, descreve os arquivos alterados
if [ -z "$RAW_LOG" ]; then
    UNCOMMITTED=$(git diff --name-only HEAD 2>/dev/null; git diff --cached --name-only 2>/dev/null)
    UNCOMMITTED=$(echo "$UNCOMMITTED" | sort -u | grep -v "^$" || true)
    if [ -n "$UNCOMMITTED" ]; then
        RAW_LOG="Arquivos modificados nesta versão:"$'\n'"$(echo "$UNCOMMITTED" | sed 's/^/- /')"
    fi
fi

DATE=$(date +%Y-%m-%d)

# ── Gera texto de changelog com IA ──────────────────────────────────────────
generate_ai_notes() {
    local commits="$1"
    local version="$2"
    local prompt="Você é um redator técnico. Gere notas de release profissionais e concisas para a versão $version do SMB Claw (framework de agentes de IA para Telegram).

Commits desta versão:
$commits

Regras:
- Escreva em português do Brasil
- Agrupe em seções: Novas funcionalidades, Melhorias, Correções (omita seções vazias)
- Cada item começa com bullet • e descreve o valor para o usuário (não detalhes técnicos internos)
- Máximo 250 palavras
- Não inclua título, número de versão nem data — só o corpo
- Não use markdown pesado (sem **, sem ##), apenas bullets simples
- IGNORE completamente commits relacionados a: landing page, index.html, site, documentação pública, .gitignore, arquivos de configuração interna, credenciais, tokens, secrets, segurança, correções de vazamento de dados, itens pessoais ou acidentais
- Mencione APENAS melhorias e funcionalidades relevantes para usuários finais do framework de bots"

    # 1. Claude CLI (OAuth)
    CLAUDE_BIN=$(which claude 2>/dev/null || echo "/home/ubuntu/.npm-global/bin/claude")
    if [ -x "$CLAUDE_BIN" ]; then
        result=$("$CLAUDE_BIN" -p "$prompt" 2>/dev/null) && [ -n "$result" ] && echo "$result" && return
    fi

    # 2. OpenRouter
    if [ -n "$OPENROUTER_API_KEY" ]; then
        PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'model':'openai/gpt-4o-mini','max_tokens':600,'messages':[{'role':'user','content':sys.argv[1]}]}))" "$prompt" 2>/dev/null)
        result=$(curl -s https://openrouter.ai/api/v1/chat/completions \
            -H "Authorization: Bearer $OPENROUTER_API_KEY" \
            -H "Content-Type: application/json" \
            -d "$PAYLOAD" 2>/dev/null \
            | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])' 2>/dev/null)
        [ -n "$result" ] && echo "$result" && return
    fi

    # 3. OpenAI
    if [ -n "$OPENAI_API_KEY" ]; then
        PAYLOAD=$(python3 -c "import json,sys; print(json.dumps({'model':'gpt-4o-mini','max_tokens':600,'messages':[{'role':'user','content':sys.argv[1]}]}))" "$prompt" 2>/dev/null)
        result=$(curl -s https://api.openai.com/v1/chat/completions \
            -H "Authorization: Bearer $OPENAI_API_KEY" \
            -H "Content-Type: application/json" \
            -d "$PAYLOAD" 2>/dev/null \
            | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["choices"][0]["message"]["content"])' 2>/dev/null)
        [ -n "$result" ] && echo "$result" && return
    fi

    # Fallback: lista de commits
    echo "$commits"
}

echo "🤖 Gerando changelog com IA..."
AI_NOTES=$(generate_ai_notes "$RAW_LOG" "$NEW_VERSION")

# ── Atualiza CHANGELOG.md ────────────────────────────────────────────────────
CHANGELOG="$BASE_DIR/CHANGELOG.md"
NEW_ENTRY="## $NEW_VERSION ($DATE)

$AI_NOTES"

if [ -f "$CHANGELOG" ]; then
    OLD=$(cat "$CHANGELOG")
    printf "# Changelog\n\n%s\n\n%s\n" "$NEW_ENTRY" "$(echo "$OLD" | tail -n +3)" > "$CHANGELOG"
else
    printf "# Changelog\n\n%s\n" "$NEW_ENTRY" > "$CHANGELOG"
fi
echo "📝 CHANGELOG.md atualizado"

# ── Atualiza seção changelog do index.html ───────────────────────────────────
INDEX="$BASE_DIR/index.html"
if [ -f "$INDEX" ] && grep -q "CHANGELOG_START" "$INDEX"; then
    # Converte o texto AI em itens HTML
    HTML_ITEMS=$(python3 - "$AI_NOTES" "$NEW_VERSION" "$DATE" << 'PYEOF'
import sys, re, html

notes = sys.argv[1]
version = sys.argv[2]
date = sys.argv[3]

lines = [l.strip() for l in notes.strip().splitlines() if l.strip()]

sections = []
current_section = None
current_items = []

for line in lines:
    # Detecta cabeçalho de seção (linha sem bullet)
    if not line.startswith("•") and not line.startswith("-") and len(line) < 80 and line.endswith(":"):
        if current_section is not None:
            sections.append((current_section, current_items))
        current_section = line.rstrip(":")
        current_items = []
    elif line.startswith("•") or line.startswith("-"):
        item = re.sub(r"^[•\-]\s*", "", line)
        current_items.append(html.escape(item))
    elif current_section is None:
        # Texto sem seção — coloca em seção genérica
        current_section = "Alterações"
        current_items = []
        current_items.append(html.escape(line))
    else:
        current_items.append(html.escape(line))

if current_section is not None:
    sections.append((current_section, current_items))

# Se não detectou seções, coloca tudo junto
if not sections:
    items_html = "\n".join(f"              <li>{html.escape(re.sub(r'^[•\\-]\\s*','',l))}</li>" for l in lines if l)
    print(f'''        <div class="cl-entry">
          <div class="cl-header">
            <span class="cl-version">v{version}</span>
            <span class="cl-date">{date}</span>
          </div>
          <div class="cl-body">
            <ul>
{items_html}
            </ul>
          </div>
        </div>''')
    sys.exit(0)

body_parts = []
for section_name, items in sections:
    if not items:
        continue
    items_html = "\n".join(f"              <li>{item}</li>" for item in items)
    body_parts.append(f'''            <p><strong>{html.escape(section_name)}</strong></p>
            <ul>
{items_html}
            </ul>''')

body = "\n".join(body_parts)
print(f'''        <div class="cl-entry">
          <div class="cl-header">
            <span class="cl-version">v{version}</span>
            <span class="cl-date">{date}</span>
          </div>
          <div class="cl-body">
{body}
          </div>
        </div>''')
PYEOF
)

    # Insere o novo entry logo após CHANGELOG_START, mantendo no máximo 3 entries
    python3 - "$INDEX" "$HTML_ITEMS" << 'PYEOF'
import sys, re

index_file = sys.argv[1]
new_entry = sys.argv[2]

content = open(index_file, encoding="utf-8").read()

start_marker = "<!-- CHANGELOG_START -->"
end_marker = "<!-- CHANGELOG_END -->"

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print("⚠️  Marcadores CHANGELOG_START/END não encontrados no index.html")
    sys.exit(0)

between = content[start_idx + len(start_marker):end_idx]

# Extrai entries existentes
existing = re.findall(r'(<div class="cl-entry">.*?</div>\s*</div>)', between, re.DOTALL)

# Mantém só as 2 mais recentes + o novo = 3 no total
kept = ([new_entry] + existing)[:3]

new_between = "\n        " + "\n        ".join(kept) + "\n        "
new_content = (
    content[:start_idx + len(start_marker)]
    + new_between
    + content[end_idx:]
)

open(index_file, "w", encoding="utf-8").write(new_content)
print("🌐 index.html atualizado")
PYEOF

fi

# ── Commit, tag, push ───────────────────────────────────────────────────────
git add -u  # inclui todos os arquivos rastreados modificados (bot.py, CLAUDE.md, etc.)
git add VERSION CHANGELOG.md  # garante que estes entram mesmo se ainda não rastreados
git commit -m "release: v$NEW_VERSION"
git tag "v$NEW_VERSION"
echo "🏷️  Tag: v$NEW_VERSION"

git push origin main --tags
echo "🚀 Push feito!"

# ── Cria GitHub Release ─────────────────────────────────────────────────────
GH_BIN=$(which gh 2>/dev/null || echo "")
if [ -n "$GH_BIN" ]; then
    echo "$AI_NOTES" | "$GH_BIN" release create "v$NEW_VERSION" \
        --title "v$NEW_VERSION — $(date +%d/%m/%Y)" \
        --notes-file - \
        2>/dev/null \
        && echo "📋 GitHub Release criado!" \
        || echo "⚠️  GitHub Release falhou (verifique: gh auth status)"
else
    echo "⚠️  gh CLI não encontrado — GitHub Release não criado"
fi

# ── Notifica admin ──────────────────────────────────────────────────────────
MSG="🚀 *Release v$NEW_VERSION*

$AI_NOTES

_Para atualizar: \`/update\`_"

send_telegram "$MSG"
echo "✅ Release v$NEW_VERSION completo!"
