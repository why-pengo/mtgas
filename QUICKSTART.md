# MTG Arena Statistics Tracker - Quick Start

## Option A: Docker Compose (Recommended)

Docker Compose starts all services (Django, PostgreSQL) with a single command. No local Python install required.

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (macOS / Windows) or Docker Engine + Docker Compose plugin (Linux)

### 1. Configure environment

Copy the example env file and adjust as needed:

```bash
cp .env.example .env
```

The defaults work out of the box for local development.

### 2. Start all services

```bash
docker compose up --build
```

This builds the image (first run only, subsequent runs are fast), then starts `web` and `postgres`.

### 3. Run migrations and download card data

In a separate terminal:

```bash
docker compose exec web python manage.py migrate
docker compose exec web python manage.py download_cards
```

### 4. Import your game log

```bash
docker compose exec web python manage.py import_log /app/data/Player.log
# or copy the log file first:
# cp ~/Library/Logs/Wizards\ Of\ The\ Coast/MTGA/Player.log data/Player.log
```

### 5. Open the app

Browse to **http://127.0.0.1:8000/**

---

## Option B: Running Locally on macOS

### Setup

```bash
# 1. Navigate to project directory
cd /path/to/mtgas

# 2. Create virtual environment
python3 -m venv .venv

# 3. Full setup (install deps + migrate database)
make setup

# 4. Download Scryfall card data (one-time, ~350MB)
make download-cards

# 5. Import your game log (batch import after sessions)
make import-log LOG=data/Player.log

# 6. Start the web server
make run

# 7. Open browser to http://127.0.0.1:8000/
```

## Running Tests

```bash
# Run all tests
make test

# Run with verbose output
make test-verbose

# Run with coverage report
make test-cov

# Run specific test file
make test-parser
```

## Code Quality

```bash
# Format code with black and isort
make format

# Check formatting and linting
make check

# Run CSS linting (requires: npm install)
make lint-css

# Run all CI checks (format + lint + tests)
make ci
```

## Makefile Commands Reference

Run `make help` to see all available commands:

| Command | Description |
|---------|-------------|
| `make setup` | Full setup (install deps + migrate) |
| `make run` | Start development server |
| `make test` | Run all tests |
| `make test-cov` | Run tests with coverage |
| `make format` | Format code with black/isort |
| `make lint` | Run flake8 linter |
| `make lint-css` | Run stylelint on CSS |
| `make check` | Run all checks: format, lint, and CSS (requires `npm install`) |
| `make ci` | Run checks + tests (for CI) |
| `make download-cards` | Download Scryfall data |
| `make import-log LOG=path` | Import a log file |
| `make clean` | Remove cache files |
| `make help` | Show all commands |

## Key Features

### Database (Django ORM with SQLite)
- **Match tracking** by `match_id` (unique identifier from Arena)
- **Deck storage** with card compositions; snapshots deduplicated — new version only on change
- **Deck analysis**: mana curve, color distribution, improvement suggestions
- **Game actions** (casts, plays, attacks, blocks)
- **Life changes** over the course of games
- **Zone transfers** (draws, plays, discards)
- **Import sessions** tracking

### Batch Import
- Run `make import-log LOG=/path/to/Player.log` after gaming sessions
- Uses `match_id` to track and avoid duplicate imports
- Supports `--force` flag to re-import all matches

### Scryfall Integration
- Downloads bulk JSON (~350MB) once with `make download-cards`
- Builds local index for fast Arena ID → card name lookups
- No per-card API calls needed

### Paper Card Identification (`/cards/`)
- Add any physical MTG card by name via `/cards/add/`
- Scryfall's fuzzy API finds the card even with partial or misspelled names
- Matched cards are saved as `PaperCard` records in the local database
- The cards table shows mana cost as SVG icons, a Scryfall link, and supports sortable columns and live search

### Web Interface (Django)
- **Dashboard**: Win rate, top decks, format stats, recent matches
- **Match History**: Filterable, sortable list with pagination
- **Match Details**: Turn-by-turn actions, life chart
- **Deck Performance**: Win rates, mana curve, color distribution, improvement suggestions
- **Deck History**: Visual diff of card changes between versions, match count per version
- **Import Sessions**: Track import history

### D3.js Visualizations
- Win rate over time line chart
- Deck win rate bar charts
- Deck usage pie charts
- Mana curve bar charts
- Color distribution stacked bar
- Life total timeline

## File Locations

- **Log File** (macOS): `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
- **Log File** (Windows): `%APPDATA%\..\LocalLow\Wizards Of The Coast\MTGA\Player.log`
- **Database**: `data/mtga_stats.db`
- **Card Cache**: `data/cache/arena_id_index.json`

