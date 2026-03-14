#!/bin/bash
# Carrega todas as envs globais dos bots no ambiente atual.
# Use no início de qualquer script de cron:
#   source /home/ubuntu/claude-bots/load-envs.sh
#
# Ou em uma linha de cron:
#   * * * * * source /home/ubuntu/claude-bots/load-envs.sh && meu-script.sh

set -a  # exporta automaticamente tudo que for definido

_load() {
    local file="$1"
    [ -f "$file" ] || return
    while IFS= read -r line; do
        [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
        [[ "$line" == *=* ]] && eval "export $line" 2>/dev/null || true
    done < "$file"
}

_load "/home/ubuntu/claude-bots/config.global"
_load "/home/ubuntu/claude-bots/secrets.global"

set +a
