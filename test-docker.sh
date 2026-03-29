#!/bin/bash
# test-docker.sh — Build, verifica instalação completa e limpa se OK.
# Uso: ./test-docker.sh [--keep]
#   --keep  mantém container e imagem mesmo se todos os testes passarem

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE="claude-bots-test"
CONTAINER="claude-bots-test-$$"
KEEP=false
[ "${1:-}" = "--keep" ] && KEEP=true

# ── Cores ─────────────────────────────────────────────────────────────────
G='\033[32m'; R='\033[31m'; Y='\033[33m'; C='\033[36m'; B='\033[1m'; N='\033[0m'
OK="${G}✔${N}"; FAIL="${R}✘${N}"; INFO="${C}→${N}"

PASS=0
FAIL_COUNT=0
declare -a ERRORS=()

check() {
    local label="$1"
    local result="$2"   # "ok" ou "fail: <motivo>"
    if [ "$result" = "ok" ]; then
        echo -e "  $OK  $label"
        PASS=$((PASS + 1))
    else
        echo -e "  $FAIL  $label — ${R}${result#fail: }${N}"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        ERRORS+=("$label: ${result#fail: }")
    fi
}

cleanup() {
    docker rm -f "$CONTAINER" > /dev/null 2>&1 || true
    docker rmi "$IMAGE" > /dev/null 2>&1 || true
}

echo ""
echo -e "${B}╔══════════════════════════════════════════╗${N}"
echo -e "${B}║      Claude Bots — Docker Test Suite     ║${N}"
echo -e "${B}╚══════════════════════════════════════════╝${N}"
echo ""

# ── 1. Build ───────────────────────────────────────────────────────────────
echo -e "${INFO} Buildando imagem ${C}${IMAGE}${N}..."
if docker build -f "$BASE_DIR/Dockerfile.test" -t "$IMAGE" "$BASE_DIR" > /tmp/docker-build.log 2>&1; then
    echo -e "  $OK  Build concluído"
else
    echo -e "  $FAIL  Build falhou:"
    tail -20 /tmp/docker-build.log
    exit 1
fi

# Inicia container em background (sem entrypoint blocking)
docker run -d --name "$CONTAINER" "$IMAGE" sleep 300 > /dev/null

echo ""
echo -e "${B}── Permissões ──────────────────────────────────${N}"

# ── 2. Scripts executáveis ────────────────────────────────────────────────
for script in setup.sh update.sh install-crons.sh migrate-env.sh memory-autosave.sh \
              memory-cleanup.sh behavior-extract.sh check-update.sh entrypoint.sh \
              criar-bot.sh gerenciar.sh release.sh; do
    result=$(docker exec "$CONTAINER" bash -c "[ -x /app/${script} ] && echo ok || echo 'fail: não executável'")
    check "$script é executável" "$result"
done

echo ""
echo -e "${B}── Testes unitários ────────────────────────────${N}"

# ── 3. Pytest ─────────────────────────────────────────────────────────────
if docker exec "$CONTAINER" bash -c "cd /app && python3 -m pytest tests/ -v --tb=short" > /tmp/pytest.log 2>&1; then
    passed=$(grep -c "PASSED" /tmp/pytest.log || true)
    check "pytest tests/ ($passed testes passaram)" "ok"
else
    failed_tests=$(grep "FAILED\|ERROR" /tmp/pytest.log | head -5 || true)
    check "pytest tests/" "fail: $failed_tests"
    echo "    Log completo em /tmp/pytest.log"
fi

echo ""
echo -e "${B}── Crons ───────────────────────────────────────${N}"

# ── 4. Cron daemon ────────────────────────────────────────────────────────
docker exec "$CONTAINER" bash -c "service cron start > /dev/null 2>&1 || true"

# ── 5. install-crons.sh ───────────────────────────────────────────────────
docker exec "$CONTAINER" bash -c "cd /app && bash install-crons.sh" > /tmp/crons.log 2>&1
for marker in "memory-autosave.sh" "memory-cleanup.sh" "check-update.sh"; do
    result=$(docker exec "$CONTAINER" bash -c "crontab -l 2>/dev/null | grep -qF '${marker}' && echo ok || echo 'fail: cron ausente'")
    check "cron instalado: $marker" "$result"
done

