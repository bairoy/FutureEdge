# FutureEdge — Project Automation & Testing Makefile
# Timezone: Asia/Kolkata (IST) for NSE markets
# Usage:
#   make help         - Display list of commands
#   make test         - Run full pytest test suite
#   make type-check   - Run TypeScript compiler checks on Next.js frontend
#   make run-dev      - Run development server using Docker Compose
#   make run-backend  - Run FastAPI server locally in .venv
#   make run-frontend - Run Next.js frontend locally
#   make db-create    - Initialize SQL database tables
#   make db-seed      - Create initial admin account
#   make db-reset     - Remove all paper and live trades from database
#   make db-wipe      - Wipe only paper/fake trading data

.PHONY: run help test type-check run-dev run-backend run-frontend db-create db-seed db-reset db-wipe docker-up docker-down docker-logs clean

# Default target runs docker-compose up without rebuilding
run:
	@echo "🐳 Starting development environment via Docker Compose (no build)..."
	docker compose up

# Default Python binary
PYTHON = ./.venv/bin/python
PYTEST = ./.venv/bin/pytest

help:
	@echo "======================================================================"
	@echo "                  FutureEdge Trading System Commands                  "
	@echo "======================================================================"
	@echo "Available Makefile targets:"
	@echo "  run            - Spin up development containers quickly (Default)"
	@echo "  test           - Run Python/pytest backend test suite"
	@echo "  type-check     - Run type compiler check inside frontend"
	@echo "  run-dev        - Spin up all development containers (Docker Compose)"
	@echo "  run-backend    - Start FastAPI uvicorn server locally"
	@echo "  run-frontend   - Start Next.js development server locally"
	@echo "  db-create      - Initialize PostgreSQL database tables"
	@echo "  db-seed        - Seed initial admin user credentials"
	@echo "  db-reset       - Reset all trade histories (paper + live)"
	@echo "  db-wipe        - Wipe paper trading logs only"
	@echo "  docker-up      - Run compose services in background"
	@echo "  docker-down    - Tear down compose services and networks"
	@echo "  docker-logs    - Tail docker container logs in real-time"
	@echo "  clean          - Remove python caches and temporary log stores"
	@echo "======================================================================"

# --- TESTING & QUALITY ASSURANCE ---

test:
	@echo "🚀 Running Python/FastAPI unit and integration test suite..."
	PYTHONPATH=backend $(PYTEST) tests

type-check:
	@echo "🔍 Running TypeScript type-checking inside Next.js frontend..."
	cd frontend && npm run type-check

# --- DEVELOPMENT SERVERS ---

run-dev:
	@echo "🐳 Starting development environment via Docker Compose..."
	docker compose up --build

run-backend:
	@echo "⚡ Starting FastAPI application locally on port 8000..."
	PYTHONPATH=backend ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

run-frontend:
	@echo "💻 Starting Next.js frontend dev server locally on port 3000..."
	cd frontend && npm run dev

# --- DATABASE OPS ---

db-create:
	@echo "🗄️ Initializing database tables..."
	PYTHONPATH=backend $(PYTHON) -m app.scripts.create_tables

db-seed:
	@echo "👤 Seeding database admin account..."
	PYTHONPATH=backend $(PYTHON) -m app.scripts.create_admin

db-reset:
	@echo "⚠️ Resetting all trade records (live and paper)..."
	PYTHONPATH=backend $(PYTHON) -m app.scripts.reset_trades

db-wipe:
	@echo "🧹 Wiping paper trades from database..."
	PYTHONPATH=backend $(PYTHON) -m app.scripts.wipe_fake_trades

# --- DOCKER CONVENIENCE TARGETS ---

docker-up:
	@echo "🐳 Spinning up docker containers in background..."
	docker compose up -d

docker-down:
	@echo "🐳 Stopping and removing docker containers..."
	docker compose down

docker-logs:
	@echo "🐳 Tailing logs..."
	docker compose logs -f

# --- SYSTEM CLEANING ---

clean:
	@echo "🧹 Cleaning Python/pytest caches and temporary files..."
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name ".next" -exec rm -rf {} +
	@echo "✨ Workspace clean!"
