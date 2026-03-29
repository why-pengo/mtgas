FROM python:3.12-slim

WORKDIR /app

# System dependencies required by psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying full source for better layer caching.
# Stub package directories let setuptools resolve the editable install metadata
# without the full source tree; the real code is provided by the bind mount at runtime.
COPY pyproject.toml README.md ./
RUN mkdir -p stats src mtgas_project cards && \
    touch stats/__init__.py src/__init__.py mtgas_project/__init__.py cards/__init__.py && \
    pip install --no-cache-dir -e ".[postgres,production]"

# Copy application code (overridden by the bind mount in docker-compose.yml)
COPY . .

# Run as a non-root user for security
RUN useradd --create-home --shell /bin/false django && \
    chown -R django:django /app /home/django
USER django

# Safe production default — overridden by docker-compose (via .env) for local dev
ENV DJANGO_DEBUG=False

EXPOSE 8000

# Use Gunicorn for production. The docker-compose dev setup overrides this
# with `python manage.py runserver` via the `command:` key.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "mtgas_project.wsgi:application"]
