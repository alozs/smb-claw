#!/bin/bash
# Carrega todas as envs globais dos bots no ambiente atual.
# Use no início de qualquer script de cron:
#   source /caminho/para/load-envs.sh
#
# Ou em uma linha de cron:
#   * * * * * source /caminho/para/load-envs.sh && meu-script.sh

set -a  # exporta automaticamente tudo que for definido

_LOAD_ENVS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_load() {
    local file="$1"
    [ -f "$file" ] || return
    while IFS= read -r line; do
        [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
        [[ "$line" == *=* ]] && eval "export $line" 2>/dev/null || true
    done < "$file"
}

_load "$_LOAD_ENVS_DIR/config.global"
_load "$_LOAD_ENVS_DIR/secrets.global"

set +a
