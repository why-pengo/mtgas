# MTG Arena Statistics Tracker - Quick Start

## Setup Commands

```bash
# 1. Navigate to project directory
cd /Users/jmorgan/workspace/mtgas

# 2. Create and activate virtual environment (if not already done)
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies (editable install with dev tools)
pip install -e ".[dev]"

# 4. Run database migrations
python manage.py migrate

# 5. Download Scryfall card data (one-time, ~350MB)
python manage.py download_cards

# 6. Import your game log (batch import after sessions)
python manage.py import_log data/Player.log

# 7. Start the web server
python manage.py runserver

# 8. Open browser to http://127.0.0.1:8000/
```

## Running Tests

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_parser.py
```

## Key Features Implemented

### Database (Django ORM with SQLite)
- **Match tracking** by `match_id` (unique identifier from Arena)
- **Deck storage** with card compositions
- **Game actions** (casts, plays, attacks, blocks)
- **Life changes** over the course of games
- **Zone transfers** (draws, plays, discards)
- **Import sessions** tracking

### Batch Import
- Run `python manage.py import_log` after gaming sessions
- Uses `match_id` to track and avoid duplicate imports
- Supports `--force` flag to re-import all matches

### Scryfall Integration
- Downloads bulk JSON (~350MB) once with `download_cards` command
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

### Testing (pytest)
- Parser unit tests
- Scryfall service tests
- Django model tests
- View integration tests

### Error Handling
- Custom exceptions for specific error types
- Graceful handling of malformed JSON
- Continues parsing after non-fatal errors
- Validates log file format

## File Locations

- **Log File** (macOS): `~/Library/Logs/Wizards Of The Coast/MTGA/Player.log`
- **Database**: `data/mtga_stats.db`
- **Card Cache**: `data/cache/arena_id_index.json`

