#!/bin/bash
# Gera link temporário de acesso ao painel admin
# Uso: ./gerar-acesso.sh [minutos]  (default: 30)

TTL_MIN="${1:-30}"
TTL_SEC=$((TTL_MIN * 60))

RESPONSE=$(curl -s -X POST http://127.0.0.1:8080/api/gen-token \
  -H "Content-Type: application/json" \
  -d "{\"ttl\": $TTL_SEC}" 2>&1)

TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null)

if [ -z "$TOKEN" ]; then
    echo "❌ Erro: painel admin não está rodando ou retornou erro."
    echo "   Resposta: $RESPONSE"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PANEL_URL=$(grep -s '^ADMIN_PANEL_URL=' "$SCRIPT_DIR/config.global" | cut -d= -f2-)
if [ -z "$PANEL_URL" ]; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    PANEL_URL="http://${IP}:8080"
fi
URL="${PANEL_URL}/?token=${TOKEN}"

echo ""
echo "🔗 Link de acesso ao painel admin:"
echo ""
echo "   $URL"
echo ""
echo "⏱  Expira em ${TTL_MIN} minutos."
echo "🔒 Não compartilhe este link."
echo ""
