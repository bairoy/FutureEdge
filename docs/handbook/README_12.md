# README 12 — PROJECT AUTOMATION, TESTING & MAKEFILE COMMANDS

This guide details the commands defined in the project's root-level [Makefile](file:///Users/baijuyadav/Desktop/futureedge/Makefile). It serves as a reference manual explaining **what command** to execute, **when** to execute it, and the **reason** behind each target's execution.

---

## 🚀 The Core Commands (Quick Reference)

| Goal / Scenario | Command to Run | Underlying Action |
| :--- | :--- | :--- |
| **Run the project instantly** | `make` or `make run` | Launches the services via `docker compose up` using existing cached images (fast startup). |
| **Clean & Rebuild the project** | `make run-dev` | Rebuilds the Docker images from scratch and runs them (`docker compose up --build`). |
| **Run unit & integration tests** | `make test` | Runs the Python/pytest suite with PYTHONPATH set. |
| **Verify frontend type compiler** | `make type-check` | Performs TypeScript static type checking inside Next.js. |
| **Initialize database tables** | `make db-create` | Creates raw database schemas in PostgreSQL. |
| **Seed initial admin credentials** | `make db-seed` | Runs interactive script to seed Admin user. |
| **Wipe only simulated trades** | `make db-wipe` | Wipes paper/fake trade entries from trade ledger. |
| **Reset entire trade history** | `make db-reset` | Clears all trade records (live and paper). |
| **Launch FastAPI locally** | `make run-backend` | Runs uvicorn backend locally inside `.venv`. |
| **Launch Next.js locally** | `make run-frontend` | Runs Next.js frontend development server locally. |

---

## 📖 In-Depth Command Explanations & Scenarios

### 1. Instant Startup (No-Build Mode)
* **Command**: `make` or `make run`
* **When to run**: Every time you begin local development and want to bring up the database, caches, FastAPI backend, and Next.js frontend quickly.
* **Reason**: Building Docker images from scratch with `docker compose up --build` compiles dependencies, compiles frontend nodes, and downloads packages, taking **5 to 10 minutes**. The default `make` command bypasses this compilation and spins up the environment in **under 10 seconds** using cached images.

### 2. Environmental Rebuild (Fresh Setup)
* **Command**: `make run-dev`
* **When to run**: 
  1. The first time setting up the repository.
  2. After adding or modifying backend packages in `requirements.txt`.
  3. After changing Node packages or script targets in `package.json`.
  4. After making modifications to `Dockerfile` or `docker-compose.yml`.
* **Reason**: Running this command calls `docker compose up --build`, forcing Docker to ignore cached layers and rebuild the container images from scratch to reflect your package and configuration updates.

### 3. Database Schema Initialization
* **Commands**:
  1. `make db-create` (to create tables)
  2. `make db-seed` (to seed the admin account)
* **When to run**: After the Docker containers are successfully started for the first time on your machine.
* **Reason**: Before the server can log trades or authenticate profiles, PostgreSQL tables must exist.
  - `make db-create` dynamically generates tables using SQLAlchemy ORM.
  - `make db-seed` prompts you in the terminal for your name, email, and password to register a default Admin user so you can log into the dashboard UI.

### 4. Running the Test Suite
* **Command**: `make test`
* **When to run**: Before committing updates to git, opening pull requests, or after modifying agents or routers.
* **Reason**: Automatically sets `PYTHONPATH=backend` so modules resolve correctly, then runs the pytest suite (`./.venv/bin/pytest tests`). This guarantees all 63 unit and integration checks pass.

### 5. Frontend Type Verification
* **Command**: `make type-check`
* **When to run**: Before executing production frontend builds or checking typescript parameters.
* **Reason**: Starts Next.js TypeScript compiler checks (`npm run type-check` inside `frontend/`), catching any static type conflicts, mismatching interfaces, or broken imports that would crash the production bundler.

### 6. Wiping Sandbox Trades
* **Command**: `make db-wipe`
* **When to run**: When you want to clear simulated paper trades to clean up charts and history tables, but keep active user accounts and live trade records intact.
* **Reason**: Runs `python -m app.scripts.wipe_fake_trades` which runs SQL deletions specifically targeting records where `broker == 'paper'`, allowing safe sandbox cleaning.

### 7. Resetting All Trade Data
* **Command**: `make db-reset`
* **When to run**: When you want to wipe the slate completely clean and reset the Kelly criterion calculations back to defaults.
* **Reason**: Runs `python -m app.scripts.reset_trades` which truncates all trade histories from the SQL ledger.

### 8. Running Background Containers (Daemon Mode)
* **Commands**:
  - `make docker-up` (runs containers in background)
  - `make docker-down` (stops background containers)
  - `make docker-logs` (tails live service logs)
* **When to run**: When you want to keep the terminal shell free while the servers are running.
* **Reason**: Provides quick aliases to keep services running silently in the background while enabling real-time terminal logging output with `docker-logs`.

### 9. Caches & Build Cleanups
* **Command**: `make clean`
* **When to run**: When facing caching issues or running low on host disk space.
* **Reason**: Clears python compile directories (`__pycache__`), pytest caches (`.pytest_cache`), and next.js build directories (`.next`).
