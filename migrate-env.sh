#!/bin/bash
# migrate-env.sh — Adiciona variáveis novas ao .env de cada bot (se ausentes).
# Chamado automaticamente pelo update.sh após cada pull.
# Idempotente: só adiciona o que ainda não existe.
#
# Uso: ./migrate-env.sh [--quiet]

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
QUIET=false
[ "$1" = "--quiet" ] && QUIET=true

log() { [ "$QUIET" = false ] && echo "$1"; }

# ── Variáveis a garantir em cada bot ──────────────────────────────────────────
# Formato: "CHAVE=VALOR_PADRAO" e "CHAVE=#comentário de bloco"
# Linhas de comentário (começam com #) são inseridas antes do primeiro item do grupo.

declare -a MIGRATIONS=(
    "# Guardrails — notificação e bloqueio de ações de risco"
    "GUARDRAILS_ENABLED=true"
    "GUARDRAILS_MODE=notify"
    "GUARDRAILS_LEVEL=dangerous"
    "# Detecção de prompt injection (0.0 = desabilitado)"
    "INJECTION_THRESHOLD=0.7"
    "# Aprendizado comportamental (requer behavior-extract.sh no cron)"
    "BEHAVIOR_LEARNING_ENABLED=false"
    "BEHAVIOR_MAX_CHARS=2000"
)

BOTS_UPDATED=0
BOTS_SKIPPED=0

for bot_dir in "$BASE_DIR/bots/"/*/; do
    [ -d "$bot_dir" ] || continue
    env_file="$bot_dir/.env"
    [ -f "$env_file" ] || continue

    bot=$(basename "$bot_dir")
    added=0
    pending_comment=""

    for entry in "${MIGRATIONS[@]}"; do
        # Linha de comentário — guarda para inserir antes da próxima variável
        if [[ "$entry" == \#* ]]; then
            pending_comment="$entry"
            continue
        fi

        key="${entry%%=*}"

        # Verifica se a chave já existe (comentada ou não)
        if grep -q "^#\?${key}=" "$env_file" 2>/dev/null; then
            pending_comment=""
            continue
        fi

        # Adiciona comentário de bloco (uma única vez por grupo) e a variável
        if [ -n "$pending_comment" ]; then
            printf '\n%s\n' "$pending_comment" >> "$env_file"
            pending_comment=""
        fi
        printf '%s\n' "$entry" >> "$env_file"
        added=$((added + 1))
    done

    if [ $added -gt 0 ]; then
        log "  ✅ $bot — $added variável(eis) adicionada(s)"
        BOTS_UPDATED=$((BOTS_UPDATED + 1))
    else
        BOTS_SKIPPED=$((BOTS_SKIPPED + 1))
    fi
done

log "migrate-env: $BOTS_UPDATED bot(s) atualizado(s), $BOTS_SKIPPED já estavam ok"
