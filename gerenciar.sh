#!/bin/bash
# Gerencia todos os bots Claude
# Uso: ./gerenciar.sh [status|start|stop|restart|logs <bot>]

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
BOTS_DIR="$BASE_DIR/bots"

# ── Detecção Docker ──────────────────────────────────────────────────────────
IN_DOCKER=false
if [ -f /.dockerenv ] || grep -q 'docker\|containerd' /proc/1/cgroup 2>/dev/null; then
    IN_DOCKER=true
fi

list_bots() {
    ls "$BOTS_DIR" 2>/dev/null
}

# Detecta o canal do bot a partir do .env
get_bot_script() {
    local bot="$1"
    local channel
    channel=$(grep -m1 '^CHANNEL=' "$BOTS_DIR/$bot/.env" 2>/dev/null | cut -d= -f2)
    if [ "$channel" = "whatsapp" ]; then
        echo "$BASE_DIR/whatsapp_bot.py"
    else
        echo "$BASE_DIR/bot.py"
    fi
}

# Pattern para pkill — identifica o processo correto
get_bot_pattern() {
    local bot="$1"
    local script
    script=$(basename "$(get_bot_script "$bot")")
    echo "$script --bot-dir.*bots/$bot"
}

start_bot() {
    local bot="$1"
    local bot_script
    bot_script=$(get_bot_script "$bot")
    if [ "$IN_DOCKER" = true ]; then
        local log_file="$BASE_DIR/logs/${bot}.log"
        mkdir -p "$BASE_DIR/logs"
        nohup python3 "$bot_script" --bot-dir "$BOTS_DIR/$bot" >> "$log_file" 2>&1 &
        echo "✅ $bot iniciado (PID $!)"
    else
        sudo systemctl start "claude-bot-$bot" && echo "✅ $bot iniciado"
    fi
}

stop_bot() {
    local bot="$1"
    local pattern
    pattern=$(get_bot_pattern "$bot")
    if [ "$IN_DOCKER" = true ]; then
        pkill -f "$pattern" 2>/dev/null && echo "⏹ $bot parado" || echo "⏹ $bot não estava rodando"
    else
        sudo systemctl stop "claude-bot-$bot" && echo "⏹ $bot parado"
    fi
}

restart_bot() {
    local bot="$1"
    local bot_script pattern
    bot_script=$(get_bot_script "$bot")
    pattern=$(get_bot_pattern "$bot")
    if [ "$IN_DOCKER" = true ]; then
        pkill -f "$pattern" 2>/dev/null
        sleep 1
        local log_file="$BASE_DIR/logs/${bot}.log"
        mkdir -p "$BASE_DIR/logs"
        nohup python3 "$bot_script" --bot-dir "$BOTS_DIR/$bot" >> "$log_file" 2>&1 &
        echo "🔄 $bot reiniciado (PID $!)"
    else
        sudo systemctl restart "claude-bot-$bot" && echo "🔄 $bot reiniciado"
    fi
}

case "$1" in
    status)
        echo "=== Status dos Bots Claude ==="
        for bot in $(list_bots); do
            if [ "$IN_DOCKER" = true ]; then
                local pattern
                pattern=$(get_bot_pattern "$bot")
                if pgrep -f "$pattern" > /dev/null 2>&1; then
                    status="active"
                else
                    status="inativo"
                fi
            else
                status=$(systemctl is-active "claude-bot-$bot" 2>/dev/null || echo "inativo")
            fi
            echo "  $bot → $status"
        done
        ;;
    start)
        if [ -n "$2" ]; then
            start_bot "$2"
        else
            for bot in $(list_bots); do
                start_bot "$bot"
            done
        fi
        ;;
    stop)
        if [ -n "$2" ]; then
            stop_bot "$2"
        else
            for bot in $(list_bots); do
                stop_bot "$bot"
            done
        fi
        ;;
    restart)
        if [ -n "$2" ]; then
            restart_bot "$2"
        else
            for bot in $(list_bots); do
                restart_bot "$bot"
            done
        fi
        ;;
    logs)
        if [ -z "$2" ]; then
            echo "Uso: $0 logs <nome-do-bot>"
            exit 1
        fi
        if [ "$IN_DOCKER" = true ]; then
            local_log="$BASE_DIR/logs/$2.log"
            if [ -f "$local_log" ]; then
                tail -f "$local_log"
            else
                echo "Log não encontrado: $local_log"
                exit 1
            fi
        else
            sudo journalctl -u "claude-bot-$2" -f --no-pager
        fi
        ;;
    list)
        echo "Bots disponíveis:"
        for bot in $(list_bots); do
            echo "  - $bot"
        done
        ;;
    *)
        echo "Uso: $0 <comando> [bot]"
        echo ""
        echo "Comandos:"
        echo "  status          → status de todos os bots"
        echo "  list            → lista os bots"
        echo "  start [bot]     → inicia um ou todos"
        echo "  stop [bot]      → para um ou todos"
        echo "  restart [bot]   → reinicia um ou todos"
        echo "  logs <bot>      → logs em tempo real"
        ;;
esac
