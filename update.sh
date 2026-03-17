#!/bin/bash
# update.sh — Puxa atualizações do remote e reinicia serviços
# Uso: ./update.sh [--notify]
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

NOTIFY=false
[ "$1" = "--notify" ] && NOTIFY=true

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

# ── Fetch e verifica ────────────────────────────────────────────────────────
git fetch origin main 2>/dev/null

BEHIND=$(git rev-list HEAD..origin/main --count 2>/dev/null || echo "0")
if [ "$BEHIND" -eq 0 ]; then
    echo "✅ Já está atualizado."
    exit 0
fi

OLD_VERSION=$(cat "$BASE_DIR/VERSION" 2>/dev/null || echo "?")

# ── Pull ────────────────────────────────────────────────────────────────────
echo "📥 Puxando $BEHIND commit(s)..."
git pull origin main --ff-only

NEW_VERSION=$(cat "$BASE_DIR/VERSION" 2>/dev/null || echo "?")

# ── Detecção Docker ──────────────────────────────────────────────────────────
IN_DOCKER=false
if [ -f /.dockerenv ] || grep -q 'docker\|containerd' /proc/1/cgroup 2>/dev/null; then
    IN_DOCKER=true
fi

# ── Reinicia serviços um a um ───────────────────────────────────────────────
echo "🔄 Reiniciando serviços..."
RESTARTED=0
for bot_dir in "$BASE_DIR"/bots/*/; do
    bot=$(basename "$bot_dir")
    if [ "$IN_DOCKER" = true ]; then
        if pgrep -f "bot.py --bot-dir.*bots/$bot" > /dev/null 2>&1; then
            pkill -f "bot.py --bot-dir.*bots/$bot" 2>/dev/null
            # Espera processo morrer e lock ser liberado
            for _i in $(seq 1 10); do
                pgrep -f "bot.py --bot-dir.*bots/$bot" > /dev/null 2>&1 || break
                sleep 1
            done
            rm -f "$BASE_DIR/.locks/"*"${bot}"* 2>/dev/null
            log_file="$BASE_DIR/logs/${bot}.log"
            mkdir -p "$BASE_DIR/logs"
            nohup python3 "$BASE_DIR/bot.py" --bot-dir "$bot_dir" >> "$log_file" 2>&1 &
            echo "  ✅ $bot"
            RESTARTED=$((RESTARTED + 1))
            sleep 2
        fi
    else
        service="claude-bot-$bot"
        if systemctl is-active "$service" > /dev/null 2>&1; then
            sudo systemctl restart "$service"
            echo "  ✅ $bot"
            RESTARTED=$((RESTARTED + 1))
            sleep 2
        fi
    fi
done

# Admin panel
if [ "$IN_DOCKER" = true ]; then
    if pgrep -f "uvicorn.*admin" > /dev/null 2>&1; then
        pkill -f "uvicorn.*admin" 2>/dev/null
        sleep 1
        _ADMIN_PORT=$(grep -s '^ADMIN_PORT=' "$BASE_DIR/config.global" | cut -d= -f2-)
        _ADMIN_PORT="${_ADMIN_PORT:-8080}"
        nohup uvicorn admin.app:app --host 0.0.0.0 --port "$_ADMIN_PORT" >> "$BASE_DIR/logs/admin.log" 2>&1 &
        echo "  ✅ admin panel"
        RESTARTED=$((RESTARTED + 1))
    fi
else
    if systemctl is-active claude-bots-admin > /dev/null 2>&1; then
        sudo systemctl restart claude-bots-admin
        echo "  ✅ admin panel"
        RESTARTED=$((RESTARTED + 1))
    fi
fi

echo "✅ Update completo! v$OLD_VERSION → v$NEW_VERSION ($RESTARTED serviços reiniciados)"

# ── Notifica se solicitado ──────────────────────────────────────────────────
if [ "$NOTIFY" = true ]; then
    send_telegram "✅ *Update completo!*

v$OLD_VERSION → v$NEW_VERSION
$RESTARTED serviço(s) reiniciado(s)"
fi
