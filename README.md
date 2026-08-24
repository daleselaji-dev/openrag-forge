# OpenRAG Forge

OpenRAG Forge is a lightweight, inspectable RAG knowledge-base framework. Upload documents, let the router choose a parser, assemble a typed RAG Recipe, run it, and inspect the complete evidence Trace.

The project combines a small local-first core with optional production profiles. SQLite and local artifacts are the default truth source; PostgreSQL, MinIO, Redis and Celery are optional adapters. Qdrant is a derived retrieval index. Models are accessed through an OpenAI-compatible endpoint such as LM Studio, llama.cpp, vLLM or a hosted provider.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn openrag_forge.app:app --reload --port 18000
```

Open `http://localhost:18000`. The Lite profile starts without Postgres, MinIO, Redis, Neo4j or an Agent worker. Point `.env` at a running Qdrant and OpenAI-compatible model service when you are ready to index and answer.

To run the full Lite stack with Qdrant:

```powershell
docker compose up -d qdrant api
```

The API is intentionally usable before model services are online. Upload and parse still work; indexing reports `deferred` with the next action instead of hiding an exception. Once LM Studio and Qdrant are available, rebuild a knowledge-base index with:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:18000/api/v1/knowledge-bases/default/index/rebuild
```

The first public framework baseline includes:

- upload and content-aware parser routing for text, Markdown, HTML, PDF, Office XML, CSV/XLSX and JSON;
- Block and Chunk persistence with source version, SHA-256 and parser reason codes;
- typed Recipe compilation for Dense, Hybrid, Operations and Controlled Agent examples;
- Preview, real run, Trace and downloadable Evidence Capsule APIs;
- React + React Flow workbench with request-abort protection when switching Recipes quickly.
- Scenario Gallery presets for customer support, internal policy and controlled customer Agent demonstrations;
- `custom_ingest` Recipe for selecting an Embedding model before uploading user documents.

## What makes it different

- **Recipe Compiler**: a drag-and-drop graph is compiled into a typed, immutable, hash-addressed Recipe before it can run.
- **Evidence Capsule**: every run exports configuration, model IDs, evidence, citations, safety decisions and Trace as one reproducible artifact.
- **Parser transparency**: upload routing, fallback decisions, blocks and chunks are visible and can be reprocessed without overwriting the source version.
- **Profiles**: `lite`, `production`, `observability`, `graph`, `multimodal` and `agent` add capability without making the default install heavy.

## Status

The current branch is the framework extraction baseline. The CFPB consumer-support implementation lives in `packs/customer-support-cfpb` and is intentionally separate from the generic core.

See `docs/architecture.md` and `docs/recipes.md` for the public contracts.
See `docs/production-playbook.md` for production-grade deployment, trace and tuning guidance.
See `docs/benchmark-smoke-v0.1.md` for the reproducible local benchmark and its limitations.
See `docs/golden-eval-v0.1-dev.md` for labeled retrieval, citation and safety evaluation.
