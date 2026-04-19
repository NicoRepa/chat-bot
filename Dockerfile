# ---- Build stage ----
FROM python:3.12-slim AS builder

WORKDIR /app

# Instalar dependencias del sistema para psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- Runtime stage ----
FROM python:3.12-slim

WORKDIR /app

# Solo las libs necesarias en runtime (libpq para psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Usuario no-root para seguridad
RUN groupadd -r app && useradd -r -g app -d /app -s /sbin/nologin app

# Copiar paquetes instalados del builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copiar código
COPY . .

# Entrypoint
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Variables de entorno por defecto (se sobreescriben en deploy)
ENV DJANGO_SETTINGS_MODULE=config.settings
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Collectstatic en build time (no necesita DB)
RUN python manage.py collectstatic --noinput 2>/dev/null || true

# Permisos
RUN chown -R app:app /app
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
