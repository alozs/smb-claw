#!/bin/bash
# Cria um novo bot completo com toda a infraestrutura.
# Uso: ./criar-bot.sh <nome-do-bot>
#
# ATENÇÃO: ao adicionar ferramentas ou arquivos novos ao sistema,
# leia CLAUDE.md e siga os checklists antes de modificar qualquer arquivo.

set -e

BOT_NAME="$1"
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
BOT_DIR="$BASE_DIR/bots/$BOT_NAME"
SERVICE_NAME="claude-bot-$BOT_NAME"
GLOBAL_CFG="$BASE_DIR/config.global"

if [ -z "$BOT_NAME" ]; then
    echo "Uso: $0 <nome-do-bot>"
    echo "Exemplo: $0 nutricionista"
    exit 1
fi

if [ -d "$BOT_DIR" ]; then
    echo "Erro: bot '$BOT_NAME' já existe em $BOT_DIR"
    exit 1
fi

# ── Lê config global ──────────────────────────────────────────────────────────

if [ ! -f "$GLOBAL_CFG" ]; then
    echo "Aviso: $GLOBAL_CFG não encontrado. Crie-o antes de continuar."
    exit 1
fi


echo ""
echo "Criando bot '$BOT_NAME'..."

# ── Estrutura de diretórios ───────────────────────────────────────────────────

mkdir -p "$BOT_DIR"/{memory,workspace}
chmod 700 "$BOT_DIR"

# ── BEHAVIOR.md ───────────────────────────────────────────────────────────────

cat > "$BOT_DIR/BEHAVIOR.md" << EOF
# Perfil Comportamental
<!-- Auto-gerado pelo behavior-extract.sh — não edite manualmente. -->
EOF
chmod 600 "$BOT_DIR/BEHAVIOR.md"

# ── .env ──────────────────────────────────────────────────────────────────────

cat > "$BOT_DIR/.env" << EOF
TELEGRAM_TOKEN=SEU_TOKEN_AQUI
BOT_NAME=$BOT_NAME
MAX_HISTORY=20

# Ferramentas: none | shell,cron,files,http,git,github,notion,database
TOOLS=shell,cron,files,http,git,github,database
WORK_DIR=$BOT_DIR/workspace

# Overrides opcionais (herdam do config.global se não definidos)
# MODEL=claude-opus-4-6
# ACCESS_MODE=approval

# Guardrails leves (notificação de ações de risco ao admin)
# GUARDRAILS_ENABLED=false
# GUARDRAILS_MODE=notify        # notify | confirm
# GUARDRAILS_LEVEL=dangerous    # moderate | dangerous

# Detecção de prompt injection (score 0.0 = desabilitado)
# INJECTION_THRESHOLD=0.7

# Aprendizado comportamental (requer behavior-extract.sh no cron)
# BEHAVIOR_LEARNING_ENABLED=false
# BEHAVIOR_MAX_CHARS=2000

# Provedor de IA:
#   anthropic  -> Claude via Anthropic API / OAuth Claude Code (padrao)
#   codex      -> OpenAI via Codex OAuth / OPENAI_API_KEY (sem API key se logado no Codex CLI)
#   openrouter -> Qualquer modelo via OpenRouter (requer OPENROUTER_API_KEY no config.global)
# PROVIDER=anthropic

# Modelos OpenAI/Codex (exemplos):
#   gpt-5.4              -> GPT-5.4 (mais recente)
#   gpt-5.3-codex-spark  -> GPT-5.3 Codex Spark (rápido)
#   gpt-5.3-codex        -> GPT-5.3 Codex
#   gpt-5.2-codex        -> GPT-5.2 Codex
#   gpt-5.2              -> GPT-5.2
#   gpt-5.1-codex-max    -> GPT-5.1 Codex Max (mais capaz)
#   gpt-5.1-codex-mini   -> GPT-5.1 Codex Mini (rápido e barato)
#   gpt-5.1              -> GPT-5.1
# Modelo OpenRouter (exemplos):
#   x-ai/grok-3                   -> Grok 3 (xAI)
#   google/gemini-2.0-flash       -> Gemini Flash (Google)
#   openai/gpt-5.4                -> GPT-5.4 (OpenAI)
#   mistralai/mistral-small-3.1   -> Mistral Small
# MODEL=x-ai/grok-3
EOF
chmod 600 "$BOT_DIR/.env"

# ── secrets.env ───────────────────────────────────────────────────────────────

