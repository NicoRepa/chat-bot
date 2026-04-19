#!/bin/bash
set -e

# ── Colores ──────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Funciones ────────────

wait_for_db() {
    echo -e "${YELLOW}⏳ Esperando base de datos...${NC}"
    local retries=30
    while [ "$retries" -gt 0 ]; do
        python -c "
import django, os
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
django.setup()
from django.db import connection
connection.ensure_connection()
print('OK')
" 2>/dev/null && break
        retries=$((retries - 1))
        sleep 1
    done
    if [ "$retries" -eq 0 ]; then
        echo -e "${RED}❌ No se pudo conectar a la DB después de 30 intentos${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Base de datos lista${NC}"
}

wait_for_redis() {
    if [ -z "$REDIS_URL" ]; then
        echo -e "${YELLOW}⚠️  REDIS_URL no configurada, saltando check de Redis${NC}"
        return 0
    fi
    echo -e "${YELLOW}⏳ Esperando Redis...${NC}"
    local retries=15
    while [ "$retries" -gt 0 ]; do
        python -c "
import redis, os
r = redis.from_url(os.environ.get('REDIS_URL', ''))
r.ping()
print('OK')
" 2>/dev/null && break
        retries=$((retries - 1))
        sleep 1
    done
    if [ "$retries" -eq 0 ]; then
        echo -e "${RED}❌ No se pudo conectar a Redis después de 15 intentos${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Redis listo${NC}"
}

# ── Modos de ejecución ──

case "${1:-web}" in
    web)
        wait_for_db
        wait_for_redis

        echo -e "${GREEN}📦 Aplicando migraciones...${NC}"
        python manage.py migrate --noinput

        echo -e "${GREEN}📂 Collecting static files...${NC}"
        python manage.py collectstatic --noinput 2>/dev/null || true

        echo -e "${GREEN}🚀 Iniciando Daphne (ASGI)...${NC}"
        exec daphne -b 0.0.0.0 -p "${PORT:-8000}" config.asgi:application
        ;;
    migrate)
        wait_for_db
        echo -e "${GREEN}📦 Aplicando migraciones...${NC}"
        exec python manage.py migrate --noinput
        ;;
    shell)
        wait_for_db
        exec python manage.py shell
        ;;
    *)
        exec "$@"
        ;;
esac