# ── 6. Nenhum cron obsoleto ───────────────────────────────────────────────
result=$(docker exec "$CONTAINER" bash -c "crontab -l 2>/dev/null | grep -qF 'behavior-extract.sh' && echo 'fail: cron legado presente' || echo ok")
check "cron legado behavior-extract ausente" "$result"

# ── 7. logs/ criado pelo install-crons ───────────────────────────────────
result=$(docker exec "$CONTAINER" bash -c "[ -d /app/logs ] && echo ok || echo 'fail: diretório logs/ não existe'")
check "diretório logs/ existe" "$result"

echo ""
echo -e "${B}── Admin panel ─────────────────────────────────${N}"

# ── 8. Admin panel sobe ───────────────────────────────────────────────────
docker exec "$CONTAINER" bash -c "
    mkdir -p /app/logs
    nohup uvicorn admin.app:app --host 0.0.0.0 --port 8080 >> /app/logs/admin.log 2>&1 &
    sleep 3
" > /dev/null 2>&1

result=$(docker exec "$CONTAINER" bash -c "
    curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/ 2>/dev/null | grep -qE '^(200|302|401|403)' && echo ok || echo 'fail: sem resposta HTTP'
")
check "admin panel responde na :8080" "$result"

echo ""
echo -e "${B}── migrate-env ─────────────────────────────────${N}"

# ── 9. migrate-env em bot de teste ───────────────────────────────────────
docker exec "$CONTAINER" bash -c "
    mkdir -p /app/bots/test-bot
    echo 'TELEGRAM_TOKEN=123:abc' > /app/bots/test-bot/.env
    echo 'BOT_NAME=test-bot' >> /app/bots/test-bot/.env
" > /dev/null

docker exec "$CONTAINER" bash -c "cd /app && bash migrate-env.sh --quiet" > /tmp/migrate.log 2>&1

for var in GUARDRAILS_ENABLED BEHAVIOR_LEARNING_ENABLED INJECTION_THRESHOLD; do
    result=$(docker exec "$CONTAINER" bash -c "grep -q '^${var}=' /app/bots/test-bot/.env && echo ok || echo 'fail: variável ausente'")
    check "migrate-env: $var adicionado" "$result"
done

result=$(docker exec "$CONTAINER" bash -c "grep '^BEHAVIOR_LEARNING_ENABLED=' /app/bots/test-bot/.env | grep -q 'true' && echo ok || echo 'fail: valor não é true'")
check "migrate-env: BEHAVIOR_LEARNING_ENABLED=true" "$result"

echo ""
echo -e "${B}── memory-autosave encadeia behavior-extract ───${N}"

# ── 10. memory-autosave chama behavior-extract ────────────────────────────
result=$(docker exec "$CONTAINER" bash -c "grep -q 'behavior-extract.sh' /app/memory-autosave.sh && echo ok || echo 'fail: chamada ausente'")
check "memory-autosave.sh chama behavior-extract.sh" "$result"

# ── Resultado final ────────────────────────────────────────────────────────
echo ""
echo -e "${B}══════════════════════════════════════════════════${N}"
TOTAL=$((PASS + FAIL_COUNT))
if [ "$FAIL_COUNT" -eq 0 ]; then
    echo -e "  ${G}${B}PASSOU — $PASS/$TOTAL testes OK${N}"
    echo ""
    if [ "$KEEP" = false ]; then
        echo -e "  ${INFO} Removendo container e imagem..."
        cleanup
        echo -e "  $OK  Limpeza concluída"
    else
        echo -e "  ${Y}--keep ativo: container ${CONTAINER} mantido${N}"
    fi
    echo ""
    exit 0
else
    echo -e "  ${R}${B}FALHOU — $FAIL_COUNT falha(s) de $TOTAL testes${N}"
    echo ""
    for err in "${ERRORS[@]}"; do
        echo -e "  ${R}•${N} $err"
    done
    echo ""
    echo -e "  ${Y}Container mantido para inspeção: ${B}${CONTAINER}${N}"
    echo -e "  ${Y}Para acessar: ${C}docker exec -it ${CONTAINER} bash${N}"
    echo -e "  ${Y}Para remover: ${C}docker rm -f ${CONTAINER} && docker rmi ${IMAGE}${N}"
    echo ""
    exit 1
fi
