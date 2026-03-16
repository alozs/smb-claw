#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# SMB Claw — Bootstrap
# Instala dependências e abre o painel admin com o setup wizard.
# Uso: ./setup.sh
# ══════════════════════════════════════════════════════════════════════════════

set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
ADMIN_PORT="${ADMIN_PORT:-8080}"

# ── Cores ────────────────────────────────────────────────────────────────────
B='\033[1m'      # bold
D='\033[2m'      # dim
C='\033[36m'     # cyan
G='\033[32m'     # green
Y='\033[33m'     # yellow
R='\033[31m'     # red
W='\033[97m'     # white
N='\033[0m'      # reset
OK="${G}✔${N}"
FAIL="${R}✘${N}"
WARN="${Y}⚠${N}"
DOT="${D}│${N}"

# ── Helpers ──────────────────────────────────────────────────────────────────
step_header() {
    echo ""
    echo -e "  ${D}$1${N}"
    echo -e "  ${D}$(printf '%.0s─' $(seq 1 50))${N}"
}

spinner() {
    local pid=$1 msg=$2
    local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    local i=0
    while kill -0 "$pid" 2>/dev/null; do
        echo -ne "\r  ${C}${frames[$i]}${N} ${msg}"
        i=$(( (i+1) % ${#frames[@]} ))
        sleep 0.1
    done
    wait "$pid"
    return $?
}

# ══════════════════════════════════════════════════════════════════════════════
# HEADER
# ══════════════════════════════════════════════════════════════════════════════

clear
echo ""
echo -e "  ${C}${B}███████╗███╗   ███╗██████╗     ██████╗██╗      █████╗ ██╗    ██╗${N}"
echo -e "  ${C}${B}██╔════╝████╗ ████║██╔══██╗   ██╔════╝██║     ██╔══██╗██║    ██║${N}"
echo -e "  ${C}${B}███████╗██╔████╔██║██████╔╝   ██║     ██║     ███████║██║ █╗ ██║${N}"
echo -e "  ${C}${B}╚════██║██║╚██╔╝██║██╔══██╗   ██║     ██║     ██╔══██║██║███╗██║${N}"
echo -e "  ${C}${B}███████║██║ ╚═╝ ██║██████╔╝   ╚██████╗███████╗██║  ██║╚███╔███╔╝${N}"
echo -e "  ${C}${B}╚══════╝╚═╝     ╚═╝╚═════╝    ╚═════╝╚══════╝╚═╝  ╚═╝ ╚══╝╚══╝${N}"
echo ""
echo -e "  ${D}Multi-Bot AI Framework — Setup${N}"
echo ""
echo -e "  ${Y}${B}AVISO DE SEGURANCA${N}"
echo -e "  ${D}$(printf '%.0s─' $(seq 1 50))${N}"
echo -e "  ${D}Este software e fornecido \"como esta\" (as-is), sem${N}"
echo -e "  ${D}garantias de qualquer tipo, expressas ou implicitas.${N}"
echo -e "  ${D}O desenvolvedor nao se responsabiliza por danos,${N}"
echo -e "  ${D}perdas de dados ou uso indevido.${N}"
echo ""
echo -e "  ${D}Ao prosseguir, voce reconhece que:${N}"
echo -e "  ${D} - API keys e tokens sao armazenados localmente${N}"
echo -e "  ${D}   nos arquivos secrets.global e secrets.env${N}"
echo -e "  ${D} - Voce e responsavel por proteger o acesso a esta${N}"
echo -e "  ${D}   maquina e aos arquivos de credenciais${N}"
echo -e "  ${D} - Nao exponha portas do painel admin sem firewall${N}"
echo -e "  ${D} - Mantenha o sistema e dependencias atualizados${N}"
echo -e "  ${D}$(printf '%.0s─' $(seq 1 50))${N}"
echo ""
read -rp "  Deseja continuar? [S/n] " CONFIRM
CONFIRM="${CONFIRM:-S}"
if [[ ! "$CONFIRM" =~ ^[SsYy]$ ]]; then
    echo ""
    echo -e "  ${D}Setup cancelado.${N}"
    echo ""
    exit 0
fi
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: System check
# ══════════════════════════════════════════════════════════════════════════════

step_header "Verificando sistema"

# Python
if command -v python3 &>/dev/null; then
    PY_VER=$(python3 --version 2>&1 | awk '{print $2}')
    echo -e "  ${OK} Python ${D}${PY_VER}${N}"
else
    echo -e "  ${FAIL} ${R}python3 não encontrado${N}"
    echo -e "  ${DOT}"
    echo -e "  ${DOT}  Instale Python 3.10+:"
    echo -e "  ${DOT}  ${D}sudo apt install python3 python3-pip${N}"
    echo ""
    exit 1
fi

# Node
if command -v node &>/dev/null; then
    NODE_VER=$(node --version 2>&1)
    echo -e "  ${OK} Node.js ${D}${NODE_VER}${N}"
else
    echo -e "  ${D}○${N} Node.js ${D}(opcional — para Claude/Codex CLI)${N}"
fi

# ffmpeg
if command -v ffmpeg &>/dev/null; then
    echo -e "  ${OK} ffmpeg ${D}(suporte a voz/vídeo)${N}"
else
    echo -e "  ${D}○${N} ffmpeg ${D}(opcional — para transcrição de áudio)${N}"
fi

# Claude CLI
if command -v claude &>/dev/null; then
    CLAUDE_VER=$(claude --version 2>/dev/null | head -1 || echo "")
    echo -e "  ${OK} Claude Code CLI ${D}${CLAUDE_VER}${N}"
else
    echo -e "  ${D}○${N} Claude Code CLI ${D}(opcional — npm i -g @anthropic-ai/claude-code)${N}"
fi

# Codex CLI
if command -v codex &>/dev/null; then
    CODEX_VER=$(codex --version 2>/dev/null | head -1 || echo "")
    echo -e "  ${OK} Codex CLI ${D}${CODEX_VER}${N}"
else
    echo -e "  ${D}○${N} Codex CLI ${D}(opcional — npm i -g @openai/codex)${N}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Auth detection
# ══════════════════════════════════════════════════════════════════════════════

step_header "Detectando autenticação"

# Claude OAuth
if [ -f "$HOME/.claude/.credentials.json" ]; then
    CLAUDE_OK=$(python3 -c "
import json
with open('$HOME/.claude/.credentials.json') as f:
    c = json.load(f)
print('ok' if c.get('claudeAiOauth',{}).get('accessToken','') else 'empty')
" 2>/dev/null || echo "error")
    if [ "$CLAUDE_OK" = "ok" ]; then
        echo -e "  ${OK} Claude Code OAuth ${D}— autenticado${N}"
    else
        echo -e "  ${WARN} Claude Code OAuth ${D}— token vazio/expirado${N}"
    fi
else
    echo -e "  ${D}○${N} Claude Code OAuth ${D}— não configurado${N}"
fi

# Codex OAuth
if [ -f "$HOME/.codex/auth.json" ]; then
    CODEX_OK=$(python3 -c "
import json
with open('$HOME/.codex/auth.json') as f:
    c = json.load(f)
print('ok' if c.get('tokens',{}).get('access_token','') else 'empty')
" 2>/dev/null || echo "error")
    if [ "$CODEX_OK" = "ok" ]; then
        echo -e "  ${OK} Codex OAuth ${D}— autenticado (ChatGPT)${N}"
    else
        echo -e "  ${WARN} Codex OAuth ${D}— token vazio/expirado${N}"
    fi
else
    echo -e "  ${D}○${N} Codex OAuth ${D}— não configurado${N}"
fi

# API Keys (from secrets.global)
if [ -f "$BASE_DIR/secrets.global" ]; then
    grep -q "^ANTHROPIC_API_KEY=.\+" "$BASE_DIR/secrets.global" 2>/dev/null \
        && echo -e "  ${OK} Anthropic API Key ${D}— configurada${N}" \
        || echo -e "  ${D}○${N} Anthropic API Key"
    grep -q "^OPENAI_API_KEY=.\+" "$BASE_DIR/secrets.global" 2>/dev/null \
        && echo -e "  ${OK} OpenAI API Key ${D}— configurada${N}" \
        || echo -e "  ${D}○${N} OpenAI API Key"
    grep -q "^OPENROUTER_API_KEY=.\+" "$BASE_DIR/secrets.global" 2>/dev/null \
        && echo -e "  ${OK} OpenRouter API Key ${D}— configurada${N}" \
        || echo -e "  ${D}○${N} OpenRouter API Key"
else
    echo -e "  ${D}○${N} API Keys ${D}— nenhuma configurada ainda${N}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Python packages
# ══════════════════════════════════════════════════════════════════════════════

step_header "Verificando pacotes Python"

MISSING=$(python3 -c "
import importlib.util
pkgs = {'fastapi':'fastapi','uvicorn':'uvicorn','jinja2':'jinja2','anthropic':'anthropic',
        'openai':'openai','telegram':'python-telegram-bot','pdfplumber':'pdfplumber',
        'multipart':'python-multipart','aiofiles':'aiofiles'}
missing = [p for m,p in pkgs.items() if not importlib.util.find_spec(m)]
installed = [p for m,p in pkgs.items() if importlib.util.find_spec(m)]
for p in installed: print(f'OK {p}')
for p in missing: print(f'MISS {p}')
" 2>/dev/null || echo "ERRO")

MISSING_PKGS=""
while IFS= read -r line; do
    status=$(echo "$line" | cut -d' ' -f1)
    pkg=$(echo "$line" | cut -d' ' -f2-)
    if [ "$status" = "OK" ]; then
        echo -e "  ${OK} ${D}${pkg}${N}"
    elif [ "$status" = "MISS" ]; then
        echo -e "  ${FAIL} ${pkg} ${D}— faltando${N}"
        MISSING_PKGS="$MISSING_PKGS $pkg"
    fi
done <<< "$MISSING"

if [ -n "$MISSING_PKGS" ]; then
    # Detecta pip disponível
    if command -v pip3 &>/dev/null; then
        PIP_CMD="pip3"
    elif command -v pip &>/dev/null; then
        PIP_CMD="pip"
    else
        echo ""
        echo -e "  ${FAIL} pip não encontrado"
        echo -e "  ${D}Instale: apt install python3-pip${N}"
        exit 1
    fi
    echo ""
    echo -ne "  ${C}⠋${N} Instalando pacotes..."
    $PIP_CMD install --break-system-packages $MISSING_PKGS -q 2>/tmp/smb-pip.log &
    PIP_PID=$!
    spinner $PIP_PID "Instalando pacotes..." && {
        echo -e "\r  ${OK} Pacotes instalados com sucesso              "
    } || {
        echo -e "\r  ${FAIL} Erro ao instalar pacotes                    "
        echo -e "  ${D}Tente manualmente: $PIP_CMD install$MISSING_PKGS${N}"
        echo -e "  ${D}Log: cat /tmp/smb-pip.log${N}"
        exit 1
    }
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Directories
# ══════════════════════════════════════════════════════════════════════════════

mkdir -p "$BASE_DIR/bots" "$BASE_DIR/logs" 2>/dev/null

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Admin panel
# ══════════════════════════════════════════════════════════════════════════════

step_header "Painel Admin"

# Detect how to run uvicorn (venv vs system)
if [ -f "$BASE_DIR/admin/venv/bin/uvicorn" ]; then
    UVICORN="$BASE_DIR/admin/venv/bin/uvicorn"
elif command -v uvicorn &>/dev/null; then
    UVICORN="uvicorn"
else
    UVICORN="python3 -m uvicorn"
fi

# Checa se o próprio painel admin já está rodando na porta
if lsof -ti:$ADMIN_PORT >/dev/null 2>&1; then
    # Verifica se é o nosso processo (uvicorn admin.app)
    EXISTING_PID=$(lsof -ti:$ADMIN_PORT 2>/dev/null | head -1)
    if ps -p "$EXISTING_PID" -o args= 2>/dev/null | grep -q "admin.app"; then
        echo -e "  ${OK} Painel admin já está rodando na porta ${B}${ADMIN_PORT}${N}"
    else
        # Porta ocupada por outro processo — busca alternativa
        ORIGINAL_PORT=$ADMIN_PORT
        MAX_ATTEMPTS=10
        for i in $(seq 1 $MAX_ATTEMPTS); do
            ADMIN_PORT=$((ADMIN_PORT + 1))
            if ! lsof -ti:$ADMIN_PORT >/dev/null 2>&1; then
                break
            fi
            if [ "$i" -eq "$MAX_ATTEMPTS" ]; then
                echo -e "  ${FAIL} Porta ${B}${ORIGINAL_PORT}${N} ocupada e nenhuma alternativa livre (${ORIGINAL_PORT}–${ADMIN_PORT})"
                exit 1
            fi
        done
        echo -e "  ${WARN} Porta ${B}${ORIGINAL_PORT}${N} ocupada — usando ${B}${ADMIN_PORT}${N}"
        cd "$BASE_DIR"
        nohup $UVICORN admin.app:app --host 0.0.0.0 --port "$ADMIN_PORT" \
            > /tmp/smb-admin.log 2>&1 &
        sleep 2
        if lsof -ti:$ADMIN_PORT >/dev/null 2>&1; then
            echo -e "  ${OK} Painel admin iniciado na porta ${B}${ADMIN_PORT}${N}"
        else
            echo -e "  ${FAIL} Falha ao iniciar o painel admin na porta ${B}${ADMIN_PORT}${N}"
            echo ""
            echo -e "  ${D}Verifique o log: cat /tmp/smb-admin.log${N}"
            exit 1
        fi
    fi
else
    echo -ne "  ${C}⠋${N} Iniciando painel admin..."
    cd "$BASE_DIR"
    nohup $UVICORN admin.app:app --host 0.0.0.0 --port "$ADMIN_PORT" \
        > /tmp/smb-admin.log 2>&1 &
    sleep 2

    if lsof -ti:$ADMIN_PORT >/dev/null 2>&1; then
        echo -e "\r  ${OK} Painel admin iniciado na porta ${B}${ADMIN_PORT}${N}        "
    else
        echo -e "\r  ${FAIL} Falha ao iniciar o painel admin                "
        echo ""
        echo -e "  ${D}Verifique o log: cat /tmp/smb-admin.log${N}"
        exit 1
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# BOTS STATUS
# ══════════════════════════════════════════════════════════════════════════════

BOT_COUNT=$(find "$BASE_DIR/bots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
ACTIVE_COUNT=$(systemctl list-units --type=service --state=running 2>/dev/null | grep -c "claude-bot-" || echo "0")

if [ "$BOT_COUNT" -gt 0 ]; then
    step_header "Bots"
    for bot_dir in "$BASE_DIR/bots"/*/; do
        [ ! -d "$bot_dir" ] && continue
        bot_name=$(basename "$bot_dir")
        service="claude-bot-$bot_name"
        if systemctl is-active --quiet "$service" 2>/dev/null; then
            echo -e "  ${G}●${N} ${B}${bot_name}${N} ${D}— online${N}"
        else
            echo -e "  ${D}○${N} ${bot_name} ${D}— offline${N}"
        fi
    done
fi

# ══════════════════════════════════════════════════════════════════════════════
# DONE
# ══════════════════════════════════════════════════════════════════════════════

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
URL="http://${IP:-localhost}:${ADMIN_PORT}"

echo ""
echo ""
echo -e "  ${G}${B}╭──────────────────────────────────────────────────╮${N}"
echo -e "  ${G}${B}│${N}                                                  ${G}${B}│${N}"
echo -e "  ${G}${B}│${N}   ${OK} ${B}${W}Setup concluído!${N}                              ${G}${B}│${N}"
echo -e "  ${G}${B}│${N}                                                  ${G}${B}│${N}"
echo -e "  ${G}${B}│${N}   Abra no navegador:                            ${G}${B}│${N}"
echo -e "  ${G}${B}│${N}   ${C}${B}${URL}$(printf '%*s' $((36 - ${#URL})) '')${N}${G}${B}│${N}"
echo -e "  ${G}${B}│${N}                                                  ${G}${B}│${N}"

if [ "$BOT_COUNT" -eq 0 ]; then
    echo -e "  ${G}${B}│${N}   ${Y}O setup wizard abrirá automaticamente.${N}       ${G}${B}│${N}"
else
    echo -e "  ${G}${B}│${N}   ${D}${BOT_COUNT} bot(s) configurado(s), ${ACTIVE_COUNT} online${N}$(printf '%*s' $((14 - ${#BOT_COUNT} - ${#ACTIVE_COUNT})) '')${G}${B}│${N}"
fi

echo -e "  ${G}${B}│${N}                                                  ${G}${B}│${N}"
echo -e "  ${G}${B}╰──────────────────────────────────────────────────╯${N}"
echo ""
