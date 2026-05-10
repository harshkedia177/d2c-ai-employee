.PHONY: install up down test fmt seed migrate

install:
	uv sync

up:
	docker compose up -d postgres redis mock_saas

down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m mock_saas.seed.generate --merchants=1

test:
	uv run pytest -q

fmt:
	uv run ruff check --fix .
	uv run ruff format .
