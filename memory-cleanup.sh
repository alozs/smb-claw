#!/bin/bash
# Remove diários com mais de KEEP_DAYS dias de todos os bots.
# Uso: ./memory-cleanup.sh [dias_para_manter]  (padrão: 30)

KEEP_DAYS="${1:-30}"
BASE_DIR="/home/ubuntu/claude-bots"
COUNT=0

echo "=== Limpeza de memória diária (mantendo últimos $KEEP_DAYS dias) ==="

for mem_dir in "$BASE_DIR"/bots/*/memory/; do
    bot_name=$(basename "$(dirname "$mem_dir")")
    while IFS= read -r -d '' file; do
        rm -f "$file"
        echo "  [$bot_name] Removido: $(basename "$file")"
        COUNT=$((COUNT+1))
    done < <(find "$mem_dir" -name "*.md" -mtime "+$KEEP_DAYS" -print0 2>/dev/null)
done

# Limpa sessões arquivadas antigas do SQLite
echo ""
echo "=== Limpando sessions_archive (mantendo últimos $KEEP_DAYS dias) ==="
ARCHIVE_COUNT=0
for db_file in "$BASE_DIR"/bots/*/bot_data.db; do
    bot_name=$(basename "$(dirname "$db_file")")
    deleted=$(python3 -c "
import sqlite3, sys
from datetime import datetime, timedelta
conn = sqlite3.connect('$db_file')
cutoff = (datetime.now() - timedelta(days=$KEEP_DAYS)).isoformat()
cur = conn.execute('DELETE FROM sessions_archive WHERE archived_at < ?', (cutoff,))
conn.commit()
print(cur.rowcount)
conn.close()
" 2>/dev/null || echo 0)
    if [ "$deleted" -gt 0 ]; then
        echo "  [$bot_name] $deleted sessão(ões) arquivada(s) removida(s)"
        ARCHIVE_COUNT=$((ARCHIVE_COUNT + deleted))
    fi
done

echo ""
echo "✅ $COUNT arquivo(s) de diário removido(s), $ARCHIVE_COUNT sessão(ões) arquivada(s) removida(s)"
