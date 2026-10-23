.PHONY: help install install-frontend install-terminal test test-terminal lint build build-python build-frontend build-terminal release-preflight release-preflight-build dev-web

PYTHON ?= python
PIP ?= $(PYTHON) -m pip
NPM ?= npm
BUN ?= bun

help:
	@printf '%s\n' \
		'VulnClaw development targets:' \
		'  make install          Install Python dev/web, frontend and terminal dependencies' \
		'  make test             Run the backend pytest suite' \
		'  make test-terminal    Check generated protocol, types and native OpenTUI tests' \
		'  make lint             Run Ruff checks' \
		'  make build            Build Python, Web and standalone terminal artifacts' \
		'  make build-python     Build the independent headless Python package' \
		'  make release-preflight Run release validation script' \
		'  make release-preflight-build Run release validation with artifact checks' \
		'  make dev-web          Start the frontend Vite dev server'

install:
	$(PIP) install -e ".[dev,web,pdf]"
	$(NPM) --prefix frontend ci
	$(BUN) install --cwd tui --frozen-lockfile

install-frontend:
	$(NPM) --prefix frontend ci

install-terminal:
	$(BUN) install --cwd tui --frozen-lockfile

test:
	$(PYTHON) -m pytest

test-terminal:
	$(BUN) run --cwd tui generate --check
	$(BUN) run --cwd tui typecheck
	VULNCLAW_PYTHON="$(shell $(PYTHON) -c 'import sys; print(sys.executable)')" $(BUN) test --cwd tui

lint:
	$(PYTHON) -m ruff check .

build: build-python build-frontend build-terminal

build-python:
	$(PYTHON) -m build

build-frontend:
	$(NPM) --prefix frontend run build

build-terminal:
	$(BUN) run --cwd tui build

release-preflight:
	$(PYTHON) scripts/release_preflight.py

release-preflight-build:
	$(PYTHON) scripts/release_preflight.py --build

dev-web:
	$(NPM) --prefix frontend run dev
