# Makefile for houses — Browser-to-Spreadsheet Ingestion Engine
.PHONY: help setup run test test-all test-integration test-e2e e2e lint format clean

# Variables
PYTHON := .venv/bin/python
UV := uv
RUFF := .venv/bin/ruff
PYTEST := .venv/bin/pytest

# Colors
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m

help:
	@echo "Available commands:"
	@echo "  ${GREEN}make setup${NC}              Create venv and install dependencies"
	@echo "  ${GREEN}make run${NC}                Start the FastAPI server (uvicorn)"
	@echo "  ${GREEN}make test${NC}               Run unit + integration tests (fast, mocked APIs)"
	@echo "  ${GREEN}make test-all${NC}           Run all tests including e2e (hits real APIs)"
	@echo "  ${GREEN}make test-integration${NC}   Run only integration tests"
	@echo "  ${GREEN}make test-e2e${NC}           Run only end-to-end tests (hits real APIs)"
	@echo "  ${GREEN}make e2e${NC}                Alias for test-e2e"
	@echo "  ${GREEN}make lint${NC}               Check code quality with ruff"
	@echo "  ${GREEN}make format${NC}             Auto-fix formatting issues"
	@echo "  ${GREEN}make coverage${NC}           Run tests with coverage report"
	@echo "  ${GREEN}make clean${NC}              Clean up generated files"

setup:
	@$(UV) --version >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
	@$(UV) sync --all-extras
	@echo "${GREEN}✓ Setup complete${NC}"

run: setup
	@echo "${YELLOW}Starting houses server on http://127.0.0.1:8080${NC}"
	@$(UV) run uvicorn houses.server:app --host 127.0.0.1 --port 8080 --reload

test: setup lint
	@$(PYTEST) tests/unit/ tests/integration/ -q 

test-e2e: setup lint
	@$(PYTEST) tests/e2e/ -m e2e -q

e2e: test-e2e

coverage: setup
	@$(UV) run coverage run -m pytest tests/ -q --tb=short
	@$(UV) run coverage report -m
	@$(UV) run coverage html
	@echo "${GREEN}Coverage report: htmlcov/index.html${NC}"

lint: setup
	@$(RUFF) check houses/ tests/

format: setup
	@$(RUFF) check --fix houses/ tests/
	@$(RUFF) format houses/ tests/

clean:
	@rm -rf .venv htmlcov/
	@rm -f .coverage coverage.xml
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "${GREEN}✓ Cleaned${NC}"
