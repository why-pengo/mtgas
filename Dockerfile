FROM python:3.12-slim

WORKDIR /app

# System dependencies required by psycopg2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying full source for better layer caching.
# Stub package directories let setuptools resolve the editable install metadata
# without the full source tree; the real code is provided by the bind mount at runtime.
COPY pyproject.toml README.md ./
RUN mkdir -p stats src mtgas_project cards && \
    touch stats/__init__.py src/__init__.py mtgas_project/__init__.py cards/__init__.py && \
    pip install --no-cache-dir -e ".[dev,postgres]"

# Copy application code (overridden by the bind mount in docker-compose.yml)
COPY . .

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
