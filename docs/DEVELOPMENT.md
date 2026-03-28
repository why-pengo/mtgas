# Development Guide

This guide covers setting up the development environment, code standards, and contribution guidelines for the MTG Arena Statistics Tracker.

## Prerequisites

- Python 3.10 or higher
- pip (Python package manager)
- Git
- Make (optional, but recommended)

## Quick Start

```bash
# Clone the repository
git clone <repository-url>
cd mtgas

# Create virtual environment
make venv
source .venv/bin/activate

# Install dependencies and setup database
make setup

# Download card data (first time only)
make download-cards

# Run the application
make run
```

## Project Structure

```
mtgas/
├── manage.py                    # Django entry point
├── Makefile                     # Build automation
├── pyproject.toml              # Project config & dependencies
├── container-run.md            # Docker Compose reference
├── .flake8                     # Flake8 configuration
├── .gitignore                  # Git ignore rules
│
├── mtgas_project/              # Django project settings
│   ├── __init__.py
│   ├── settings.py             # Django configuration
│   ├── urls.py                 # Root URL routing
│   └── wsgi.py                 # WSGI application
│
├── stats/                      # Main Django application
│   ├── __init__.py
│   ├── apps.py                 # App configuration
│   ├── models.py               # Database models
│   ├── views/                  # Views split by domain
│   │   ├── dashboard.py
│   │   ├── decks.py
│   │   ├── matches.py
│   │   ├── imports.py
│   │   └── cards.py
│   ├── urls.py                 # App URL routing
│   ├── admin.py                # Admin interface
│   ├── templates/              # HTML templates
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── matches.html
│   │   ├── match_detail.html
│   │   ├── decks.html
│   │   ├── deck_detail.html
│   │   └── import_sessions.html
│   ├── static/                 # Static files
│   │   ├── css/
│   │   │   └── style.css
│   │   └── js/
│   │       ├── app.js
│   │       └── charts.js       # D3.js visualizations
│   └── management/
│       └── commands/           # Custom management commands
│           ├── import_log.py
│           └── download_cards.py
│
├── cards/                      # Paper Cards Django application
│   ├── __init__.py
│   ├── apps.py
│   ├── models.py               # PaperCard model
│   ├── views.py                # Index, add, detail views
│   ├── urls.py                 # Mounted at /cards/
│   ├── admin.py
│   ├── templatetags/
│   │   └── cards_extras.py     # mana_icons & cmc_value template filters
│   ├── templates/cards/
│   │   ├── index.html          # Paper Cards list (sortable, searchable)
│   │   ├── add_paper_card.html
│   │   └── paper_card_detail.html
│   └── migrations/
│
├── src/                        # Core business logic
│   ├── __init__.py
│   ├── exceptions.py           # Custom exceptions
│   ├── parser/
│   │   ├── __init__.py
│   │   └── log_parser.py       # MTGA log parser
│   └── services/
│       ├── __init__.py
│       └── scryfall.py         # Scryfall data service
│
├── tests/                      # Test suite
│   ├── __init__.py
│   ├── conftest.py             # Pytest configuration & fixtures
│   ├── test_parser.py          # Parser tests
│   ├── test_scryfall.py        # Scryfall service tests
│   ├── test_models.py          # Model tests
│   ├── test_views.py           # Stats view tests
│   ├── test_cards.py           # Paper Cards model & view tests
│   ├── test_deck_analysis.py   # Mana curve, color distribution tests
│   ├── test_deck_versioning.py # DeckSnapshot deduplication tests
│   ├── test_play_advisor.py    # Play advisor tests
│   └── test_unknown_cards.py  # Unknown card fallback tests
│
├── data/                       # Data directory
│   ├── .gitkeep
│   ├── mtga_stats.db           # SQLite database (gitignored)
│   ├── cache/                  # Scryfall cache (gitignored)
│   └── Player.log              # Sample log file (gitignored)
│
└── docs/                       # Documentation
    ├── DATABASE_SCHEMA.md
    ├── LOG_PARSING.md
    ├── DEVELOPMENT.md
    ├── LOGGING.md
    └── MATCH_REPLAY.md
```

## Development Workflow

### 1. Setting Up

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install all dependencies including dev tools
make install-dev

# Or manually:
pip install -e ".[dev]"

# Run migrations
make migrate
```

### 2. Running the Application

```bash
# Start development server
make run

# Or manually:
python manage.py runserver

# Access at http://127.0.0.1:8000/
```

### 3. Importing Data

```bash
# Download card data (first time)
make download-cards

# Import a log file
make import-log LOG=/path/to/Player.log

# Or import the sample log
make import-default
```

## Code Quality

### Formatting with Black

Black is an opinionated code formatter that ensures consistent style.

```bash
# Format all code
make format

# Check formatting without changes
make format-check

# Or manually:
black src/ stats/ tests/ mtgas_project/
```

Configuration in `pyproject.toml`:
```toml
[tool.black]
line-length = 100
target-version = ['py310', 'py311', 'py312']
```

### Import Sorting with isort

isort automatically sorts and organizes imports.

```bash
# Sort imports (included in make format)
isort src/ stats/ tests/ mtgas_project/

# Check import order
isort --check-only --diff src/ stats/ tests/
```

Configuration in `pyproject.toml`:
```toml
[tool.isort]
profile = "black"
line_length = 100
```

### Linting with Flake8

Flake8 checks for code style and potential errors.

```bash
# Run linter
make lint

