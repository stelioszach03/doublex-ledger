PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

.PHONY: install dev run migrate makemigration test bench compose-up compose-down up db-up seed fmt

# Install dependencies (system Python or inside venv if present)
install:
	@if [ -d "$(VENV)" ]; then \
		echo "Using existing venv: $(VENV)"; \
		. $(VENV)/bin/activate; $(PIP) install -r requirements.txt; \
	else \
		echo "Installing to system environment (no .venv found)"; \
		pip install -r requirements.txt; \
	fi

# Dev server (reload)
dev:
	uvicorn ledger.apps.api.main:app --reload --host 0.0.0.0 --port 8000

# Backwards-compat alias
run: dev

test:
	pytest -q

# Database migrations (alembic)
migrate:
	alembic upgrade head

makemigration:
	@if [ -z "$(m)" ]; then \
		echo 'Usage: make makemigration m="your message"' && exit 1; \
	fi; \
	alembic revision --autogenerate -m "$(m)"

# Locust bench (headless)
bench:
	locust -f ledger/bench/locust/locustfile.py --headless -u 200 -r 200 -t 2m

# Docker Compose helpers
compose-up:
	docker compose up -d --build

compose-down:
	docker compose down -v

up: compose-up

db-up:
	docker compose up -d postgres

seed:
	$(PYTHON) -m ledger.db.seeds
