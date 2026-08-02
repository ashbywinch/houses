# Makefile for houses — Browser-to-Spreadsheet Ingestion Engine
.PHONY: help setup install-hooks run frontend-dev frontend-build frontend-setup test test-all test-integration test-e2e e2e lint format clean reset-db commute-shed commute-searches commute-validate

# Variables
PYTHON := .venv/bin/python
UV := $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
RUFF := .venv/bin/ruff
PYTEST := .venv/bin/pytest
BASEDPYRIGHT := .venv/bin/basedpyright
OMP_CONFIG_DIR ?= $(HOME)/Documents/code/omp-config


# Colors
GREEN := \033[0;32m
YELLOW := \033[1;33m
RED := \033[0;31m
NC := \033[0m

help:
	@echo "Available commands:"
	@echo "  ${GREEN}make setup${NC}              Create venv and install dependencies"
	@echo "  ${GREEN}make run-prod${NC}           Serve backend + built frontend (no Vite)"
	@echo "  ${GREEN}make run${NC}                Start backend + frontend dev server (local + LAN)"
	@echo "  ${GREEN}make login${NC}              Google sign-in (device flow); saves auth state for captures"
	@echo "  ${GREEN}make test${NC}               Run unit + integration tests (fast, mocked APIs)"
	@echo "  ${GREEN}make test-all${NC}           Run all tests including e2e (hits real APIs)"
	@echo "  ${GREEN}make test-integration${NC}   Run only integration tests"
	@echo "  ${GREEN}make test-e2e${NC}           Run only end-to-end tests (hits real APIs)"
	@echo "  ${GREEN}make e2e${NC}                Alias for test-e2e"
	@echo "  ${GREEN}make lint${NC}               Check code quality with ruff"
	@echo "  ${GREEN}make format${NC}             Auto-fix formatting issues"
	@echo "  ${GREEN}make coverage${NC}           Run tests with coverage report"
	@echo "  ${GREEN}make clean${NC}              Clean up generated files"
	@echo "  ${GREEN}make commute-shed${NC}       One-off TfL batch → data/commute/station_shed.json (FORCE=1 to re-run)"
	@echo "  ${GREEN}make commute-searches${NC}   Offline: build data/commute/searches.json + .txt"
	@echo "  ${GREEN}make commute-validate${NC}   Validate searches + run commute tests"

setup: frontend-setup install-hooks
	@$(UV) --version >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh
	@$(UV) sync --all-extras
	@echo "${GREEN}✓ Setup complete${NC}"

install-hooks:
	@mkdir -p .git/hooks
	@if [ -f .git/hooks/pre-commit ] && ! cmp -s scripts/pre-commit .git/hooks/pre-commit; then \
		echo "${YELLOW}An existing pre-commit hook differs — move it aside and re-run 'make install-hooks'${NC}"; \
		exit 1; \
	fi
	@cp scripts/pre-commit .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "${GREEN}✓ Pre-commit hook installed (ruff on staged Python files)${NC}"

omp-config-install:
	$(MAKE) -C $(OMP_CONFIG_DIR) install

LAN_IP := $(shell ip -4 route get 1 2>/dev/null | awk '{print $$7; exit}')

_PORT_CHECK = if lsof -ti :$(1) >/dev/null 2>&1; then echo "${YELLOW}Port $(1) already in use — use 'make stop' first${NC}"; exit 1; fi

run: setup
	@if [ -z "$(LAN_IP)" ]; then echo "${RED}Could not detect LAN IP${NC}"; exit 1; fi
	@$(call _PORT_CHECK,8765)
	@$(call _PORT_CHECK,5173)
	@echo "${YELLOW}Backend: http://$(LAN_IP):8765  Frontend: http://$(LAN_IP).sslip.io:5173${NC}"
	@mkdir -p .logs
	@cd houses/frontend && HOUSES_HOST=0.0.0.0 HOUSES_PUBLIC_URL=http://$(LAN_IP).sslip.io:5173 HOUSES_FRONTEND_URL=http://$(LAN_IP).sslip.io:5173 \
		nohup npm run dev > "$(CURDIR)/.logs/frontend.log" 2>&1 & echo $$! > "$(CURDIR)/.logs/frontend.pid"
	@HOUSES_HOST=0.0.0.0 HOUSES_PUBLIC_URL=http://$(LAN_IP).sslip.io:5173 HOUSES_FRONTEND_URL=http://$(LAN_IP).sslip.io:5173 \
		nohup $(UV) run python -m houses > .logs/backend.log 2>&1 & echo $$! > .logs/backend.pid

stop:
	@echo "Stopping dev processes..."
	@lsof -ti :8765 2>/dev/null | xargs -r kill 2>/dev/null || true
	@lsof -ti :5173 2>/dev/null | xargs -r kill 2>/dev/null || true
	@rm -f .logs/backend.pid .logs/frontend.pid
	@echo "Stopped."

run-prod: setup frontend-build
	@echo "${YELLOW}Serving frontend build + backend on http://127.0.0.1:8765${NC}"
	@$(UV) run python -c "import uvicorn; from houses.config import settings; from houses.server import app; from fastapi.staticfiles import StaticFiles; from pathlib import Path; build_dir = Path('houses/frontend/dist'); if build_dir.exists(): app.mount('/', StaticFiles(directory=str(build_dir), html=True), name='frontend'); uvicorn.run(app, host=settings.host, port=settings.port, reload=False)"

login: setup
	@echo "${YELLOW}Requires servers running — use 'make run' first${NC}"
	@$(PYTHON) tools/capture_dom.py --login
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

test: setup lint typecheck
	$(PYTEST) tests/unit/ -q --tb=short
	$(PYTEST) tests/integration/ -q --tb=short
	cd houses/frontend && npm test

test-e2e: setup lint
	@$(PYTEST) tests/e2e/ -m e2e -q

e2e: test-e2e

coverage: setup
	@$(UV) run coverage run -m pytest tests/ -q --tb=short
	@$(UV) run coverage report -m
	@$(UV) run coverage html
	@echo "${GREEN}Coverage report: htmlcov/index.html${NC}"

lint: setup
	@$(RUFF) check houses/ tests/ tools/
	cd houses/frontend && npm run lint:css

typecheck: setup
	@$(BASEDPYRIGHT)

.PHONY: typecheck

format: setup
	@$(RUFF) check --fix houses/ tests/
	@$(RUFF) format houses/ tests/

clean:
	@rm -rf .venv htmlcov/
	@rm -f .coverage coverage.xml
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete

reset-db:  # Reset DAG database but PRESERVE API cache
	@rm -f data/houses.db
	@echo "data/houses.db removed (API cache in data/api_cache/ left intact)"
	@echo "${GREEN}✓ Cleaned${NC}"

# ── Rightmove commute search toolchain (docs/rightmove-commute-monitor.md) ──

commute-shed:
	@if [ -f data/commute/station_shed.json ] && [ -z "$(FORCE)" ]; then \
		echo "${YELLOW}data/commute/station_shed.json exists — use 'make commute-shed FORCE=1' to re-run the one-off TfL batch${NC}"; \
		exit 1; \
	fi
	@$(PYTHON) -m tools.commute.station_shed $(if $(FORCE),--force,)

commute-searches:
	@$(PYTHON) -m tools.commute.searches

commute-validate: commute-searches
	@$(PYTHON) -m tools.commute.validate
	@$(PYTEST) tests/unit/test_commute_rightmove_url.py tests/unit/test_commute_station_shed.py \
		tests/unit/test_commute_tile.py tests/unit/test_commute_searches.py tests/unit/test_commute_validate.py -q --tb=short
