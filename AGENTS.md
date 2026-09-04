# AGENTS

## Cursor Cloud specific instructions

OpenRAG Forge is a Python FastAPI backend (`src/openrag_forge`, package `openrag_forge`) plus a
React + Vite + React Flow frontend in `web/`. The default `lite` profile is fully self-contained:
SQLite + local files under `data/` are the source of truth. Qdrant, LM Studio / OpenAI-compatible
model endpoints, Postgres, MinIO, Redis and Celery are all OPTIONAL adapters — the app runs and the
core upload → parse → retrieve → answer flow works without them (retrieval falls back to lexical
scoring and answers become extractive). `GET /api/v1/health` reporting `qdrant`/`lm_studio` as
`unreachable` is expected in this environment and is not a failure.

Python deps are installed into a virtualenv at `.venv` (system Python is PEP 668 externally-managed,
so a venv is required; `python3.12-venv` must be present). Always invoke backend tooling through the
venv, e.g. `.venv/bin/uvicorn`, `.venv/bin/pytest`, `.venv/bin/ruff`, `.venv/bin/python`.

Services and commands (run from the repo root unless noted):
- Backend (dev, hot reload): `.venv/bin/uvicorn openrag_forge.app:app --reload --port 18000`.
  Serves the API under `/api/v1/...` and, when `web/dist` exists, also serves the built SPA at `/`.
- Frontend (dev, HMR): `cd web && npm run dev -- --port 5173`. IMPORTANT: `npm run dev`'s default
  port is 18000, which collides with the backend, so override the port. Vite proxies `/api` to
  `http://127.0.0.1:18000`, so the backend must be running on 18000 for the dev UI to work. Open the
  dev UI at http://localhost:5173.
- Tests: `.venv/bin/pytest -q` (CI gate). `pyproject.toml` sets `pythonpath = ["src"]`, so tests
  run without an editable install, but the editable install is already in the venv.
- Lint: `.venv/bin/ruff check .`. Ruff is available but the repo currently has pre-existing ruff
  findings and CI does NOT run ruff (see `.github/workflows/ci.yml`, which only runs pytest + the
  web build). Do not treat existing ruff findings as regressions.
- Web build (CI gate): `cd web && npm run build` (`tsc -b && vite build`, output in `web/dist`).
- Optional eval/benchmark scripts (require a running backend): pass the base URL explicitly since
  their default is port 18003, e.g.
  `.venv/bin/python scripts/run_framework_benchmark.py --base-url http://127.0.0.1:18000`.

Config comes from `.env` (copied from `.env.example`), read via the `OPENRAG_` env prefix.
