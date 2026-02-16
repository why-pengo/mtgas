# MTG Arena Statistics Tracker - Quick Start

## Setup Commands

```bash
# 1. Navigate to project directory
cd /Users/jmorgan/workspace/mtgas

# 2. Create and activate virtual environment
make venv
source .venv/bin/activate

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

## Alternative: Manual Setup

If you prefer not to use the Makefile:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
python manage.py migrate
python manage.py download_cards
python manage.py import_log data/Player.log
python manage.py runserver
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
pytest tests/test_parser.py
```

## Code Quality

```bash
# Format code with black and isort
make format

# Check formatting and linting
make check

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
| `make check` | Run all code quality checks |
| `make ci` | Run checks + tests (for CI) |
| `make download-cards` | Download Scryfall data |
| `make import-log LOG=path` | Import a log file |
| `make clean` | Remove cache files |
| `make help` | Show all commands |

## Key Features

### Database (Django ORM with SQLite)
- **Match tracking** by `match_id` (unique identifier from Arena)
- **Deck storage** with card compositions
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

### Web Interface (Django)
- **Dashboard**: Win rate, top decks, format stats, recent matches
- **Match History**: Filterable list with pagination
- **Match Details**: Turn-by-turn actions, life chart
- **Deck Performance**: Win rates, mana curve, matchups
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