cat > "$BOT_DIR/secrets.env" << 'EOF'
# Credenciais sensíveis — NÃO versionar, NÃO compartilhar
# Para preencher com segurança: ./configurar-secrets.sh <nome-do-bot>

# DB_URL=postgresql://usuario:senha@host:5432/banco
# GIT_TOKEN=
# GIT_USER=
# GIT_EMAIL=
# API_KEY_1=
# API_KEY_2=
EOF
chmod 600 "$BOT_DIR/secrets.env"

# ── soul.md ───────────────────────────────────────────────────────────────────

cat > "$BOT_DIR/soul.md" << EOF
# $BOT_NAME

Você é $BOT_NAME, um assistente especializado.

## Personalidade
- (descreva o tom e comportamento)

## Especialidade
- (descreva no que este bot é expert)

## Regras
- Sempre responda em português brasileiro
- Seja direto e objetivo
- Registre na memória diária eventos relevantes da conversa
EOF

# ── USER.md ───────────────────────────────────────────────────────────────────

cat > "$BOT_DIR/USER.md" << 'EOF'
# Usuário

## Preferências de comunicação
- Tom: direto e objetivo
- Idioma: português brasileiro

## Contexto pessoal
- (preencha aqui)

## Projetos ativos
- (preencha aqui)
EOF
chmod 600 "$BOT_DIR/USER.md"

# ── MEMORY.md ─────────────────────────────────────────────────────────────────

cat > "$BOT_DIR/MEMORY.md" << EOF
# Memória de longo prazo — $BOT_NAME

EOF
chmod 600 "$BOT_DIR/MEMORY.md"

# ── Detecção Docker ──────────────────────────────────────────────────────────
IN_DOCKER=false
if [ -f /.dockerenv ] || grep -q 'docker\|containerd' /proc/1/cgroup 2>/dev/null; then
    IN_DOCKER=true
fi

# ── Serviço systemd (skip em Docker) ─────────────────────────────────────────

if [ "$IN_DOCKER" = true ]; then
    echo "(Docker detectado — pulando criação de serviço systemd)"
else
    sudo tee "/etc/systemd/system/$SERVICE_NAME.service" > /dev/null << EOF
[Unit]
Description=Claude Bot: $BOT_NAME
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=$BASE_DIR
EnvironmentFile=$BOT_DIR/.env
ExecStart=python3 $BASE_DIR/bot.py --bot-dir $BOT_DIR
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

    sudo systemctl daemon-reload
fi

# ── Resumo ────────────────────────────────────────────────────────────────────

echo ""
echo "✅ Bot '$BOT_NAME' criado com sucesso!"
echo ""
echo "Estrutura:"
echo "  $BOT_DIR/"
echo "  ├── .env          ← config do bot (TOKEN obrigatório; demais herdam do config.global)"
echo "  ├── secrets.env   ← DB, git, APIs (preencher via configurar-secrets.sh)"
echo "  ├── soul.md       ← personalidade (EDITE ESTE)"
echo "  ├── USER.md       ← perfil do usuário"
echo "  ├── MEMORY.md     ← memória longo prazo (auto-preenchida)"
echo "  ├── BEHAVIOR.md   ← perfil comportamental (auto-gerado pelo behavior-extract.sh)"
echo "  ├── memory/       ← diários diários (auto-gerados)"
echo "  └── workspace/    ← arquivos do bot"
echo ""
echo "Próximos passos:"
echo ""
echo "  1. Obtenha o token no @BotFather e configure:"
echo "     nano $BOT_DIR/.env"
echo "     → TELEGRAM_TOKEN=<token>"
echo ""
echo "  2. Edite a personalidade do bot:"
echo "     nano $BOT_DIR/soul.md"
echo ""
echo "  3. (Opcional) Configure credenciais sensíveis:"
echo "     bash $BASE_DIR/configurar-secrets.sh $BOT_NAME"
echo ""
if [ "$IN_DOCKER" = true ]; then
echo "  4. Inicie o bot:"
echo "     nohup python3 $BASE_DIR/bot.py --bot-dir $BOT_DIR >> $BASE_DIR/logs/$BOT_NAME.log 2>&1 &"
echo ""
echo "  5. Ver logs:"
echo "     tail -f $BASE_DIR/logs/$BOT_NAME.log"
else
echo "  4. Inicie o bot:"
echo "     sudo systemctl start $SERVICE_NAME"
echo "     sudo systemctl enable $SERVICE_NAME"
echo ""
echo "  5. Ver logs:"
echo "     sudo journalctl -u $SERVICE_NAME -f"
fi
echo ""
