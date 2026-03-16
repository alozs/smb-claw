#!/bin/bash
# ══════════════════════════════════════════════════════════════════════════════
# SMB Claw — Bootstrap
# Instala dependências e abre o painel admin com o setup wizard.
# Uso: ./setup.sh
# ══════════════════════════════════════════════════════════════════════════════

set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
ADMIN_PORT="${ADMIN_PORT:-8080}"
IN_DOCKER=false
[ -f /.dockerenv ] || grep -q 'docker\|containerd' /proc/1/cgroup 2>/dev/null && IN_DOCKER=true

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

# Checa se uma porta está em uso (fallback se lsof não existir)
port_in_use() {
    local port=$1
    if command -v lsof &>/dev/null; then
        lsof -ti:"$port" >/dev/null 2>&1
    elif command -v ss &>/dev/null; then
        ss -tlnp 2>/dev/null | grep -q ":${port} "
    elif [ -e /proc/net/tcp ]; then
        local hex_port=$(printf '%04X' "$port")
        grep -qi ":${hex_port} " /proc/net/tcp 2>/dev/null
    else
        # Tenta conectar na porta
        (echo >/dev/tcp/127.0.0.1/"$port") 2>/dev/null
    fi
}

# Retorna PID do processo na porta (se disponível)
port_pid() {
    local port=$1
    if command -v lsof &>/dev/null; then
        lsof -ti:"$port" 2>/dev/null | head -1
    elif command -v ss &>/dev/null; then
        ss -tlnp 2>/dev/null | grep ":${port} " | grep -oP 'pid=\K[0-9]+' | head -1
    else
        echo ""
    fi
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
# STEP 0: Dependências do sistema (auto-install)
# ══════════════════════════════════════════════════════════════════════════════

SYS_PKGS=""
command -v python3 &>/dev/null || SYS_PKGS="$SYS_PKGS python3"
command -v pip3 &>/dev/null || command -v pip &>/dev/null || SYS_PKGS="$SYS_PKGS python3-pip"
command -v git &>/dev/null || SYS_PKGS="$SYS_PKGS git"
command -v curl &>/dev/null || SYS_PKGS="$SYS_PKGS curl"
command -v lsof &>/dev/null || SYS_PKGS="$SYS_PKGS lsof"

if [ -n "$SYS_PKGS" ]; then
    step_header "Instalando dependências do sistema"
    echo -ne "  ${C}⠋${N} apt install${SYS_PKGS}..."
    if command -v apt-get &>/dev/null; then
        apt-get update -qq >/dev/null 2>&1
        apt-get install -y -qq $SYS_PKGS >/dev/null 2>&1 &
        SYS_PID=$!
        spinner $SYS_PID "Instalando${SYS_PKGS}..." && {
            echo -e "\r  ${OK} Dependências do sistema instaladas             "
        } || {
            echo -e "\r  ${FAIL} Erro ao instalar dependências do sistema      "
            echo -e "  ${D}Tente manualmente: apt install${SYS_PKGS}${N}"
            exit 1
        }
    else
        echo -e "\r  ${FAIL} Gerenciador de pacotes não suportado (apt não encontrado)"
        echo -e "  ${D}Instale manualmente:${SYS_PKGS}${N}"
        exit 1
    fi
fi

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
    # Detecta se pip suporta --break-system-packages
    BSP_FLAG=""
    if $PIP_CMD install --break-system-packages --help &>/dev/null; then
        BSP_FLAG="--break-system-packages"
    fi
    $PIP_CMD install $BSP_FLAG $MISSING_PKGS -q 2>/tmp/smb-pip.log &
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

mkdir -p "$BASE_DIR/bots" "$BASE_DIR/logs" 2>/dev/null || {
    echo -e "  ${FAIL} Sem permissão para criar diretórios em ${B}${BASE_DIR}${N}"
    exit 1
}

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Admin panel
# ══════════════════════════════════════════════════════════════════════════════

step_header "Painel Admin"

# Verifica se o módulo admin existe
if [ ! -f "$BASE_DIR/admin/app.py" ]; then
    echo -e "  ${FAIL} Módulo admin não encontrado ${D}(admin/app.py)${N}"
    echo -e "  ${D}Verifique se o repositório está completo.${N}"
    exit 1
fi

# Detect how to run uvicorn (venv vs system)
if [ -f "$BASE_DIR/admin/venv/bin/uvicorn" ]; then
    UVICORN="$BASE_DIR/admin/venv/bin/uvicorn"
elif command -v uvicorn &>/dev/null; then
    UVICORN="uvicorn"
else
    UVICORN="python3 -m uvicorn"
fi

# Checa se o próprio painel admin já está rodando na porta
if port_in_use $ADMIN_PORT; then
    # Verifica se é o nosso processo (uvicorn admin.app)
    EXISTING_PID=$(port_pid $ADMIN_PORT)
    if [ -n "$EXISTING_PID" ] && ps -p "$EXISTING_PID" -o args= 2>/dev/null | grep -q "admin.app"; then
        echo -e "  ${OK} Painel admin já está rodando na porta ${B}${ADMIN_PORT}${N}"
    else
        # Porta ocupada por outro processo — busca alternativa
        ORIGINAL_PORT=$ADMIN_PORT
        MAX_ATTEMPTS=10
        for i in $(seq 1 $MAX_ATTEMPTS); do
            ADMIN_PORT=$((ADMIN_PORT + 1))
            if ! port_in_use $ADMIN_PORT; then
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
        if port_in_use $ADMIN_PORT; then
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

    if port_in_use $ADMIN_PORT; then
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

# Largura interna do box (entre as bordas)
BOX_W=50
# Padding helper: preenche até a borda direita
pad() {
    local text="$1"
    local len=${#text}
    local spaces=$((BOX_W - 2 - len))  # -2 para margem esquerda "   "
    [ "$spaces" -lt 0 ] && spaces=0
    printf '%*s' "$spaces" ''
}

echo ""
echo ""
echo -e "  ${G}${B}╭$(printf '%.0s─' $(seq 1 $BOX_W))╮${N}"
echo -e "  ${G}${B}│${N}$(printf '%*s' $BOX_W '')${G}${B}│${N}"
echo -e "  ${G}${B}│${N}   ${OK} ${B}${W}Setup concluído!${N}$(pad "✔ Setup concluído!")${G}${B}│${N}"
echo -e "  ${G}${B}│${N}$(printf '%*s' $BOX_W '')${G}${B}│${N}"

URL_LABEL="Abra no navegador:"
echo -e "  ${G}${B}│${N}   ${D}${URL_LABEL}${N}$(pad "$URL_LABEL")${G}${B}│${N}"
echo -e "  ${G}${B}│${N}   ${C}${B}${URL}${N}$(pad "$URL")${G}${B}│${N}"
echo -e "  ${G}${B}│${N}$(printf '%*s' $BOX_W '')${G}${B}│${N}"

if [ "$BOT_COUNT" -eq 0 ]; then
    WIZ_TEXT="O setup wizard abrirá automaticamente."
    echo -e "  ${G}${B}│${N}   ${Y}${WIZ_TEXT}${N}$(pad "$WIZ_TEXT")${G}${B}│${N}"
else
    BOT_TEXT="${BOT_COUNT} bot(s) configurado(s), ${ACTIVE_COUNT} online"
    echo -e "  ${G}${B}│${N}   ${D}${BOT_TEXT}${N}$(pad "$BOT_TEXT")${G}${B}│${N}"
fi

echo -e "  ${G}${B}│${N}$(printf '%*s' $BOX_W '')${G}${B}│${N}"
echo -e "  ${G}${B}╰$(printf '%.0s─' $(seq 1 $BOX_W))╯${N}"

# Aviso de Docker: porta pode não estar exposta no host
if [ "$IN_DOCKER" = true ]; then
    CONTAINER_ID=$(cat /proc/self/cgroup 2>/dev/null | grep -oP '[a-f0-9]{64}' | head -1)
    SHORT_ID="${CONTAINER_ID:0:12}"
    CONTAINER_NAME=$(hostname 2>/dev/null)
    echo ""
    echo -e "  ${Y}${B}Docker detectado${N}"
    echo -e "  ${D}$(printf '%.0s─' $(seq 1 50))${N}"
    echo -e "  ${D}O painel está rodando dentro do container, mas a${N}"
    echo -e "  ${D}porta ${B}${ADMIN_PORT}${N}${D} pode não estar exposta no host.${N}"
    echo ""
    echo -e "  ${D}Se não conseguir acessar, rode no ${B}host${N}${D}:${N}"
    echo ""
    echo -e "  ${C}  # Parar e recriar com a porta exposta${N}"
    echo -e "  ${C}  docker stop ${SHORT_ID:-\$CONTAINER}${N}"
    echo -e "  ${C}  docker commit ${SHORT_ID:-\$CONTAINER} smb-claw:latest${N}"
    echo -e "  ${C}  docker run -d -p ${ADMIN_PORT}:${ADMIN_PORT} -p 2222:22 \\${N}"
    echo -e "  ${C}    --name smb-claw smb-claw:latest${N}"
    echo ""
    echo -e "  ${D}Ou use um docker-compose.yml com:${N}"
    echo -e "  ${C}  ports:${N}"
    echo -e "  ${C}    - \"${ADMIN_PORT}:${ADMIN_PORT}\"${N}"
    echo -e "  ${D}$(printf '%.0s─' $(seq 1 50))${N}"
fi

echo ""
