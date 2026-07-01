# Makefile for houses — Browser-to-Spreadsheet Ingestion Engine
.PHONY: help setup run frontend-dev frontend-build dev test test-all test-integration test-e2e e2e lint format clean reset-db

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
	@echo "  ${GREEN}make run${NC}                Start backend + frontend dev server"
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

run: setup frontend-setup
	@echo "${YELLOW}Backend: http://127.0.0.1:8080  Frontend: http://localhost:5173${NC}"
	@trap 'kill 0' EXIT; \
		cd houses/frontend && npm run dev & \
		$(UV) run uvicorn houses.server:app --host 0.0.0.0 --port 8080 --reload

FRONTEND := houses/frontend
NPM := npm

frontend-setup:
	@cd $(FRONTEND) && $(NPM) install
	@echo "${GREEN}✓ Frontend dependencies installed${NC}"

frontend-dev: frontend-setup
	@echo "${YELLOW}Starting Vue dev server on http://localhost:5173${NC}"
	@cd $(FRONTEND) && $(NPM) run dev

frontend-build: frontend-setup
	@cd $(FRONTEND) && $(NPM) run build
	@echo "${GREEN}✓ Frontend build complete${NC}"

test: setup lint
	$(PYTEST) tests/unit/ -q --tb=short
	$(PYTEST) tests/integration/ -q --tb=short
	cd houses/frontend && npx vue-tsc -b --noEmit
	cd houses/frontend && npx vitest run

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

reset-db:  # Reset DAG database but PRESERVE API cache
	@rm -f data/dag.db
	@echo "data/dag.db removed (API cache in data/cache/ left intact)"
	@echo "${GREEN}✓ Cleaned${NC}"
