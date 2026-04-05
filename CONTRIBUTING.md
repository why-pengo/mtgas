# Contributing to MTG Arena Statistics Tracker

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork locally
3. Set up the development environment (see below)
4. Create a feature branch from `develop` (see [Branch & PR Conventions](#branch--pr-conventions))

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
.venv/bin/pytest

# Run with coverage
.venv/bin/pytest --cov=stats --cov=src --cov=cards

# Run specific tests
.venv/bin/pytest tests/test_parser.py -v
```

## Pull Request Process

1. **Create a branch** from `develop` using the `issue-{number}-{short-description}` naming convention (see [Branch & PR Conventions](#branch--pr-conventions))

2. **Make changes**: Follow code standards

3. **Test**: Ensure `make ci` passes

4. **Commit**: Use conventional commit prefixes:
   - `feat:` for new features
   - `fix:` for bug fixes
   - `docs:` for documentation
   - `test:` for tests
   - `refactor:` for refactoring

5. **Push**: `git push origin issue-{number}-{short-description}`

6. **Open PR** targeting `develop` with:
   - Clear description of changes
   - Link to the related issue (`Closes #N`)
   - Screenshots for UI changes

## Project Structure

- `stats/` - Main Django application (models, views, templates)
- `cards/` - Paper Cards Django application (PaperCard model, Scryfall lookup, templatetags)
- `src/` - Core business logic (parser, services)
- `tests/` - Test suite
- `docs/` - Documentation

## Documentation

- Update `README.md` for user-facing changes
- Update `docs/` for technical documentation
- Add docstrings to new functions/classes

## Branch & PR Conventions

Every piece of work must follow this branching model:

| Rule | Detail |
|------|--------|
| **Branch base** | Always cut from `develop` — never from `main` |
| **Branch name** | `issue-{number}-{short-description}` — e.g., `issue-34-import-spinner` |
| **PR target** | Always target `develop` — never `main` |
| **Merging to `main`** | Only via a release PR from `develop` |
| **CI gate** | `make ci` must pass before merging |
| **Issue link** | Every branch must be linked to a GitHub issue; create one first if none exists |

```bash
# Start work on issue #42
git checkout develop
git pull
git checkout -b issue-42-my-feature
# … make changes …
make ci
git push -u origin issue-42-my-feature
# Open PR targeting develop on GitHub
```

## Documenting Feature Changes

After every code change, update **every** doc file that describes the affected feature:

| File | When to update |
|------|---------------|
| `README.md` | User-facing features, routes, setup steps |
| `QUICKSTART.md` | Quick-start commands or first-run steps |
| `docs/DATABASE_SCHEMA.md` | Model or schema changes |
| `docs/DEVELOPMENT.md` | Dev workflow, commands, or architecture changes |
| `docs/LOG_PARSING.md` | Parser changes or new event types |
| `docs/LOGGING.md` | Logging config or logger-name changes |
| `docs/MATCH_REPLAY.md` | Match replay or game-action changes |

**Rule**: if your commit adds, removes, or changes a feature, model, route, management command, or configuration value that is described in any of the files above, update that file in the **same commit**. Do not leave docs stale.

## Using Copilot to Work on Issues

### Writing a good issue

Clear issues produce better results from both humans and Copilot. Follow the templates below.

**Bug report**
- Title: short and specific — *"Import fails when log has duplicate match IDs"*
- Body: steps to reproduce, expected vs. actual behaviour, environment, full traceback in a fenced code block
- Labels: `bug`

**Feature request**
- Title: action-oriented — *"Add deck win-rate chart to dashboard"*
- Body: problem statement, proposed solution, alternatives considered, acceptance criteria
- Labels: `enhancement`

**General rules**: one issue = one concern; link related issues/PRs with `#N`; add screenshots or logs.

```markdown
### Acceptance Criteria
- [ ] <observable outcome 1>
- [ ] <observable outcome 2>
- [ ] Tests cover the new behaviour
```

### Picking up an issue with Copilot

Copilot reads `.github/copilot-instructions.md` for project-specific conventions. To start work on an existing issue:

1. Reference the issue number when prompting — e.g., *"Let's work on issue #42"*
2. Copilot will create the branch `issue-42-{description}` from `develop` automatically
3. All commits and the PR description will reference the issue
4. Copilot will run `make ci` before pushing and will open the PR targeting `develop`

To write a new issue before handing it to Copilot, follow the templates above and include clear acceptance criteria — Copilot uses them to know when the task is complete.

## Questions?

Open an issue for questions or discussions.

