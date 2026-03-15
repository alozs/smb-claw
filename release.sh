#!/bin/bash
# release.sh — Bump versão, gera changelog, commita, taga, pusha e notifica admin
# Uso: ./release.sh [mensagem opcional]
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

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
    # Trunca em 4000 chars (limite Telegram = 4096)
    [ ${#text} -gt 4000 ] && text="${text:0:3997}..."
    curl -s -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d chat_id="${ADMIN_ID}" \
        -d text="$text" \
        -d parse_mode="Markdown" > /dev/null 2>&1
}

# ── Verifica se há algo pra lançar ──────────────────────────────────────────
# Inclui changes staged + unstaged de arquivos tracked
if [ -z "$(git status --porcelain)" ]; then
    # Sem mudanças locais, verifica se há commits não-taggeados
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

# ── Lê versão atual e bumpa patch ───────────────────────────────────────────
VERSION_FILE="$BASE_DIR/VERSION"
CURRENT=$(cat "$VERSION_FILE" 2>/dev/null || echo "0.0.0")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"
PATCH=$((PATCH + 1))
NEW_VERSION="$MAJOR.$MINOR.$PATCH"
echo "$NEW_VERSION" > "$VERSION_FILE"
echo "📦 Versão: $CURRENT → $NEW_VERSION"

# ── Gera changelog ──────────────────────────────────────────────────────────
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
if [ -n "$LAST_TAG" ]; then
    LOG=$(git log --pretty=format:"- %s" "$LAST_TAG..HEAD" 2>/dev/null)
else
    LOG=$(git log --pretty=format:"- %s" 2>/dev/null)
fi
# Remove linha do próprio release anterior se houver
LOG=$(echo "$LOG" | grep -v "^- release:" || true)

DATE=$(date +%Y-%m-%d)
NEW_ENTRY="## $NEW_VERSION ($DATE)
$LOG"

# Prepend no CHANGELOG.md
CHANGELOG="$BASE_DIR/CHANGELOG.md"
if [ -f "$CHANGELOG" ]; then
    OLD=$(cat "$CHANGELOG")
    printf "# Changelog\n\n%s\n\n%s\n" "$NEW_ENTRY" "$(echo "$OLD" | tail -n +3)" > "$CHANGELOG"
else
    printf "# Changelog\n\n%s\n" "$NEW_ENTRY" > "$CHANGELOG"
fi

echo "📝 Changelog atualizado"

# ── Commit, tag, push ───────────────────────────────────────────────────────
git add VERSION CHANGELOG.md
git commit -m "release: v$NEW_VERSION"
git tag "v$NEW_VERSION"
echo "🏷️  Tag: v$NEW_VERSION"

git push origin main --tags
echo "🚀 Push feito!"

# ── Notifica admin ──────────────────────────────────────────────────────────
MSG="🚀 *Release v$NEW_VERSION*

$LOG

_Para atualizar outra instância:_ \`/update\`"

send_telegram "$MSG"
echo "✅ Release v$NEW_VERSION completo!"
