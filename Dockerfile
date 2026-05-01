# Multi-stage build for Attenova Django app

# -----------------------------------------------------------------------------
# Stage 1: builder – install dependencies
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system deps for MySQL (mysqlclient) and PostgreSQL (psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    libpq-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Create virtual env and install Python deps
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Precompile Python bytecode for faster startup (micro-optimization)
RUN python -m compileall -q /opt/venv

# -----------------------------------------------------------------------------
# Stage 2: runtime – minimal image to run the app
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

WORKDIR /app

# Runtime deps for MySQL (mysqlclient → libmariadb) and PostgreSQL (shared libs only).
# Debian trixie+ no longer ships the default-libmysqlclient metapackage name.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmariadb3 \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Use virtual env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Non-root user
RUN groupadd --gid 1000 app && \
    useradd --uid 1000 --gid app --shell /bin/bash --create-home app
USER app

# Application code (run as app so paths are writable for media/static if mounted)
COPY --chown=app:app . .

# Workers/threads: set GUNICORN_WORKERS (default 4), GUNICORN_THREADS (default 2) at runtime
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-4} --threads ${GUNICORN_THREADS:-2} Attenova.wsgi:application"]
