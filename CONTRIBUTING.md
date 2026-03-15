# Contributing to MTG Arena Statistics Tracker

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Set up the development environment (see below)
4. Create a feature branch from `main`

## Development Setup

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/mtgas.git
cd mtgas

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies (editable install with dev tools)
pip install -e ".[dev]"

# Set up database
make migrate

# Download card data
make download-cards

# Verify setup
make test
```

## Code Standards

### Formatting

All code must be formatted with **black** and **isort**:

```bash
# Format code
make format

# Check formatting
make format-check
```

### CSS

All styles must live in `stats/static/css/style.css`. **Do not** add `<style>` blocks to templates or `style="..."` inline attributes to HTML elements.

```
✅ <div class="my-component">      (class in style.css)
❌ <div style="color: red">        (inline style)
❌ {% block extra_css %}<style>…   (template style block)
```

**Exceptions** — these two cases are unavoidable and acceptable:
1. **Django template variables**: `style="width: {{ value }}%"` when the value drives the style directly (e.g., progress bar fills). There is no CSS-only alternative.
2. **JS-controlled initial state**: `style="display:none"` on elements whose visibility is toggled by JavaScript that reads/writes `element.style.*`. Changing these to class toggles requires coordinated JS changes.

Run `make lint-css` (stylelint) to validate CSS. Use modern `rgb()` notation — `rgb(0 0 0 / 50%)` not `rgba(0, 0, 0, 0.5)`.

### Linting

All code must pass **flake8** linting:

```bash
make lint
```

### Pre-commit Check

Before committing, run:

```bash
make ci
```

This runs format checks, linting, and tests.

## Testing

All new features should include tests:

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Run specific tests
pytest tests/test_parser.py -v
```

## Pull Request Process

1. **Create a branch**: `git checkout -b feature/your-feature-name`

2. **Make changes**: Follow code standards

3. **Test**: Ensure `make ci` passes

4. **Commit**: Use descriptive commit messages
   - `feat:` for new features
   - `fix:` for bug fixes
   - `docs:` for documentation
   - `test:` for tests
   - `refactor:` for refactoring

5. **Push**: `git push origin feature/your-feature-name`

6. **Open PR**: Submit a pull request with:
   - Clear description of changes
   - Link to related issues
   - Screenshots for UI changes

## Project Structure

- `stats/` - Django application (models, views, templates)
- `src/` - Core business logic (parser, services)
- `tests/` - Test suite
- `docs/` - Documentation

## Documentation

- Update `README.md` for user-facing changes
- Update `docs/` for technical documentation
- Add docstrings to new functions/classes

## Questions?

Open an issue for questions or discussions.

