# Rental Housing Law Navigator - dev shortcuts.
.PHONY: help setup dev-api dev-web seed migrate test lint fmt docker deploy

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup:  ## Install backend and frontend dependencies
	cd backend && uv venv --python 3.12 && uv pip install -e ".[dev]"
	cd frontend && npm install

dev-api:  ## Run FastAPI with reload on :8080
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8080

dev-web:  ## Run Next.js on :3000
	cd frontend && npm run dev

seed:  ## Load corpus + addresses into the database
	cd backend && .venv/bin/python scripts/seed.py

migrate:  ## Apply database migrations
	cd backend && .venv/bin/alembic upgrade head

test:  ## Run backend tests
	cd backend && .venv/bin/python -m pytest -q

lint:  ## Lint and typecheck both sides
	cd backend && .venv/bin/ruff check app scripts tests
	cd frontend && npx tsc --noEmit

fmt:  ## Format backend
	cd backend && .venv/bin/ruff format app scripts tests
	cd backend && .venv/bin/ruff check --fix app scripts tests

docker:  ## Build the backend image (context = repo root)
	docker build -f backend/Dockerfile -t rhln-api:local .

deploy:  ## Deploy the backend to Fly
	flyctl deploy --remote-only --config backend/fly.toml
