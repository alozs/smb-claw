#!/bin/bash
# install-crons.sh — Verifica e instala os crons do sistema (idempotente).
# Remove entradas obsoletas e garante que as entradas necessárias existam.
# Chamado pelo setup.sh e pelo update.sh.

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$BASE_DIR/logs"

if ! command -v crontab &>/dev/null; then
    echo "⚠️  crontab não encontrado, pulando instalação de crons"
    exit 0
fi

CHANGED=0

# ── Remove crons obsoletos ────────────────────────────────────────────────
_remove_cron() {
    local marker="$1"
    local label="$2"
    if crontab -l 2>/dev/null | grep -qF "$marker"; then
        crontab -l 2>/dev/null | grep -vF "$marker" | crontab -
        echo "  🗑  cron removido: $label"
        CHANGED=$((CHANGED + 1))
    fi
}

# behavior-extract agora é embutido no memory-autosave
_remove_cron "behavior-extract.sh" "behavior-extract (legado)"

# ── Instala crons ausentes ────────────────────────────────────────────────
_ensure_cron() {
    local entry="$1"
    local marker="$2"
    local label="$3"
    if ! crontab -l 2>/dev/null | grep -qF "$marker"; then
        (crontab -l 2>/dev/null; echo "$entry") | crontab -
        echo "  ✅ cron instalado: $label"
        CHANGED=$((CHANGED + 1))
    fi
}

_ensure_cron \
    "50 23 * * * $BASE_DIR/memory-autosave.sh >> $BASE_DIR/logs/memory-autosave.log 2>&1 # autosave de memória" \
    "memory-autosave.sh" \
    "memory-autosave (23:50)"

_ensure_cron \
    "0 2 * * 0 $BASE_DIR/memory-cleanup.sh 30 >> $BASE_DIR/logs/memory-cleanup.log 2>&1 # limpeza de memória" \
    "memory-cleanup.sh" \
    "memory-cleanup (dom 02:00)"

_ensure_cron \
    "0 8 * * * $BASE_DIR/check-update.sh >> $BASE_DIR/logs/check-update.log 2>&1 # verificação de atualização" \
    "check-update.sh" \
    "check-update (08:00)"

# ── Relatório ─────────────────────────────────────────────────────────────
if [ "$CHANGED" -eq 0 ]; then
    echo "  crons ok — nenhuma alteração necessária"
fi
