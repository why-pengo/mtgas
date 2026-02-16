# Session 1 - MTG Arena Statistics Tracker Development

## Initial Request

I would like to save my Magic the Gathering: Arena games statistics in a database. I want to track the following information for each game:
- Date and time of the game
- Opponent's name
- Result (win/loss)
- Deck used
- Duration of the game
- Format (Standard, Modern, etc.)
- All plays during the game, with details such as cards played, mana spent.

To achieve this, design a database schema that can accommodate all of this information.
It will likely need to create multiple tables to store the different types of data, such as a table for games, a table for decks, and a table for plays.

To collect the data, find a way to extract the relevant information from the game logs.
There is a recent log file in data/Player.log that contains the details of a game.
Parse this log file and analyze to see if the necessary information can be extracted.

Once the data in the log file is parsed and understood, plan the database schema and the process for inserting the data into the database.

Consider using a relational database like MySQL or PostgreSQL, and design the tables with appropriate relationships to ensure data integrity and efficient querying.

Then implement the database and write the necessary code to parse the log file and insert the data into the database.

Test the entire process to ensure that the data is being correctly extracted, stored, and can be queried effectively.

Then design a user interface or reporting system to visualize the statistics and insights from the stored game data, such as win rates, most used decks, and performance against decks and color types.

## Additional Requirements

- Use Django instead of Flask.
- Download bulk JSON from Scryfall instead of API calls.
- Use matchId to track.
- Batch import after sessions.

## Visualization Requirements

- Use d3.js to create visualizations for the statistics, such as bar charts for win rates and pie charts for deck usage.

## Testing Requirements

- Write pytests to ensure that the data parsing, database insertion, and querying processes are working correctly.
- Make sure to handle any edge cases, such as incomplete log files or missing data, and implement error handling to ensure the application is robust and can handle unexpected situations gracefully.

## Add .gitignore

Add a gitignore file.

## Code Quality & Documentation

Use the black, flake8 and isort tools to ensure that the code is well-formatted and adheres to best practices for readability and maintainability. Create a makefile to automate the setup of the database, running the application, checking format, checking linting and running tests to streamline the development process and ensure consistency across different environments. Finally, document the entire process, including the database schema design, data parsing logic, and how to set up and run the application, so that others can understand and contribute to the project in the future.

## Use pyproject.toml

Can the pyproject.toml be using instead of a requirements.txt

## Fix Failing Tests

FAILED tests/test_parser.py::TestLogParserEventExtraction::test_parse_empty_file - src.exceptions.InvalidLogFormatError: Log file is empty
FAILED tests/test_parser.py::TestEdgeCases::test_malformed_json - assert 0 == 1
FAILED tests/test_parser.py::TestEdgeCases::test_large_game_state - AssertionError: assert 99 == 100

## Fix Test Warnings

Fix the warnings (RuntimeWarning: DateTimeField Match.start_time received a naive datetime while time zone support is active).

## Fix make check

make check is failing (isort and flake8 errors).

## Document Makefile Usage

Is the makefile usage documented in quickstart.md and readme.md?

## Fix Import Error

```
make import-log LOG=data/Player.log
Importing log file: data/Player.log
python3 manage.py import_log data/Player.log
Traceback (most recent call last):
  ...
AttributeError: module 'django.utils.timezone' has no attribute 'utc'. Did you mean: 'UTC'?
```

## Move Inline CSS to External Stylesheet

Move all inline css from stats/templates to stats/static/css/style.css.

## CSS Linting

What CSS linting could be implemented?

## Fix Bootstrap CSS Variable Override

--bs-secondary-color is overriding --text-primary

## Scryfall Card Links

Make all mtg card names be links to that card on scryfall.com.

---

## Summary of What Was Built

### Core Features
1. **Django Web Application** - Full web interface for viewing statistics
2. **Log Parser** - Extracts match data from MTG Arena's Player.log file
3. **Scryfall Integration** - Downloads bulk card data for card name resolution
4. **Database Schema** - SQLite database with 8 tables for comprehensive tracking
5. **D3.js Visualizations** - Interactive charts for win rates, deck usage, mana curves
6. **Scryfall Card Links** - All card names link to Scryfall for card details

### Database Tables
- `cards` - Card metadata from Scryfall
- `decks` - Deck information
- `deck_cards` - Cards in each deck (junction table)
- `matches` - Game results and metadata
- `game_actions` - Individual plays during games
- `life_changes` - Life total tracking
- `zone_transfers` - Card movements
- `import_sessions` - Import history tracking

### Code Quality
- **black** - Code formatting
- **isort** - Import sorting
- **flake8** - Python linting
- **stylelint** - CSS linting
- **pytest** - 63 tests covering parser, models, views, and services

### Documentation
- `README.md` - Main documentation
- `QUICKSTART.md` - Quick start guide with Makefile commands
- `CONTRIBUTING.md` - Contribution guidelines
- `docs/DATABASE_SCHEMA.md` - Database schema documentation
- `docs/LOG_PARSING.md` - Log parsing documentation
- `docs/DEVELOPMENT.md` - Development guide

### Makefile Commands
- `make setup` - Full setup
- `make run` - Start server
- `make test` - Run tests
- `make format` - Format code
- `make check` - Check Python formatting and linting
- `make lint-css` - Check CSS with stylelint
- `make check-all` - Check Python and CSS
- `make ci` - Run all CI checks
- `make import-log LOG=path` - Import log file
- `make download-cards` - Download Scryfall data

### CSS Refactoring
- Moved all inline CSS from `base.html` to `stats/static/css/style.css`
- Added CSS classes for scrollable containers (`scrollable-deck-list`, `scrollable-timeline`)
- Organized CSS with sections for variables, base styles, components, and charts
- Only dynamic `width` values remain inline (required for template variables)
- Added Bootstrap CSS variable overrides for dark theme compatibility
- Implemented stylelint for CSS linting with `stylelint-config-standard`
- Added `.card-link` class for Scryfall links (blue color, distinct from accent)