# Or manually:
flake8 src/ stats/ tests/ mtgas_project/
```

Configuration in `.flake8`:
```ini
[flake8]
max-line-length = 100
extend-ignore = E203, E501, W503
exclude = .git, __pycache__, .venv, migrations
```

### CSS Linting with Stylelint

Stylelint checks CSS for errors and enforces consistent style.

```bash
# Install Node.js dependencies (first time only)
npm install

# Run CSS linter
make lint-css

# Auto-fix CSS issues
make lint-css-fix

# Or manually:
npx stylelint 'stats/static/css/**/*.css'
```

Configuration in `.stylelintrc.json`:
```json
{
  "extends": ["stylelint-config-standard"],
  "rules": {
    "color-hex-length": "long",
    "alpha-value-notation": "number",
    "color-function-notation": "legacy"
  }
}
```

### Running All Checks

```bash
# Run format check + Python lint
make check

# Run format check + Python lint + CSS lint
make check-all

# Run format check + lint + tests
make ci
```

## Testing

### Running Tests

```bash
# Run all tests
make test

# Run with verbose output
make test-verbose

# Run with coverage
make test-cov

# Run specific test file
pytest tests/test_parser.py -v

# Run specific test class
pytest tests/test_models.py::TestMatchModel -v

# Run specific test
pytest tests/test_parser.py::TestLogParserInitialization::test_parser_file_not_found -v
```

### Writing Tests

Tests are organized by module:

- `test_parser.py`: Log parsing functionality
- `test_scryfall.py`: Scryfall service
- `test_models.py`: Django models
- `test_views.py`: Stats web views
- `test_cards.py`: Paper Cards model, views, and Scryfall lookup
- `test_deck_analysis.py`: Mana curve, color distribution, improvement suggestions
- `test_deck_versioning.py`: DeckSnapshot deduplication and diff
- `test_play_advisor.py`: Play advisor / improvement suggestion logic
- `test_unknown_cards.py`: Unknown card fallback handling

Example test:
```python
import pytest
from src.parser.log_parser import MTGALogParser

class TestLogParser:
    def test_parser_file_not_found(self):
        """Test that parser raises error for missing file."""
        with pytest.raises(FileNotFoundError):
            MTGALogParser("/nonexistent/path/Player.log")
    
    def test_parse_match(self, tmp_path):
        """Test parsing a complete match."""
        log_content = '{"matchGameRoomStateChangedEvent": {...}}'
        log_file = tmp_path / "Player.log"
        log_file.write_text(log_content)
        
        parser = MTGALogParser(str(log_file))
        matches = parser.parse_matches()
        
        assert len(matches) == 1
```

### Test Fixtures

Common fixtures are defined in `conftest.py`:

```python
@pytest.fixture
def sample_card(db):
    """Create a sample card for testing."""
    from stats.models import Card
    return Card.objects.create(
        grp_id=12345,
        name="Lightning Bolt",
        mana_cost="{R}",
        cmc=1.0
    )
```

## Database Migrations

### Creating Migrations

When you change models:

```bash
# Generate migration files
make makemigrations

# Apply migrations
make migrate
```

### Resetting Database

```bash
# WARNING: Destroys all data
make resetdb
```

## Adding Features

### 1. Adding a New Model

1. Define model in `stats/models.py`
2. Create migration: `make makemigrations`
3. Apply migration: `make migrate`
4. Add to admin in `stats/admin.py`
5. Write tests in `tests/test_models.py`

### 2. Adding a New View

1. Add view function in `stats/views.py`
2. Add URL pattern in `stats/urls.py`
3. Create template in `stats/templates/`
4. Write tests in `tests/test_views.py`

### 3. Adding a Management Command

1. Create file in `stats/management/commands/`
2. Implement `Command` class with `handle()` method
3. Add Makefile target if needed

## Debugging

### Django Debug Mode

Debug mode is enabled by default in development (`DEBUG = True` in settings.py).

### Logging

Configure logging in `settings.py`:
```python
LOGGING = {
    'version': 1,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'src.parser': {
            'handlers': ['console'],
            'level': 'DEBUG',
        },
    },
}
```

### Django Shell

```bash
make shell

# Then in the shell:
>>> from stats.models import Match
>>> Match.objects.count()
>>> Match.objects.filter(result='win').count()
```

## Common Issues

### 1. Import Errors

If you get import errors, ensure you're in the project root and your virtual environment is activated:
```bash
cd /path/to/mtgas
source .venv/bin/activate
```

### 2. Migration Errors

If migrations are out of sync:
```bash
# Reset and recreate
rm data/mtga_stats.db
python manage.py migrate
```

### 3. Card Data Missing

If card names show as "Unknown":
```bash
make download-cards
```

## Deployment

For production deployment:

1. Set `DEBUG = False` in settings
2. Configure `ALLOWED_HOSTS`
3. Use a production database (PostgreSQL)
4. Set up static file serving
5. Use gunicorn or similar WSGI server

```bash
# Example production run
gunicorn mtgas_project.wsgi:application --bind 0.0.0.0:8000
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes following code standards
4. Run `make ci` to verify all checks pass
5. Submit a pull request

### Commit Messages

Use descriptive commit messages:
- `feat: Add deck color distribution chart`
- `fix: Handle missing opponent name in parser`
- `docs: Update database schema documentation`
- `test: Add tests for life change tracking`

