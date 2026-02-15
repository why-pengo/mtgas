# MTG Arena Statistics Tracker

A Django-based application to track your Magic: The Gathering Arena game statistics.

## Features

- **Match Tracking**: Records all match details including opponent, result, deck used, duration, and format
- **Deck Performance**: Analyze win rates and performance for each deck
- **Game Replay**: View detailed game actions, life changes, and card plays
- **Statistics Dashboard**: Visualize win rates over time, format performance, and more
- **D3.js Visualizations**: Interactive charts including:
  - Win rate bar charts
  - Deck usage pie charts
  - Mana curve visualizations
  - Color distribution charts
  - Life total timeline
- **Batch Import**: Import log files after gaming sessions using `match_id` to avoid duplicates
- **Scryfall Integration**: Downloads bulk card data for card name resolution
- **Robust Error Handling**: Graceful handling of incomplete logs and missing data

## Setup

### 1. Create Virtual Environment

```bash
cd mtgas
python3 -m venv .venv
source .venv/bin/activate  # On macOS/Linux
# or: .venv\Scripts\activate  # On Windows
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Initialize Database

```bash
python manage.py migrate
```

### 4. Download Card Data (One-time)

```bash
python manage.py download_cards
```

This downloads Scryfall's bulk card data (~350MB) for resolving Arena card IDs to card names.

### 5. Create Admin User (Optional)

```bash
python manage.py createsuperuser
```

## Usage

### Import Game Data

After your gaming sessions, import your Player.log file:

```bash
python manage.py import_log /path/to/Player.log
```

On macOS, the log file is typically at:
```
~/Library/Logs/Wizards Of The Coast/MTGA/Player.log
```

On Windows:
```
%APPDATA%\..\LocalLow\Wizards Of The Coast\MTGA\Player.log
```

Options:
- `--force`: Re-import all matches, even if already imported
- `--download-cards`: Download fresh card data before importing

### Start Web Server

```bash
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser.

### Admin Interface

Access Django admin at http://127.0.0.1:8000/admin/

## Running Tests

The project includes comprehensive pytest tests:

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_parser.py

# Run specific test class
pytest tests/test_models.py::TestMatchModel

# Run with coverage (install pytest-cov first)
pip install pytest-cov
pytest --cov=stats --cov=src
```

### Test Categories

- `test_parser.py`: Log file parsing tests
- `test_scryfall.py`: Scryfall bulk data service tests  
- `test_models.py`: Django model and database tests
- `test_views.py`: Web interface tests

## Project Structure

```
mtgas/
├── manage.py                    # Django management script
├── requirements.txt             # Python dependencies
├── pytest.ini                   # Pytest configuration
├── mtgas_project/               # Django project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── stats/                       # Main Django app
│   ├── models.py               # Database models
│   ├── views.py                # Web views
│   ├── urls.py                 # URL routing
│   ├── admin.py                # Admin configuration
│   ├── templates/              # HTML templates
│   ├── static/                 # CSS and JS files
│   │   └── js/charts.js        # D3.js visualizations
│   └── management/commands/    # CLI commands
│       ├── import_log.py       # Log import command
│       └── download_cards.py   # Card data download
├── src/                        # Core logic
│   ├── exceptions.py           # Custom exceptions
│   ├── parser/
│   │   └── log_parser.py       # MTG Arena log parser
│   └── services/
│       └── scryfall.py         # Scryfall bulk data service
├── tests/                      # Pytest tests
│   ├── conftest.py
│   ├── test_parser.py
│   ├── test_scryfall.py
│   ├── test_models.py
│   └── test_views.py
└── data/                       # Data directory
    ├── mtga_stats.db           # SQLite database
    ├── cache/                  # Scryfall cache
    └── Player.log              # Sample log file
```

## How It Works

1. **Log Parsing**: The parser reads MTG Arena's Player.log file and extracts:
   - Match info from `matchGameRoomStateChangedEvent`
   - Player names, match IDs, and results
   - Deck info from `EventSetDeckV2` events
   - Game actions from `greToClientEvent` messages

2. **Card Resolution**: Uses Scryfall's bulk data to map Arena's `grpId` to card names

3. **Match Tracking**: Uses `match_id` as the unique identifier to prevent duplicate imports

4. **Batch Import**: Designed for post-session imports - run after you finish playing

5. **Error Handling**: 
   - Gracefully handles malformed JSON in logs
   - Continues parsing after non-fatal errors
   - Tracks and reports parse errors
   - Validates log file format before parsing

## Database Schema

### Core Tables

- **matches**: Game results, opponents, timing
- **decks**: Deck names and compositions  
- **deck_cards**: Cards in each deck
- **cards**: Card metadata from Scryfall
- **game_actions**: Individual plays during games
- **life_changes**: Life total tracking
- **zone_transfers**: Card movements (draw, play, etc.)
- **import_sessions**: Track import history

## Visualizations (D3.js)

The dashboard includes interactive D3.js charts:

- **Win Rate Over Time**: Line chart showing daily win rates with game count overlay
- **Deck Win Rates**: Bar chart comparing deck performance
- **Deck Usage**: Pie/donut chart showing which decks you play most
- **Mana Curve**: Bar chart showing card distribution by mana cost
- **Color Distribution**: Horizontal stacked bar for deck colors
- **Life Total Chart**: Line chart tracking life totals during a match

## License

MIT License

