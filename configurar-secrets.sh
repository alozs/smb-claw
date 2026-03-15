#!/bin/bash
# Configura credenciais sensíveis de um bot de forma segura.
# As senhas são digitadas direto no terminal (sem aparecer na tela ou no chat).
# Uso: ./configurar-secrets.sh <nome-do-bot>

set -e

BOT_NAME="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_DIR="$SCRIPT_DIR/bots/$BOT_NAME"
SECRETS_FILE="$BOT_DIR/secrets.env"

if [ -z "$BOT_NAME" ]; then
    echo "Uso: $0 <nome-do-bot>"
    exit 1
fi

if [ ! -d "$BOT_DIR" ]; then
    echo "Erro: bot '$BOT_NAME' não encontrado em $BOT_DIR"
    exit 1
fi

echo ""
echo "=== Configurar secrets: $BOT_NAME ==="
echo "Os valores digitados NÃO aparecem na tela."
echo "Deixe em branco para não alterar um campo existente."
echo ""

# Carrega valores existentes
declare -A current
if [ -f "$SECRETS_FILE" ]; then
    while IFS='=' read -r key value; do
        [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
        current["$key"]="$value"
    done < "$SECRETS_FILE"
fi

read_secret() {
    local label="$1"
    local key="$2"
    local current_val="${current[$key]}"
    local hint=""
    [ -n "$current_val" ] && hint=" (atual: definido, Enter para manter)"

    local value
    read -rsp "  $label$hint: " value
    echo ""

    if [ -z "$value" ] && [ -n "$current_val" ]; then
        echo "  → mantido"
        current["$key"]="$current_val"
    elif [ -n "$value" ]; then
        echo "  → salvo"
        current["$key"]="$value"
    else
        echo "  → vazio (ignorado)"
    fi
}

echo "── Banco de dados ───────────────────────────────────"
echo "  Formato: postgresql://usuario:senha@host:5432/banco"
echo "           mysql://usuario:senha@host:3306/banco"
echo "           sqlite:///caminho/arquivo.db"
read_secret "DB_URL" "DB_URL"

echo ""
echo "── Git ──────────────────────────────────────────────"
read_secret "Token GitHub/GitLab (GIT_TOKEN)" "GIT_TOKEN"
read_secret "Username git        (GIT_USER)"  "GIT_USER"
read_secret "Email git           (GIT_EMAIL)" "GIT_EMAIL"

echo ""
echo "── GitHub ─────────────────────────────────────────────"
read_secret "Token GitHub (GITHUB_TOKEN)" "GITHUB_TOKEN"

echo ""
echo "── OpenRouter (opcional) ────────────────────────────"
echo "  Necessário para bots com PROVIDER=openrouter"
echo "  Obtenha em: https://openrouter.ai/keys"
read_secret "OpenRouter API Key (OPENROUTER_API_KEY)" "OPENROUTER_API_KEY"

echo ""
echo "── Outras APIs (opcional) ───────────────────────────"
read_secret "API Key extra 1 (API_KEY_1)" "API_KEY_1"
read_secret "API Key extra 2 (API_KEY_2)" "API_KEY_2"

# Grava o arquivo
{
    echo "# secrets.env — NÃO commitar, NÃO compartilhar"
    echo "# Gerado em: $(date)"
    echo ""
    for key in DB_URL GIT_TOKEN GIT_USER GIT_EMAIL GITHUB_TOKEN OPENROUTER_API_KEY API_KEY_1 API_KEY_2; do
        val="${current[$key]}"
        [ -n "$val" ] && echo "$key=$val"
    done
} > "$SECRETS_FILE"

chmod 600 "$SECRETS_FILE"

echo ""
echo "✅ secrets.env salvo em $SECRETS_FILE (chmod 600)"
echo ""

# Reinicia o serviço se estiver rodando
SERVICE="claude-bot-$BOT_NAME"
if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    echo "🔄 Reiniciando $SERVICE para aplicar..."
    sudo systemctl restart "$SERVICE"
    echo "✅ Reiniciado."
fi
