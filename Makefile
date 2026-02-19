# MTG Arena Statistics Tracker - Makefile
#
# This Makefile provides commands for common development tasks.
# Run `make help` to see all available commands.

.PHONY: help install install-dev setup migrate run test lint format check clean download-cards import-log

# Default Python interpreter
VENV := .venv
VENV_BIN := $(VENV)/bin
PYTHON := $(VENV_BIN)/python3
PIP := $(VENV_BIN)/pip

# Colors for terminal output
BLUE := \033[0;34m
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## Show this help message
	@echo "$(BLUE)MTG Arena Statistics Tracker$(NC)"
	@echo "=============================="
	@echo ""
	@echo "$(GREEN)Available commands:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

# =============================================================================
# Environment Setup
# =============================================================================

venv: ## Create virtual environment
	@echo "$(BLUE)Creating virtual environment...$(NC)"
	$(PYTHON) -m venv $(VENV)
	@echo "$(GREEN)Virtual environment created at $(VENV)$(NC)"
	@echo "Activate with: source $(VENV)/bin/activate"

install: ## Install production dependencies
	@echo "$(BLUE)Installing production dependencies...$(NC)"
	$(PIP) install -e .
	@echo "$(GREEN)Dependencies installed$(NC)"

install-dev: ## Install development dependencies (includes formatting/linting tools)
	@echo "$(BLUE)Installing development dependencies...$(NC)"
	$(PIP) install -e ".[dev]"
	@echo "$(GREEN)Development dependencies installed$(NC)"

setup: install-dev migrate ## Full setup: install deps and initialize database
	@echo "$(GREEN)Setup complete!$(NC)"
	@echo "Run 'make download-cards' to download Scryfall card data"
	@echo "Run 'make run' to start the development server"

# =============================================================================
# Database
# =============================================================================

migrate: ## Run database migrations
	@echo "$(BLUE)Running database migrations...$(NC)"
	$(PYTHON) manage.py migrate
	@echo "$(GREEN)Migrations complete$(NC)"

makemigrations: ## Create new migrations
	@echo "$(BLUE)Creating migrations...$(NC)"
	$(PYTHON) manage.py makemigrations
	@echo "$(GREEN)Migrations created$(NC)"

resetdb: ## Reset database (WARNING: destroys all data)
	@echo "$(RED)WARNING: This will delete all data!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	rm -f data/mtga_stats.db
	$(PYTHON) manage.py migrate
	@echo "$(GREEN)Database reset complete$(NC)"

# =============================================================================
# Application
# =============================================================================

run: ## Run development server
	@echo "$(BLUE)Starting development server...$(NC)"
	@echo "$(GREEN)Open http://127.0.0.1:8000/ in your browser$(NC)"
	$(PYTHON) manage.py runserver

shell: ## Open Django shell
	$(PYTHON) manage.py shell

createsuperuser: ## Create admin superuser
	$(PYTHON) manage.py createsuperuser

# =============================================================================
# Data Import
# =============================================================================

download-cards: ## Download Scryfall bulk card data
	@echo "$(BLUE)Downloading Scryfall card data...$(NC)"
	@echo "$(YELLOW)This may take a few minutes (~350MB download)$(NC)"
	$(PYTHON) manage.py download_cards
	@echo "$(GREEN)Card data downloaded$(NC)"

import-log: ## Import Player.log file (usage: make import-log LOG=/path/to/Player.log)
ifndef LOG
	@echo "$(RED)Error: LOG path not specified$(NC)"
	@echo "Usage: make import-log LOG=/path/to/Player.log"
	@exit 1
endif
	@echo "$(BLUE)Importing log file: $(LOG)$(NC)"
	$(PYTHON) manage.py import_log $(LOG)
	@echo "$(GREEN)Import complete$(NC)"

import-default: ## Import default log file (data/Player.log)
	@echo "$(BLUE)Importing data/Player.log...$(NC)"
	$(PYTHON) manage.py import_log data/Player.log
	@echo "$(GREEN)Import complete$(NC)"

# =============================================================================
# Code Quality
# =============================================================================

format: ## Format code with black and isort
	@echo "$(BLUE)Formatting code with isort...$(NC)"
	$(VENV_BIN)/isort src/ stats/ tests/ mtgas_project/
	@echo "$(BLUE)Formatting code with black...$(NC)"
	$(VENV_BIN)/black src/ stats/ tests/ mtgas_project/
	@echo "$(GREEN)Code formatted$(NC)"

format-check: ## Check code formatting without making changes
	@echo "$(BLUE)Checking code formatting...$(NC)"
	$(VENV_BIN)/isort --check-only --diff src/ stats/ tests/ mtgas_project/
	$(VENV_BIN)/black --check --diff src/ stats/ tests/ mtgas_project/
	@echo "$(GREEN)Format check complete$(NC)"

lint: ## Run flake8 linter
	@echo "$(BLUE)Running flake8 linter...$(NC)"
	$(VENV_BIN)/flake8 src/ stats/ tests/ mtgas_project/
	@echo "$(GREEN)Linting complete$(NC)"

lint-css: ## Run stylelint on CSS files
	@echo "$(BLUE)Running stylelint...$(NC)"
	npx stylelint 'stats/static/css/**/*.css'
	@echo "$(GREEN)CSS linting complete$(NC)"

lint-css-fix: ## Fix CSS linting issues automatically
	@echo "$(BLUE)Fixing CSS with stylelint...$(NC)"
	npx stylelint 'stats/static/css/**/*.css' --fix
	@echo "$(GREEN)CSS fixed$(NC)"

check: format-check lint lint-css ## Run all code quality checks (format + lint)
	@echo "$(GREEN)All checks passed$(NC)"

# =============================================================================
# Testing
# =============================================================================

test: ## Run all tests
	@echo "$(BLUE)Running tests...$(NC)"
	$(VENV_BIN)/pytest
	@echo "$(GREEN)Tests complete$(NC)"

test-verbose: ## Run tests with verbose output
	@echo "$(BLUE)Running tests (verbose)...$(NC)"
	$(VENV_BIN)/pytest -v --tb=long
	@echo "$(GREEN)Tests complete$(NC)"

test-cov: ## Run tests with coverage report
	@echo "$(BLUE)Running tests with coverage...$(NC)"
	$(VENV_BIN)/pytest --cov=stats --cov=src --cov-report=html --cov-report=term
	@echo "$(GREEN)Coverage report generated in htmlcov/$(NC)"

test-parser: ## Run only parser tests
	$(VENV_BIN)/pytest tests/test_parser.py -v

test-models: ## Run only model tests
	$(VENV_BIN)/pytest tests/test_models.py -v

test-views: ## Run only view tests
	$(VENV_BIN)/pytest tests/test_views.py -v

# =============================================================================
# Cleanup
# =============================================================================

clean: ## Remove Python artifacts and cache files
	@echo "$(BLUE)Cleaning up...$(NC)"
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name ".coverage" -delete 2>/dev/null || true
	@echo "$(GREEN)Cleanup complete$(NC)"

clean-all: clean ## Remove all generated files including database and cache
	@echo "$(RED)WARNING: This will delete database and card cache!$(NC)"
	@read -p "Are you sure? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	rm -f data/mtga_stats.db
	rm -rf data/cache/
	@echo "$(GREEN)Full cleanup complete$(NC)"

# =============================================================================
# Documentation
# =============================================================================

docs: ## Generate documentation (placeholder)
	@echo "$(BLUE)Documentation is in README.md and docs/$(NC)"

# =============================================================================
# Combined Commands
# =============================================================================

all: setup download-cards ## Full setup including card data download
	@echo "$(GREEN)Full setup complete!$(NC)"
	@echo "Run 'make run' to start the development server"

ci: check test ## Run all CI checks (format, lint, test)
	@echo "$(GREEN)All CI checks passed$(NC)"

