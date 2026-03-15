#!/bin/bash
# check-update.sh — Verifica se origin/main tem commits novos e notifica admin
# Roda via cron diário
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
    [ -z "$token" ] && return
    local text="$1"
    [ ${#text} -gt 4000 ] && text="${text:0:3997}..."
    curl -s -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d chat_id="${ADMIN_ID}" \
        -d text="$text" \
        -d parse_mode="Markdown" > /dev/null 2>&1
}

# ── Verifica ────────────────────────────────────────────────────────────────
git fetch origin main 2>/dev/null

BEHIND=$(git rev-list HEAD..origin/main --count 2>/dev/null || echo "0")

if [ "$BEHIND" -eq 0 ]; then
    exit 0
fi

# Mostra os commits pendentes
LOG=$(git log --pretty=format:"- %s" HEAD..origin/main 2>/dev/null)
REMOTE_VERSION=$(git show origin/main:VERSION 2>/dev/null || echo "?")
LOCAL_VERSION=$(cat "$BASE_DIR/VERSION" 2>/dev/null || echo "?")

send_telegram "🔔 *Atualização disponível!*

v$LOCAL_VERSION → v$REMOTE_VERSION ($BEHIND commit(s))

$LOG

_Use_ \`/update\` _em qualquer bot para atualizar._"

echo "$(date): Notificação enviada — $BEHIND commits pendentes"
