.PHONY: help install fmt lint type test test-pg cov up down migrate demo check clean

VENV := .venv
PY   := $(VENV)/bin/python
PG_TEST_DSN ?= postgresql+psycopg://tradeit:tradeit@localhost:5432/tradeit_test

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## create the venv and install the project with dev extras
	uv venv $(VENV)
	uv pip install --python $(PY) -e ".[dev]"

fmt:  ## format
	$(PY) -m ruff format src tests
	$(PY) -m ruff check --fix src tests migrations

lint:  ## lint without modifying
	$(PY) -m ruff check src tests migrations
	$(PY) -m ruff format --check src tests

type:  ## strict type check
	$(PY) -m mypy src/tradeit

test:  ## unit + sqlite integration tests
	$(PY) -m pytest -q

test-pg:  ## also run the PostgreSQL-specific tests
	TRADEIT_TEST_PG_DSN=$(PG_TEST_DSN) $(PY) -m pytest -q

cov:  ## test with coverage
	$(PY) -m pytest --cov=tradeit --cov-report=term-missing

check: lint type test  ## everything CI runs

up:  ## start postgres + redis
	docker compose up -d

down:  ## stop and remove containers
	docker compose down

migrate:  ## apply migrations
	$(PY) -m alembic upgrade head

demo:  ## load synthetic data and read it back point-in-time
	$(PY) -m tradeit.cli demo-ingest

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
