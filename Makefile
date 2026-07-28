# Makefile for houses — Browser-to-Spreadsheet Ingestion Engine
.PHONY: help setup run frontend-dev frontend-build frontend-setup test test-all test-integration test-e2e e2e lint format clean reset-db

# Variables
PYTHON := .venv/bin/python
UV := uv
RUFF := .venv/bin/ruff
PYTEST := .venv/bin/pytest
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

omp-config-install:
	$(MAKE) -C $(OMP_CONFIG_DIR) install

LAN_IP := $(shell hostname -I | awk '{print $$1}')

run: setup frontend-setup
	@if [ -z "$(LAN_IP)" ]; then echo "${RED}Could not detect LAN IP${NC}"; exit 1; fi
	@echo "${YELLOW}Backend: http://$(LAN_IP):8080  Frontend: http://$(LAN_IP).sslip.io:5173${NC}"
	@mkdir -p .logs; \
		HOUSES_HOST=0.0.0.0 HOUSES_PUBLIC_URL=http://$(LAN_IP).sslip.io:5173 HOUSES_FRONTEND_URL=http://$(LAN_IP).sslip.io:5173 \
		nohup sh -c 'cd houses/frontend && npm run dev' > .logs/frontend.log 2>&1 & echo $$! > .logs/frontend.pid; \
		HOUSES_HOST=0.0.0.0 HOUSES_PUBLIC_URL=http://$(LAN_IP).sslip.io:5173 HOUSES_FRONTEND_URL=http://$(LAN_IP).sslip.io:5173 \
		nohup uv run python -m houses > .logs/backend.log 2>&1 & echo $$! > .logs/backend.pid;

stop:
	@echo "Stopping dev processes..."
	@test -f .logs/backend.pid && kill $$(cat .logs/backend.pid) 2>/dev/null && rm .logs/backend.pid || true
	@test -f .logs/frontend.pid && kill $$(cat .logs/frontend.pid) 2>/dev/null && rm .logs/frontend.pid || true
	@echo "Stopped."

run-prod: setup frontend-build
	@echo "${YELLOW}Serving frontend build + backend on http://127.0.0.1:8080${NC}"
	@$(UV) run python -c "import uvicorn; from houses.config import settings; from houses.server import app; from fastapi.staticfiles import StaticFiles; from pathlib import Path; build_dir = Path('houses/frontend/dist'); if build_dir.exists(): app.mount('/', StaticFiles(directory=str(build_dir), html=True), name='frontend'); uvicorn.run(app, host=settings.host, port=settings.port, reload=False)"
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

test: setup lint frontend-setup
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
	@$(RUFF) check houses/ tests/
	cd houses/frontend && npm run lint:css

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
