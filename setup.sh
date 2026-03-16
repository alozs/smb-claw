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
FORCE_CONFIG=false
[ "$1" = "--config" ] || [ "$1" = "--reconfig" ] || [ "$1" = "-c" ] && FORCE_CONFIG=true

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

# ── OAuth PKCE helper ──────────────────────────────────────────────────────
# Uso: do_oauth <provider_label> <authorize_url> <token_url> <client_id> <redirect_uri> <scopes> <extra_params> <token_file> <token_format>
# token_format: "codex" ou "claude"
do_oauth() {
    local label="$1" auth_url="$2" token_url="$3" client_id="$4" redirect_uri="$5" scopes="$6" extra_params="$7" token_file="$8" token_format="$9"

    echo ""
    echo -e "  ${C}${B}◇ OAuth — Autenticação via ${label}${N}"
    echo ""

    # Gerar PKCE code_verifier e code_challenge
    CODE_VERIFIER=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null)
    CODE_CHALLENGE=$(python3 -c "
import hashlib, base64
v = '$CODE_VERIFIER'
digest = hashlib.sha256(v.encode()).digest()
print(base64.urlsafe_b64encode(digest).rstrip(b'=').decode())
" 2>/dev/null)
    STATE=$(python3 -c "import secrets; print(secrets.token_hex(16))" 2>/dev/null)

    # Encode redirect_uri
    ENCODED_REDIRECT=$(python3 -c "from urllib.parse import quote; print(quote('$redirect_uri', safe=''))" 2>/dev/null)
    ENCODED_SCOPES=$(echo "$scopes" | tr ' ' '+')

    OAUTH_URL="${auth_url}?response_type=code&client_id=${client_id}&redirect_uri=${ENCODED_REDIRECT}&scope=${ENCODED_SCOPES}&code_challenge=${CODE_CHALLENGE}&code_challenge_method=S256&state=${STATE}"
    [ -n "$extra_params" ] && OAUTH_URL="${OAUTH_URL}&${extra_params}"

    echo -e "  ${D}Abra esta URL no seu navegador LOCAL:${N}"
    echo ""
    echo -e "  ${C}${OAUTH_URL}${N}"
    echo ""
    echo -e "  ${D}Após autorizar, o navegador vai redirecionar para${N}"
    echo -e "  ${D}um endereço localhost que vai falhar — isso é normal.${N}"
    echo -e "  ${D}Copie a URL completa da barra de endereços e cole aqui.${N}"
    echo ""
    read -rp "  Cole a URL de redirect: " REDIRECT_URL

    if [ -z "$REDIRECT_URL" ]; then
        echo -e "  ${WARN} OAuth ignorado — configure depois"
        return 1
    fi

    # Extrair code da URL
    AUTH_CODE=$(python3 -c "
from urllib.parse import urlparse, parse_qs
import sys
url = sys.stdin.read().strip()
qs = parse_qs(urlparse(url).query)
print(qs.get('code', [''])[0])
" <<< "$REDIRECT_URL" 2>/dev/null)

    if [ -z "$AUTH_CODE" ]; then
        echo -e "  ${FAIL} Não foi possível extrair o código da URL"
        echo -e "  ${WARN} Continue sem OAuth — configure depois"
        return 1
    fi

    echo -ne "  ${C}⠋${N} Trocando código por token..."

    TOKEN_RESPONSE=$(python3 -c "
import urllib.request, urllib.parse, json, sys
data = urllib.parse.urlencode({
    'grant_type': 'authorization_code',
    'code': '$AUTH_CODE',
    'redirect_uri': '$redirect_uri',
    'client_id': '$client_id',
    'code_verifier': '$CODE_VERIFIER'
}).encode()
req = urllib.request.Request('$token_url', data=data,
    headers={'Content-Type': 'application/x-www-form-urlencoded'})
try:
    resp = urllib.request.urlopen(req, timeout=15)
    print(resp.read().decode())
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null)

    if echo "$TOKEN_RESPONSE" | grep -q "ERROR:"; then
        echo -e "\r  ${FAIL} Erro na troca do token OAuth              "
        echo -e "  ${D}${TOKEN_RESPONSE}${N}"
        echo -e "  ${WARN} Continue sem OAuth — configure depois"
        return 1
    fi

    # Salvar token no formato correto
    local token_dir
    token_dir=$(dirname "$token_file")
    mkdir -p "$token_dir"

    if [ "$token_format" = "claude" ]; then
        python3 -c "
import json, sys
tokens = json.loads(sys.stdin.read())
auth = {
    'claudeAiOauth': {
        'accessToken': tokens.get('access_token', ''),
        'refreshToken': tokens.get('refresh_token', ''),
        'expiresAt': tokens.get('expires_in', 0)
    }
}
with open('$token_file', 'w') as f:
    json.dump(auth, f, indent=2)
" <<< "$TOKEN_RESPONSE" 2>/dev/null
    else
        python3 -c "
import json, sys
from datetime import datetime
tokens = json.loads(sys.stdin.read())
auth = {
    'auth_mode': 'chatgpt',
    'tokens': {
        'access_token': tokens.get('access_token', ''),
        'id_token': tokens.get('id_token', ''),
        'refresh_token': tokens.get('refresh_token', '')
    },
    'last_refresh': datetime.now().isoformat()
}
with open('$token_file', 'w') as f:
    json.dump(auth, f, indent=2)
" <<< "$TOKEN_RESPONSE" 2>/dev/null
    fi

    chmod 600 "$token_file"
    echo -e "\r  ${OK} OAuth configurado com sucesso!              "
    echo -e "  ${D}Token salvo em ${token_file}${N}"
    return 0
}

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6: Setup Wizard (CLI) — se config.global não existe
# ══════════════════════════════════════════════════════════════════════════════

NEEDS_SETUP=false
if [ "$FORCE_CONFIG" = true ]; then
    NEEDS_SETUP=true
elif [ ! -f "$BASE_DIR/config.global" ]; then
    NEEDS_SETUP=true
elif ! grep -q "^PROVIDER=.\+" "$BASE_DIR/config.global" 2>/dev/null; then
    NEEDS_SETUP=true
elif ! grep -q "^ADMIN_ID=[0-9]\+" "$BASE_DIR/config.global" 2>/dev/null; then
    NEEDS_SETUP=true
fi

# Carregar valores atuais (se existem) como defaults
CUR_PROVIDER=""
CUR_MODEL=""
CUR_ADMIN_ID=""
CUR_ACCESS=""
CUR_ANTHROPIC_KEY=""
CUR_OPENAI_KEY=""
CUR_OPENROUTER_KEY=""
if [ -f "$BASE_DIR/config.global" ]; then
    CUR_PROVIDER=$(grep "^PROVIDER=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2-)
    CUR_MODEL=$(grep "^MODEL=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2-)
    CUR_ADMIN_ID=$(grep "^ADMIN_ID=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2-)
    CUR_ACCESS=$(grep "^ACCESS_MODE=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2-)
fi
if [ -f "$BASE_DIR/secrets.global" ]; then
    CUR_ANTHROPIC_KEY=$(grep "^ANTHROPIC_API_KEY=" "$BASE_DIR/secrets.global" 2>/dev/null | cut -d= -f2-)
    CUR_OPENAI_KEY=$(grep "^OPENAI_API_KEY=" "$BASE_DIR/secrets.global" 2>/dev/null | cut -d= -f2-)
    CUR_OPENROUTER_KEY=$(grep "^OPENROUTER_API_KEY=" "$BASE_DIR/secrets.global" 2>/dev/null | cut -d= -f2-)
fi

if [ "$NEEDS_SETUP" = true ]; then
    if [ "$FORCE_CONFIG" = true ] && [ -f "$BASE_DIR/config.global" ]; then
        step_header "Editar configuração"
        echo ""
        echo -e "  ${D}Valores atuais mostrados entre parênteses.${N}"
        echo -e "  ${D}Pressione Enter para manter o valor atual.${N}"
    else
        step_header "Configuração inicial"
        echo ""
        echo -e "  ${D}Nenhuma configuração encontrada. Vamos configurar${N}"
        echo -e "  ${D}o básico para começar.${N}"
    fi

    # ── Provedor ────────────────────────────────────────────────────────────
    # Mapear provider atual para número
    CUR_PROV_NUM="1"
    case "$CUR_PROVIDER" in
        openrouter) CUR_PROV_NUM="2" ;;
        codex)      CUR_PROV_NUM="3" ;;
    esac
    CUR_PROV_LABEL=""
    [ -n "$CUR_PROVIDER" ] && CUR_PROV_LABEL=" ${D}(atual: ${CUR_PROVIDER})${N}"

    echo ""
    echo -e "  ${B}Provedor de IA:${N}${CUR_PROV_LABEL}"
    echo -e "  ${D}  1) Anthropic   — Claude (OAuth + API key)${N}"
    echo -e "  ${D}  2) OpenRouter  — Qualquer modelo via OpenRouter${N}"
    echo -e "  ${D}  3) OpenAI      — GPT / Codex (OAuth + API key)${N}"
    echo ""
    read -rp "  Escolha [1-3] (padrão: ${CUR_PROV_NUM}): " PROV_CHOICE
    PROV_CHOICE="${PROV_CHOICE:-$CUR_PROV_NUM}"
    case "$PROV_CHOICE" in
        2) WIZ_PROVIDER="openrouter" ;;
        3) WIZ_PROVIDER="codex" ;;
        *) WIZ_PROVIDER="anthropic" ;;
    esac
    echo -e "  ${OK} Provedor: ${B}${WIZ_PROVIDER}${N}"

    # ── API Key / OAuth (se necessário) ─────────────────────────────────────
    WIZ_ANTHROPIC_KEY=""
    WIZ_OPENROUTER_KEY=""
    WIZ_OPENAI_KEY=""

    if [ "$WIZ_PROVIDER" = "anthropic" ]; then
        # Verifica se já tem OAuth do Claude configurado
        CLAUDE_HAS_TOKEN=""
        if [ -f "$HOME/.claude/.credentials.json" ]; then
            CLAUDE_HAS_TOKEN=$(python3 -c "
import json
with open('$HOME/.claude/.credentials.json') as f:
    c = json.load(f)
print('ok' if c.get('claudeAiOauth',{}).get('accessToken','') else '')
" 2>/dev/null)
        fi
        if [ "$CLAUDE_HAS_TOKEN" = "ok" ]; then
            echo -e "  ${OK} Claude OAuth já configurado"
        else
            echo ""
            echo -e "  ${B}Autenticação Anthropic:${N}"
            echo -e "  ${D}  1) Claude OAuth (abrir URL no navegador — sem API key)${N}"
            echo -e "  ${D}  2) Anthropic API Key${N}"
            echo -e "  ${D}  3) Já tenho o Claude Code CLI instalado e logado${N}"
            echo ""
            read -rp "  Escolha [1-3] (padrão: 1): " ANTH_AUTH_CHOICE
            ANTH_AUTH_CHOICE="${ANTH_AUTH_CHOICE:-1}"

            if [ "$ANTH_AUTH_CHOICE" = "2" ]; then
                read -rp "  Anthropic API Key (sk-ant-...): " WIZ_ANTHROPIC_KEY
                if [ -z "$WIZ_ANTHROPIC_KEY" ]; then
                    echo -e "  ${WARN} Nenhuma key informada — configure depois em secrets.global"
                fi
            elif [ "$ANTH_AUTH_CHOICE" = "3" ]; then
                echo -e "  ${D}OK — certifique-se de rodar ${B}claude login${N}${D} antes de iniciar os bots${N}"
            else
                do_oauth "Claude" \
                    "https://auth.anthropic.com/oauth/authorize" \
                    "https://auth.anthropic.com/oauth/token" \
                    "d912a2d4-0544-4661-8498-7638e8196c55" \
                    "http://localhost:18217/oauth/callback" \
                    "user:inference" \
                    "" \
                    "$HOME/.claude/.credentials.json" \
                    "claude"
            fi
        fi

        # Se usou OAuth (não API key), seta provider como claude-cli
        if [ -z "$WIZ_ANTHROPIC_KEY" ]; then
            WIZ_PROVIDER="claude-cli"
        fi

    elif [ "$WIZ_PROVIDER" = "openrouter" ]; then
        echo ""
        read -rp "  OpenRouter API Key (sk-or-...): " WIZ_OPENROUTER_KEY
        if [ -z "$WIZ_OPENROUTER_KEY" ]; then
            echo -e "  ${WARN} Nenhuma key informada — configure depois em secrets.global"
        fi

    elif [ "$WIZ_PROVIDER" = "codex" ]; then
        # Verifica se já tem OAuth do Codex configurado
        CODEX_HAS_TOKEN=""
        if [ -f "$HOME/.codex/auth.json" ]; then
            CODEX_HAS_TOKEN=$(python3 -c "
import json
with open('$HOME/.codex/auth.json') as f:
    c = json.load(f)
print('ok' if c.get('tokens',{}).get('access_token','') else '')
" 2>/dev/null)
        fi
        if [ "$CODEX_HAS_TOKEN" = "ok" ]; then
            echo -e "  ${OK} OpenAI Codex OAuth já configurado"
        else
            echo ""
            echo -e "  ${B}Autenticação OpenAI:${N}"
            echo -e "  ${D}  1) OpenAI Codex OAuth (ChatGPT OAuth — sem API key)${N}"
            echo -e "  ${D}  2) OpenAI API Key${N}"
            echo -e "  ${D}  3) Já tenho o Codex CLI instalado e logado${N}"
            echo ""
            read -rp "  Escolha [1-3] (padrão: 1): " OPENAI_AUTH_CHOICE
            OPENAI_AUTH_CHOICE="${OPENAI_AUTH_CHOICE:-1}"

            if [ "$OPENAI_AUTH_CHOICE" = "2" ]; then
                read -rp "  OpenAI API Key (sk-...): " WIZ_OPENAI_KEY
                if [ -z "$WIZ_OPENAI_KEY" ]; then
                    echo -e "  ${WARN} Nenhuma key informada — configure depois em secrets.global"
                fi
            elif [ "$OPENAI_AUTH_CHOICE" = "3" ]; then
                echo -e "  ${D}OK — certifique-se de rodar ${B}codex login${N}${D} antes de iniciar os bots${N}"
            else
                do_oauth "ChatGPT" \
                    "https://auth.openai.com/oauth/authorize" \
                    "https://auth.openai.com/oauth/token" \
                    "app_EMoamEEZ73f0CkXaXp7hrann" \
                    "http://localhost:1455/auth/callback" \
                    "openid profile email offline_access" \
                    "id_token_add_organizations=true&codex_cli_simplified_flow=true&originator=pi" \
                    "$HOME/.codex/auth.json" \
                    "codex"
            fi
        fi
    fi

    # ── Modelo ──────────────────────────────────────────────────────────────
    echo ""
    case "$WIZ_PROVIDER" in
        claude-cli|anthropic)
            echo -e "  ${B}Modelo (Anthropic):${N}"
            echo -e "  ${D}   1) anthropic/claude-sonnet-4-6 ${C}(recomendado)${N}"
            echo -e "  ${D}   2) anthropic/claude-sonnet-4-5${N}"
            echo -e "  ${D}   3) anthropic/claude-sonnet-4-5-20250929${N}"
            echo -e "  ${D}   4) anthropic/claude-sonnet-4-0${N}"
            echo -e "  ${D}   5) anthropic/claude-sonnet-4-20250514${N}"
            echo -e "  ${D}   6) anthropic/claude-opus-4-6${N}"
            echo -e "  ${D}   7) anthropic/claude-opus-4-5${N}"
            echo -e "  ${D}   8) anthropic/claude-opus-4-5-20251101${N}"
            echo -e "  ${D}   9) anthropic/claude-opus-4-1${N}"
            echo -e "  ${D}  10) anthropic/claude-opus-4-1-20250805${N}"
            echo -e "  ${D}  11) anthropic/claude-opus-4-0${N}"
            echo -e "  ${D}  12) anthropic/claude-opus-4-20250514${N}"
            echo -e "  ${D}  13) anthropic/claude-haiku-4-5${N}"
            echo -e "  ${D}  14) anthropic/claude-haiku-4-5-20251001${N}"
            echo -e "  ${D}  15) Digitar manualmente${N}"
            echo ""
            read -rp "  Escolha [1-15] (padrão: 1): " MODEL_CHOICE
            case "$MODEL_CHOICE" in
                2)  WIZ_MODEL="claude-sonnet-4-5" ;;
                3)  WIZ_MODEL="claude-sonnet-4-5-20250929" ;;
                4)  WIZ_MODEL="claude-sonnet-4-0" ;;
                5)  WIZ_MODEL="claude-sonnet-4-20250514" ;;
                6)  WIZ_MODEL="claude-opus-4-6" ;;
                7)  WIZ_MODEL="claude-opus-4-5" ;;
                8)  WIZ_MODEL="claude-opus-4-5-20251101" ;;
                9)  WIZ_MODEL="claude-opus-4-1" ;;
                10) WIZ_MODEL="claude-opus-4-1-20250805" ;;
                11) WIZ_MODEL="claude-opus-4-0" ;;
                12) WIZ_MODEL="claude-opus-4-20250514" ;;
                13) WIZ_MODEL="claude-haiku-4-5" ;;
                14) WIZ_MODEL="claude-haiku-4-5-20251001" ;;
                15)
                    read -rp "  Modelo: " WIZ_MODEL
                    WIZ_MODEL="${WIZ_MODEL:-claude-sonnet-4-6}"
                    ;;
                *) WIZ_MODEL="claude-sonnet-4-6" ;;
            esac
            ;;
        openrouter)
            echo -e "  ${B}Modelo (OpenRouter):${N}"
            echo -e "  ${D}  1) anthropic/claude-sonnet-4-6 ${C}(recomendado)${N}"
            echo -e "  ${D}  2) x-ai/grok-3${N}"
            echo -e "  ${D}  3) google/gemini-2.0-flash${N}"
            echo -e "  ${D}  4) openai/gpt-4o${N}"
            echo -e "  ${D}  5) Digitar manualmente${N}"
            echo ""
            read -rp "  Escolha [1-5] (padrão: 1): " MODEL_CHOICE
            case "$MODEL_CHOICE" in
                2) WIZ_MODEL="x-ai/grok-3" ;;
                3) WIZ_MODEL="google/gemini-2.0-flash" ;;
                4) WIZ_MODEL="openai/gpt-4o" ;;
                5)
                    read -rp "  Modelo (ex: mistralai/mistral-small-3.1): " WIZ_MODEL
                    WIZ_MODEL="${WIZ_MODEL:-anthropic/claude-sonnet-4-6}"
                    ;;
                *) WIZ_MODEL="anthropic/claude-sonnet-4-6" ;;
            esac
            ;;
        codex)
            echo -e "  ${B}Modelo (OpenAI):${N}"
            echo -e "  ${D}  1) gpt-5.4 ${C}(recomendado)${N}"
            echo -e "  ${D}  2) gpt-5.3-codex-spark${N}"
            echo -e "  ${D}  3) gpt-5.3-codex${N}"
            echo -e "  ${D}  4) gpt-5.2-codex${N}"
            echo -e "  ${D}  5) gpt-5.2${N}"
            echo -e "  ${D}  6) gpt-5.1-codex-max${N}"
            echo -e "  ${D}  7) gpt-5.1-codex-mini${N}"
            echo -e "  ${D}  8) gpt-5.1${N}"
            echo -e "  ${D}  9) Digitar manualmente${N}"
            echo ""
            read -rp "  Escolha [1-9] (padrão: 1): " MODEL_CHOICE
            case "$MODEL_CHOICE" in
                2) WIZ_MODEL="gpt-5.3-codex-spark" ;;
                3) WIZ_MODEL="gpt-5.3-codex" ;;
                4) WIZ_MODEL="gpt-5.2-codex" ;;
                5) WIZ_MODEL="gpt-5.2" ;;
                6) WIZ_MODEL="gpt-5.1-codex-max" ;;
                7) WIZ_MODEL="gpt-5.1-codex-mini" ;;
                8) WIZ_MODEL="gpt-5.1" ;;
                9)
                    read -rp "  Modelo: " WIZ_MODEL
                    WIZ_MODEL="${WIZ_MODEL:-gpt-5.4}"
                    ;;
                *) WIZ_MODEL="gpt-5.4" ;;
            esac
            ;;
    esac
    echo -e "  ${OK} Modelo: ${B}${WIZ_MODEL}${N}"

    # ── Admin ID ────────────────────────────────────────────────────────────
    CUR_ADMIN_LABEL=""
    [ -n "$CUR_ADMIN_ID" ] && [ "$CUR_ADMIN_ID" != "auto" ] && [ "$CUR_ADMIN_ID" != "0" ] \
        && CUR_ADMIN_LABEL=" ${D}(atual: ${CUR_ADMIN_ID})${N}"

    echo ""
    echo -e "  ${B}Telegram Admin ID:${N}${CUR_ADMIN_LABEL}"
    echo -e "  ${D}  Quem será o administrador dos bots.${N}"
    echo -e "  ${D}  Se já sabe seu ID, digite abaixo.${N}"
    echo -e "  ${D}  Se não sabe, deixe vazio — a primeira pessoa${N}"
    echo -e "  ${D}  a enviar /start no bot será definida como admin.${N}"
    echo ""
    if [ -n "$CUR_ADMIN_ID" ] && [ "$CUR_ADMIN_ID" != "auto" ] && [ "$CUR_ADMIN_ID" != "0" ]; then
        read -rp "  Admin ID (Enter = manter ${CUR_ADMIN_ID}): " WIZ_ADMIN_ID
        WIZ_ADMIN_ID="${WIZ_ADMIN_ID:-$CUR_ADMIN_ID}"
    else
        read -rp "  Admin ID (Enter = auto-detectar): " WIZ_ADMIN_ID
    fi
    if [ -z "$WIZ_ADMIN_ID" ]; then
        WIZ_ADMIN_ID="auto"
        echo -e "  ${OK} Admin será definido automaticamente no primeiro /start"
    elif [[ "$WIZ_ADMIN_ID" =~ ^[0-9]+$ ]]; then
        echo -e "  ${OK} Admin ID: ${B}${WIZ_ADMIN_ID}${N}"
    else
        while ! [[ "$WIZ_ADMIN_ID" =~ ^[0-9]+$ ]]; do
            echo -e "  ${WARN} ID deve conter apenas números"
            read -rp "  Admin ID: " WIZ_ADMIN_ID
        done
        echo -e "  ${OK} Admin ID: ${B}${WIZ_ADMIN_ID}${N}"
    fi

    # ── Modo de acesso ──────────────────────────────────────────────────────
    CUR_ACCESS_NUM="1"
    case "$CUR_ACCESS" in
        open)   CUR_ACCESS_NUM="2" ;;
        closed) CUR_ACCESS_NUM="3" ;;
    esac
    CUR_ACCESS_LABEL=""
    [ -n "$CUR_ACCESS" ] && CUR_ACCESS_LABEL=" ${D}(atual: ${CUR_ACCESS})${N}"

    echo ""
    echo -e "  ${B}Modo de acesso:${N}${CUR_ACCESS_LABEL}"
    echo -e "  ${D}  1) approval — novos usuários precisam de aprovação (recomendado)${N}"
    echo -e "  ${D}  2) open     — qualquer pessoa pode usar o bot${N}"
    echo -e "  ${D}  3) closed   — somente o admin pode usar${N}"
    echo ""
    read -rp "  Escolha [1-3] (padrão: ${CUR_ACCESS_NUM}): " ACCESS_CHOICE
    ACCESS_CHOICE="${ACCESS_CHOICE:-$CUR_ACCESS_NUM}"
    case "$ACCESS_CHOICE" in
        2) WIZ_ACCESS="open" ;;
        3) WIZ_ACCESS="closed" ;;
        *) WIZ_ACCESS="approval" ;;
    esac
    echo -e "  ${OK} Modo de acesso: ${B}${WIZ_ACCESS}${N}"

    # ── Salvar config.global ────────────────────────────────────────────────
    # Preservar valores do bugfixer se existem
    CUR_BF_ENABLED=$(grep "^BUGFIXER_ENABLED=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2- || echo "false")
    CUR_BF_TIMES=$(grep "^BUGFIXER_TIMES_PER_DAY=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2- || echo "1")
    CUR_BF_TOKEN=$(grep "^BUGFIXER_TELEGRAM_TOKEN=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2- || echo "")
    CUR_PANEL_URL=$(grep "^ADMIN_PANEL_URL=" "$BASE_DIR/config.global" 2>/dev/null | cut -d= -f2- || echo "")

    cat > "$BASE_DIR/config.global" << GCEOF
PROVIDER=$WIZ_PROVIDER
ADMIN_ID=$WIZ_ADMIN_ID
MODEL=$WIZ_MODEL
ACCESS_MODE=$WIZ_ACCESS
BUGFIXER_ENABLED=${CUR_BF_ENABLED:-false}
BUGFIXER_TIMES_PER_DAY=${CUR_BF_TIMES:-1}
BUGFIXER_TELEGRAM_TOKEN=${CUR_BF_TOKEN}
ADMIN_PANEL_URL=${CUR_PANEL_URL}
GCEOF
    echo ""
    echo -e "  ${OK} config.global salvo"

    # ── Salvar secrets.global ───────────────────────────────────────────────
    # Usa valor novo se informado, senão mantém o existente
    SAVE_ANTHROPIC="${WIZ_ANTHROPIC_KEY:-$CUR_ANTHROPIC_KEY}"
    SAVE_OPENAI="${WIZ_OPENAI_KEY:-$CUR_OPENAI_KEY}"
    SAVE_OPENROUTER="${WIZ_OPENROUTER_KEY:-$CUR_OPENROUTER_KEY}"

    {
        echo "ANTHROPIC_API_KEY=${SAVE_ANTHROPIC}"
        echo "OPENAI_API_KEY=${SAVE_OPENAI}"
        echo "OPENROUTER_API_KEY=${SAVE_OPENROUTER}"
    } > "$BASE_DIR/secrets.global"
    chmod 600 "$BASE_DIR/secrets.global"
    echo -e "  ${OK} secrets.global salvo ${D}(chmod 600)${N}"

    # ── Criar primeiro bot? (só se não tem bots ainda) ──────────────────────
    EXISTING_BOTS=$(find "$BASE_DIR/bots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
    CREATE_BOT="n"
    if [ "$EXISTING_BOTS" -eq 0 ]; then
        echo ""
        read -rp "  Deseja criar o primeiro bot agora? [S/n] " CREATE_BOT
        CREATE_BOT="${CREATE_BOT:-S}"
    fi

    if [[ "$CREATE_BOT" =~ ^[SsYy]$ ]]; then
        echo ""
        echo -e "  ${B}Nome do bot:${N}"
        echo -e "  ${D}  Use letras minúsculas, sem espaços (ex: assistente)${N}"
        read -rp "  Nome: " WIZ_BOT_NAME
        WIZ_BOT_NAME=$(echo "$WIZ_BOT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')
        while [ -z "$WIZ_BOT_NAME" ]; do
            echo -e "  ${WARN} Nome inválido"
            read -rp "  Nome: " WIZ_BOT_NAME
            WIZ_BOT_NAME=$(echo "$WIZ_BOT_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')
        done

        echo ""
        echo -e "  ${B}Token do Telegram:${N}"
        echo -e "  ${D}  Obtenha em @BotFather → /newbot${N}"
        echo -e "  ${D}  (Enter para configurar depois)${N}"
        read -rp "  Token: " WIZ_BOT_TOKEN
        WIZ_BOT_TOKEN="${WIZ_BOT_TOKEN:-SEU_TOKEN_AQUI}"

        echo ""
        echo -e "  ${B}Ferramentas:${N}"
        echo -e "  ${D}  1) Completo   — shell, cron, files, http, git, github, database (recomendado)${N}"
        echo -e "  ${D}  2) Básico     — shell, files, http${N}"
        echo -e "  ${D}  3) Nenhuma    — apenas conversa${N}"
        echo ""
        read -rp "  Escolha [1-3] (padrão: 1): " TOOLS_CHOICE
        case "$TOOLS_CHOICE" in
            2) WIZ_TOOLS="shell,files,http" ;;
            3) WIZ_TOOLS="none" ;;
            *) WIZ_TOOLS="shell,cron,files,http,git,github,database" ;;
        esac

        # Cria estrutura do bot
        WIZ_BOT_DIR="$BASE_DIR/bots/$WIZ_BOT_NAME"
        mkdir -p "$WIZ_BOT_DIR"/{memory,workspace}
        chmod 700 "$WIZ_BOT_DIR"

        cat > "$WIZ_BOT_DIR/.env" << BEOF
TELEGRAM_TOKEN=$WIZ_BOT_TOKEN
BOT_NAME=$WIZ_BOT_NAME
MAX_HISTORY=20
TOOLS=$WIZ_TOOLS
WORK_DIR=$WIZ_BOT_DIR/workspace
BEOF
        chmod 600 "$WIZ_BOT_DIR/.env"

        cat > "$WIZ_BOT_DIR/secrets.env" << 'BEOF'
# Credenciais sensíveis — NÃO versionar
# DB_URL=postgresql://usuario:senha@host:5432/banco
# GIT_TOKEN=
# GIT_USER=
# GIT_EMAIL=
# API_KEY_1=
BEOF
        chmod 600 "$WIZ_BOT_DIR/secrets.env"

        cat > "$WIZ_BOT_DIR/soul.md" << BEOF
# $WIZ_BOT_NAME

Você é $WIZ_BOT_NAME, um assistente especializado.

## Personalidade
- Seja direto e objetivo
- Responda sempre em português brasileiro

## Regras
- Registre na memória diária eventos relevantes da conversa
BEOF

        cat > "$WIZ_BOT_DIR/USER.md" << 'BEOF'
# Usuário

## Preferências de comunicação
- Tom: direto e objetivo
- Idioma: português brasileiro
BEOF
        chmod 600 "$WIZ_BOT_DIR/USER.md"

        cat > "$WIZ_BOT_DIR/MEMORY.md" << BEOF
# Memória de longo prazo — $WIZ_BOT_NAME

BEOF
        chmod 600 "$WIZ_BOT_DIR/MEMORY.md"

        # Serviço systemd (só se systemctl existir)
        if command -v systemctl &>/dev/null; then
            WIZ_SERVICE="claude-bot-$WIZ_BOT_NAME"
            sudo tee "/etc/systemd/system/$WIZ_SERVICE.service" > /dev/null << BEOF
[Unit]
Description=Claude Bot: $WIZ_BOT_NAME
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$BASE_DIR
EnvironmentFile=$WIZ_BOT_DIR/.env
ExecStart=python3 $BASE_DIR/bot.py --bot-dir $WIZ_BOT_DIR
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
BEOF
            sudo systemctl daemon-reload 2>/dev/null
        fi

        echo ""
        echo -e "  ${OK} Bot ${B}${WIZ_BOT_NAME}${N} criado em ${D}bots/${WIZ_BOT_NAME}/${N}"
        if [ "$WIZ_BOT_TOKEN" = "SEU_TOKEN_AQUI" ]; then
            echo -e "  ${WARN} Token não configurado — edite ${D}bots/${WIZ_BOT_NAME}/.env${N}"
        else
            # Iniciar o bot automaticamente
            echo ""
            if command -v systemctl &>/dev/null && [ -f "/etc/systemd/system/claude-bot-$WIZ_BOT_NAME.service" ]; then
                sudo systemctl enable --now "claude-bot-$WIZ_BOT_NAME" 2>/dev/null
                sleep 2
                if systemctl is-active --quiet "claude-bot-$WIZ_BOT_NAME" 2>/dev/null; then
                    echo -e "  ${OK} Bot ${B}${WIZ_BOT_NAME}${N} iniciado via systemd"
                else
                    echo -e "  ${FAIL} Falha ao iniciar — verifique: journalctl -u claude-bot-$WIZ_BOT_NAME -n 20"
                fi
            else
                # Sem systemd (Docker) — inicia direto em background
                cd "$BASE_DIR"
                nohup python3 "$BASE_DIR/bot.py" --bot-dir "$WIZ_BOT_DIR" \
                    > "$BASE_DIR/logs/${WIZ_BOT_NAME}.log" 2>&1 &
                BOT_PID=$!
                sleep 3
                if kill -0 "$BOT_PID" 2>/dev/null; then
                    echo -e "  ${OK} Bot ${B}${WIZ_BOT_NAME}${N} iniciado ${D}(PID: ${BOT_PID})${N}"
                    echo -e "  ${D}Log: tail -f $BASE_DIR/logs/${WIZ_BOT_NAME}.log${N}"
                else
                    echo -e "  ${FAIL} Falha ao iniciar o bot"
                    echo -e "  ${D}Verifique: cat $BASE_DIR/logs/${WIZ_BOT_NAME}.log${N}"
                fi
            fi
        fi
    fi

    echo ""
    echo -e "  ${D}$(printf '%.0s─' $(seq 1 50))${N}"
    echo -e "  ${OK} ${B}Configuração inicial concluída!${N}"
    echo -e "  ${D}$(printf '%.0s─' $(seq 1 50))${N}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# BOTS STATUS
# ══════════════════════════════════════════════════════════════════════════════

BOT_COUNT=$(find "$BASE_DIR/bots" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
ACTIVE_COUNT=0

if [ "$BOT_COUNT" -gt 0 ]; then
    step_header "Bots"
    for bot_dir in "$BASE_DIR/bots"/*/; do
        [ ! -d "$bot_dir" ] && continue
        bot_name=$(basename "$bot_dir")
        bot_env="$bot_dir/.env"

        # Verifica se o bot está rodando (systemd ou processo direto)
        BOT_RUNNING=false
        if command -v systemctl &>/dev/null && systemctl is-active --quiet "claude-bot-$bot_name" 2>/dev/null; then
            BOT_RUNNING=true
        elif pgrep -f "bot.py --bot-dir.*bots/$bot_name" >/dev/null 2>&1; then
            BOT_RUNNING=true
        fi

        if [ "$BOT_RUNNING" = true ]; then
            echo -e "  ${G}●${N} ${B}${bot_name}${N} ${D}— online${N}"
            ACTIVE_COUNT=$((ACTIVE_COUNT + 1))
        else
            # Tenta iniciar automaticamente se tem token configurado
            HAS_TOKEN=false
            if [ -f "$bot_env" ] && grep -q "^TELEGRAM_TOKEN=.\+" "$bot_env" 2>/dev/null; then
                TOKEN_VAL=$(grep "^TELEGRAM_TOKEN=" "$bot_env" 2>/dev/null | cut -d= -f2-)
                [ "$TOKEN_VAL" != "SEU_TOKEN_AQUI" ] && HAS_TOKEN=true
            fi

            if [ "$HAS_TOKEN" = true ]; then
                if command -v systemctl &>/dev/null && [ -f "/etc/systemd/system/claude-bot-$bot_name.service" ]; then
                    sudo systemctl start "claude-bot-$bot_name" 2>/dev/null
                    sleep 2
                    if systemctl is-active --quiet "claude-bot-$bot_name" 2>/dev/null; then
                        echo -e "  ${G}●${N} ${B}${bot_name}${N} ${D}— iniciado${N}"
                        ACTIVE_COUNT=$((ACTIVE_COUNT + 1))
                    else
                        echo -e "  ${R}●${N} ${bot_name} ${D}— falha ao iniciar${N}"
                    fi
                else
                    # Sem systemd (Docker) — inicia direto
                    mkdir -p "$BASE_DIR/logs"
                    cd "$BASE_DIR"
                    nohup python3 "$BASE_DIR/bot.py" --bot-dir "$bot_dir" \
                        > "$BASE_DIR/logs/${bot_name}.log" 2>&1 &
                    sleep 3
                    if pgrep -f "bot.py --bot-dir.*bots/$bot_name" >/dev/null 2>&1; then
                        echo -e "  ${G}●${N} ${B}${bot_name}${N} ${D}— iniciado${N}"
                        ACTIVE_COUNT=$((ACTIVE_COUNT + 1))
                    else
                        echo -e "  ${R}●${N} ${bot_name} ${D}— falha ao iniciar${N}"
                        echo -e "  ${D}    Log: cat $BASE_DIR/logs/${bot_name}.log${N}"
                    fi
                fi
            else
                echo -e "  ${D}○${N} ${bot_name} ${D}— sem token configurado${N}"
            fi
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
    local spaces=$((BOX_W - 3 - len))  # -3 para margem esquerda "   "
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
    # Detectar container ID (cgroups v1 e v2, hostname como fallback)
    CONTAINER_ID=$(cat /proc/self/cgroup 2>/dev/null | grep -oP '[a-f0-9]{64}' | head -1)
    [ -z "$CONTAINER_ID" ] && CONTAINER_ID=$(cat /proc/self/mountinfo 2>/dev/null | grep -oP '[a-f0-9]{64}' | head -1)
    [ -z "$CONTAINER_ID" ] && CONTAINER_ID=$(hostname 2>/dev/null)
    SHORT_ID="${CONTAINER_ID:0:12}"
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
