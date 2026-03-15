#!/bin/bash
# Gerencia todos os bots Claude
# Uso: ./gerenciar.sh [status|start|stop|restart|logs <bot>]

BASE_DIR="$(cd "$(dirname "$0")" && pwd)/bots"

list_bots() {
    ls "$BASE_DIR" 2>/dev/null
}

case "$1" in
    status)
        echo "=== Status dos Bots Claude ==="
        for bot in $(list_bots); do
            service="claude-bot-$bot"
            status=$(systemctl is-active "$service" 2>/dev/null || echo "inativo")
            echo "  $bot → $status"
        done
        ;;
    start)
        if [ -n "$2" ]; then
            sudo systemctl start "claude-bot-$2" && echo "✅ $2 iniciado"
        else
            for bot in $(list_bots); do
                sudo systemctl start "claude-bot-$bot" && echo "✅ $bot iniciado"
            done
        fi
        ;;
    stop)
        if [ -n "$2" ]; then
            sudo systemctl stop "claude-bot-$2" && echo "⏹ $2 parado"
        else
            for bot in $(list_bots); do
                sudo systemctl stop "claude-bot-$bot" && echo "⏹ $bot parado"
            done
        fi
        ;;
    restart)
        if [ -n "$2" ]; then
            sudo systemctl restart "claude-bot-$2" && echo "🔄 $2 reiniciado"
        else
            for bot in $(list_bots); do
                sudo systemctl restart "claude-bot-$bot" && echo "🔄 $bot reiniciado"
            done
        fi
        ;;
    logs)
        if [ -z "$2" ]; then
            echo "Uso: $0 logs <nome-do-bot>"
            exit 1
        fi
        sudo journalctl -u "claude-bot-$2" -f --no-pager
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
