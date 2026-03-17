#!/bin/bash
# entrypoint.sh — Inicializa todos os serviços do SMB Claw em Docker
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$BASE_DIR"

# ── Lê ADMIN_PORT do config.global ──────────────────────────────────────────
ADMIN_PORT=$(grep -s '^ADMIN_PORT=' "$BASE_DIR/config.global" | cut -d= -f2- | tr -d '"' | xargs)
ADMIN_PORT="${ADMIN_PORT:-8080}"

# ── Cron daemon ──────────────────────────────────────────────────────────────
echo "🕐 Iniciando cron daemon..."
service cron start || cron || true

# ── Limpa locks órfãos de execuções anteriores ───────────────────────────────
mkdir -p "$BASE_DIR/.locks"
rm -f "$BASE_DIR/.locks"/*.lock 2>/dev/null || true

# ── Painel admin ─────────────────────────────────────────────────────────────
mkdir -p "$BASE_DIR/logs"
echo "🌐 Iniciando painel admin na porta $ADMIN_PORT..."
nohup uvicorn admin.app:app --host 0.0.0.0 --port "$ADMIN_PORT" \
    >> "$BASE_DIR/logs/admin.log" 2>&1 &

# ── Bots ─────────────────────────────────────────────────────────────────────
echo "🤖 Iniciando bots..."
for bot_dir in "$BASE_DIR/bots"/*/; do
    [ -f "$bot_dir/.env" ] || continue
    bot=$(basename "$bot_dir")
    log="$BASE_DIR/logs/${bot}.log"
    echo "  ▶ $bot"
    nohup python3 "$BASE_DIR/bot.py" --bot-dir "$bot_dir" >> "$log" 2>&1 &
    sleep 1  # evita colisão de startup simultâneo
done

echo "✅ Todos os serviços iniciados. Aguardando..."

# Mantém o container vivo e faz restart de bots que morrerem
while true; do
    sleep 30
    for bot_dir in "$BASE_DIR/bots"/*/; do
        [ -f "$bot_dir/.env" ] || continue
        bot=$(basename "$bot_dir")
        if ! pgrep -f -- "bot.py --bot-dir.*bots/${bot}" > /dev/null 2>&1; then
            log="$BASE_DIR/logs/${bot}.log"
            echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') Reiniciando $bot..." >> "$log"
            rm -f "$BASE_DIR/.locks/"*"${bot}"* 2>/dev/null || true
            nohup python3 "$BASE_DIR/bot.py" --bot-dir "$bot_dir" >> "$log" 2>&1 &
            sleep 1
        fi
    done
    # Restart do admin panel se morrer
    if ! pgrep -f "uvicorn.*admin" > /dev/null 2>&1; then
        echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') Reiniciando admin panel..." >> "$BASE_DIR/logs/admin.log"
        nohup uvicorn admin.app:app --host 0.0.0.0 --port "$ADMIN_PORT" \
            >> "$BASE_DIR/logs/admin.log" 2>&1 &
    fi
done
