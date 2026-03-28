# Running MTG Arena Stats in Containers

This project ships with a `docker-compose.yml` that starts the full service stack:
**Django web app and PostgreSQL**. A bind mount keeps your local source tree in sync
with the containers so code edits take effect immediately without rebuilding.

---

## Services

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| `web` | built from `Dockerfile` | `8000` | Django development server |
| `postgres` | `postgres:16-alpine` | 5432 (internal) | Primary database |

---

## First-time setup

### 1 — Copy the environment file

```bash
cp .env.example .env
```

The defaults in `.env.example` work out of the box for local development.
For production you **must** change `DJANGO_SECRET_KEY` and `POSTGRES_PASSWORD`.

### 2 — Build and start all services

```bash
docker compose up --build
```

This:
1. Builds the `web` image.
2. Starts PostgreSQL (with a health check before `web` starts).
3. Starts the Django dev server.

The app will be available at **http://localhost:8000**.

### 3 — Apply database migrations (first run only)

Open a second terminal and run:

```bash
docker compose exec web python manage.py migrate
```

---

## Common commands

### Start (foreground, with logs)

```bash
docker compose up
```

### Start in the background (detached)

```bash
docker compose up -d
```

### Stop all services

```bash
docker compose down
```

### Stop and delete volumes (wipes all data — use with care)

```bash
docker compose down -v
```

---

## Building and rebuilding

### First build (or after changing `pyproject.toml` / `Dockerfile`)

```bash
docker compose build
# or combined start-and-build:
docker compose up --build
```

### Force a clean rebuild (no Docker layer cache)

```bash
docker compose build --no-cache
docker compose up
```

### Rebuild only one service

```bash
docker compose build web
docker compose up web --no-deps
```

> **When to rebuild:** Rebuild whenever you change `pyproject.toml`, `Dockerfile`,
> or system-level dependencies. You do **not** need to rebuild for Python source
> code changes — the bind mount handles those automatically.

---

## Live code changes

The `web` container mounts your project root into `/app` inside the container:

```yaml
volumes:
  - .:/app    # your local files are the container's files
```

Because Django's dev server runs with `--reload` by default, **any `.py` file
edit is picked up automatically** — the server restarts within a second or two.

### What is NOT live-reloaded

| File | Action required |
|------|-----------------|
| `pyproject.toml` (new dep) | `docker compose build && docker compose up` |
| `Dockerfile` | `docker compose build && docker compose up` |
| New migration file | `docker compose exec web python manage.py migrate` |
| Static files | Served directly from the bind-mounted directory — changes appear immediately |

---

## Running management commands in containers

```bash
# Apply migrations
docker compose exec web python manage.py migrate

# Create a superuser
docker compose exec web python manage.py createsuperuser

# Open the Django shell
docker compose exec web python manage.py shell

# Import an MTGA log file (path inside the container or mounted volume)
docker compose exec web python manage.py import_log /app/data/Player.log

# Download Scryfall card data
docker compose exec web python manage.py download_cards

# Open a PostgreSQL prompt
docker compose exec postgres psql -U mtgas -d mtgas
```

---

## Running pytest

### Locally (recommended — uses SQLite, no containers needed)

Local development does **not** require Docker. The test suite runs entirely with
the default SQLite database:

```bash
# Ensure the local virtualenv is set up
make setup

# Run all tests
.venv/bin/pytest

# Or via Make
make test
```

### Inside the web container (against PostgreSQL)

```bash
docker compose exec web pytest
```

The container environment includes `POSTGRES_DB` so tests will run against
PostgreSQL. Use this to verify behaviour matches production before shipping.

---

## Volumes and persistent data

| Volume | Mounted at | Contents |
|--------|-----------|----------|
| `postgres_data` | inside `postgres` container | PostgreSQL data directory |
| `data_cache` | `/app/data/cache` in `web` | Scryfall card image cache |
| `media` | `/app/media` in `web` | Django MEDIA_ROOT |

Volumes survive `docker compose down` but are deleted by `docker compose down -v`.

### Inspect a volume

```bash
docker volume inspect mtgas_postgres_data
```

### Back up the PostgreSQL database

```bash
docker compose exec postgres pg_dump -U mtgas mtgas > backup.sql
```

### Restore from a backup

```bash
docker compose exec -T postgres psql -U mtgas mtgas < backup.sql
```

---

## Environment variables reference

See `.env.example` for the full list with comments. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DJANGO_SECRET_KEY` | (insecure dev key) | Django secret key — **change in production** |
| `DJANGO_DEBUG` | `True` | Enable/disable Django debug mode |
| `DJANGO_ALLOWED_HOSTS` | *(empty)* | Comma-separated extra allowed hosts |
| `POSTGRES_DB` | `mtgas` | Database name |
| `POSTGRES_USER` | `mtgas` | Database user |
| `POSTGRES_PASSWORD` | `mtgas_dev_password` | Database password |
| `POSTGRES_HOST` | `postgres` | Set by `docker-compose.yml`; only override for external DB |
| `POSTGRES_PORT` | `5432` | Database port |
| `TIME_ZONE` | `America/New_York` | Django timezone |

> `POSTGRES_HOST` is always overridden to `postgres` by `docker-compose.yml` so
> containers always reach the correct database service, regardless of `.env`.

---

## Switching between SQLite (local) and PostgreSQL (Docker)

The application detects the database automatically:

* **No `POSTGRES_DB` env var** → SQLite at `data/mtga_stats.db` (local dev default)
* **`POSTGRES_DB` env var set** → PostgreSQL using the `POSTGRES_*` variables

This means `pytest`, `make run`, and other local workflows continue to work
exactly as before — no environment variables need to be set or unset.
