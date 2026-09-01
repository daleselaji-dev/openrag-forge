# OpenRAG Forge

OpenRAG Forge is a lightweight, inspectable RAG knowledge-base framework. Upload documents, let the router choose a parser, assemble a typed RAG Recipe, run it, and inspect the complete evidence Trace.

The project combines a small local-first core with optional production profiles. SQLite and local artifacts are the default truth source; PostgreSQL, MinIO, Redis and Celery are optional adapters. Qdrant is a derived retrieval index. Models are accessed through an OpenAI-compatible endpoint such as LM Studio, llama.cpp, vLLM or a hosted provider.

**Why it exists.** Most RAG demos fail at auditability, not at answering: nobody can say which document version, which chunk, which model and which safety decision produced a given answer. OpenRAG Forge makes every run reproducible and every decision inspectable — a drag-and-drop graph is compiled into a hash-addressed Recipe, and every run exports a single-file Evidence Capsule containing configuration, model IDs, evidence, citations, safety decisions and the full Trace.

## Quick start

Linux / macOS:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn openrag_forge.app:app --reload --port 18000
```

Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
uvicorn openrag_forge.app:app --reload --port 18000
```

Open `http://localhost:18000` — the full assembly workbench is served at `/` (build it once with `cd web && npm install && npm run build`, or run `npm run dev` for the Vite dev server which proxies `/api`). The Lite profile starts without Postgres, MinIO, Redis, Neo4j or an Agent worker: upload/parse, BM25 retrieval, extractive answers, Trace and Evidence Capsules all work fully offline. Point `.env` (or the workbench "导入 API / 模型" drawer) at a running Qdrant and OpenAI-compatible model service when you are ready for dense indexing and model-generated answers.

To run the full Lite stack with Qdrant:

```powershell
docker compose up -d qdrant api
```

The API is intentionally usable before model services are online. Upload and parse still work; indexing reports `deferred` with the next action instead of hiding an exception. Once LM Studio and Qdrant are available, rebuild a knowledge-base index with:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:18000/api/v1/knowledge-bases/default/index/rebuild
```

## How the system is designed

```text
Upload → Route → Parse → Blocks → Chunk → Enrich → Embed/Index (derived)
                                             ↓
Question → Recipe Compiler → Retrieve → Context → Generate → Policy Gate
                                             ↓
                              Evidence Capsule + Trace + Eval
```

Four ideas carry the whole architecture (full rationale, alternatives and trade-offs in [`docs/design.md`](docs/design.md)):

1. **One truth source, everything else derived.** Raw files, Blocks, Chunks, Recipes, Runs and Trace events live in SQLite + local artifacts. Qdrant points carry `chunk_id` payloads and query results are filtered against the truth source, so a stale index can never smuggle ghost evidence into an answer — a control that has already caught a real stale point during evaluation (see [`docs/experiments.md`](docs/experiments.md), experiment 4).
2. **Compile before run.** The React Flow canvas is only an authoring surface. The Recipe Compiler validates node types against a 27-node typed catalog, checks port compatibility on every edge, rejects undeclared cycles (corrective retrieval must use a `bounded_corrective` node with explicit `max_retries`) and assigns a canonical SHA-256 hash. Published Recipes are immutable; the hash in every Capsule pins results to an exact graph definition.
3. **Safety before generation.** A request-level policy gate detects refund-promise, legal-conclusion and account-decision requests (bilingual patterns) before retrieval runs; skipped nodes are recorded as `skipped`, and the refusal itself produces a full Evidence Capsule. The Controlled Agent recipe only ever emits a structured ticket draft with explicit `missing_fields` and `forbidden_actions`, and always stops at a human-approval node.
4. **Degrade loudly, never silently.** Retrieval falls back from Qdrant dense to a lexical scorer (recorded as `backend: lexical_fallback` in the Trace); generation falls back from the OpenAI-compatible endpoint to an extractive answer; answers with evidence but no `[S#]` markers are replaced by a deterministic cited summary (`citation_repair_fallback`). Every fallback is visible in the Trace, not hidden.

The framework baseline includes:

- upload and content-aware parser routing for text, Markdown, HTML, PDF, Office XML, CSV/XLSX and JSON, with per-decision `confidence` and `reason_codes`;
- Block and Chunk persistence with source version, SHA-256 and parser reason codes; reprocessing (with a different route or chunker config) bumps the version instead of overwriting the source;
- real metadata enrichment (title / language / keywords) applied at ingest and visible in chunk metadata, Qdrant payloads and evidence;
- typed Recipe compilation for an 11-recipe ladder from `v0_1_dense` to `v1_controlled_agent` (see [`docs/design.md`](docs/design.md) §3.2), plus Recipe JSON import/export;
- a real dataflow executor: built-in BM25 sparse retrieval, real RRF fusion, parent-child expansion, context token-budget, bounded corrective retry (hard cap 2), in-memory cache and rate-limit envelopes, and an OpenAI-compatible `/rerank` path — every fallback, passthrough and skip is recorded in the Trace with impact fields (see [`docs/modules.md`](docs/modules.md));
- Preview (dry compile), real run, run history, Trace and downloadable Evidence Capsule APIs;
- a model registry for OpenAI-compatible chat/embedding/reranker endpoints with optional per-model API keys stored server-side and masked in every response — connection profiles only, weights never enter the web app;
- a production multi-panel React + React Flow workbench: grouped node palette with per-block explanations and honest runtime badges, assembly canvas with trace highlighting, an inspector that always answers "trace / block role / how to tune", document & ParsedBlock viewer, import drawers and a download/self-host panel (request-abort protection preserved);
- Scenario Gallery presets for customer support, internal policy and controlled customer Agent demonstrations;
- `custom_ingest` Recipe whose node configs (route, chunker, enricher, embedding model) actually drive ingest;
- two reproducible evaluation harnesses (smoke benchmark and labeled Golden Eval) plus an on-line Eval API.

## Experiments and evaluation results

The repository treats evaluation as a first-class contract: any benchmark claim must ship with the dataset snapshot, recipe hash and the actual report (`CONTRIBUTING.md`). Two harnesses exist today; both run against the real API, Qdrant and a local LM Studio model.

### Smoke benchmark — including a deliberate before/after

The first run intentionally exposed two baseline failures (stopwords let an out-of-domain question retrieve an unrelated chunk; the local 7B model sometimes omitted citations and missed English high-risk wording). Both were fixed and re-measured on the same 7 cases. Source: [`docs/benchmark-smoke-v0.1.md`](docs/benchmark-smoke-v0.1.md); full story in [`docs/experiments.md`](docs/experiments.md).

| Metric | Before fixes | After fixes |
|---|---:|---:|
| Answerable citation presence | 0.500 | 1.000 |
| Refusal correctness | 0.333 | 1.000 |
| Out-of-domain evidence | 1 unrelated hit | 0 hits |

After-fix run: evidence rate 1.000, citation presence 1.000, refusal correctness 1.000, trace completeness 1.000, p50 623 ms, p95 9,619 ms (7 cases, 1 local document, `deepseek-r1-distill-qwen-7b` + `text-embedding-qwen3-embedding-0.6b`).

### Labeled Golden Eval v0.1-dev

Retrieval scored against evidence labels, refusal cases evaluated in a separate safety slice, and Wilson 95% intervals reported to keep small-sample honesty. Source: [`docs/golden-eval-v0.1-dev.md`](docs/golden-eval-v0.1-dev.md); dataset: `packs/customer-support-cfpb/evals/golden_v0_1_dev.jsonl` (3 answerable + 4 refusal cases).

| Metric | Value | 95% Wilson interval |
|---|---:|---:|
| Hit@k / Recall@k / MRR / nDCG | 1.000 | Hit@k: 0.438–1.000 |
| Citation validity / completeness | 1.000 | 0.438–1.000 |
| Refusal correctness | 1.000 | 0.510–1.000 |
| Trace completeness | 1.000 | 0.646–1.000 |
| p50 / p95 end-to-end | 690 ms / 8,050 ms | — |

**What these numbers do and do not prove.** They prove the framework can execute a fixed test set end-to-end, detect retrieval and safety-routing failures, and verify fixes on the same cases with fully traceable rows. They do **not** prove enterprise-grade quality: the corpus is one document, the answerable slice has three cases, and the intervals are intentionally wide. Release requirements (50+ reviewed cases, frozen corpus, human citation-support labels, V0.1/V0.2/V0.4 comparison on the same snapshot) are tracked in [`docs/evaluation.md`](docs/evaluation.md) §6.

Reproduce locally: `scripts/run_framework_benchmark.py` and `scripts/run_golden_eval.py` (commands in [`docs/evaluation.md`](docs/evaluation.md) §5).

## Production readiness（生产级能力）

这个仓库同时是一份"如何把项目做到生产级"的教学样例——关键代码、配置与部署清单里都有注释解释每个决策的动机：

- **可观测性**：OpenTelemetry 分布式追踪（业务 Recipe 节点与 Qdrant/LLM 调用都出现在 Jaeger 瀑布图）、JSON 结构化日志（自动携带 request_id / trace_id）、Prometheus 指标（含降级次数计数器）。一键启动本地栈：

```bash
OPENRAG_OTEL_ENABLED=true docker compose --profile observability up -d
# Jaeger http://localhost:16686 · Prometheus :19090 · Grafana :13000
```

- **可靠性**：`/livez` `/readyz` 健康探针、优雅关机（flush 追踪缓冲 + 关闭连接池）、出站连接池与逐类显式超时、SQLite WAL 并发加固、降级路径全程可见；
- **安全**：可选 API key 认证（常数时间比较）、可配置 CORS、限流兜底、安全响应头、非 root 容器；
- **配置管理**：全部走 `OPENRAG_*` 环境变量，`environment=production` 时自动输出生产就绪告警（`/api/v1/health` 的 `production_readiness` 字段）。

| 文档 | 内容 |
|---|---|
| `docs/configuration.md` | 每个配置项在哪里改、生产建议值 |
| `docs/observability.md` | 如何查看每个组件的 trace / 指标 / 日志 |
| `docs/deployment.md` | 本地 → Compose → Kubernetes 三档部署 |
| `docs/production-checklist.md` | 上线逐项清单（映射到仓库内落地位置） |

## Workbench（Control Room 工作台）

`web/` 是全高 Control Room 工作台：左轨切换 装配 / 数据 / 模型 / 场景，中间是 React Flow 画布，右侧节点检查器提供结构化配置表单（字段带 生效 / 不生效 标注，JSON 仅作高级模式），底部 Trace 面板包含 运行 Trace（真实 duration_ms + execution 标注，点击行高亮画布节点）、Ingest Trace 与 回答与证据（安全决策 + Evidence Capsule 下载）。

- 顶栏三态模式切换（localStorage 持久化）：`工作台`（干净控制室）/ `辅助教学`（7 步操作课：怎么用这个工作台）/ `面试讲解`（RAG 设计课，见下）。
- 诚实标注：画布、检查器与 Trace 共享同一份 live / fallback / stub 能力目录。BM25、RRF、上下文预算、父块扩展、有限纠错、缓存和限流已有本地真实基线；重排在绑定可达 `/rerank` 端点时 live，否则标注 fallback；图谱仍为 stub。Trace 的 `execution` 字段记录每一步真实发生了什么。

### 面试讲解模式（RAG 设计课）

顶栏切到「面试讲解」后，左侧出现可收起的讲解面板（内容在 `web/src/interview/`），五章可导航：**设计历程**（V0 整文入上下文 → V0.1 Naive RAG → 可观察 → 可装配 → 生产横切 → 当前诚实态，每代含业务动机 / 取舍 / 面试追问）、**方案对比**（Naive/Advanced/Modular 范式 + 托管套件 / 开源编排 / 检索中台 / GraphRAG 逐维对比与 PM 结论）、**环节地图**（点击环节即选中画布对应节点，检查器同步显示该环节的规格级讲解 + 可改配置；不在当前 Recipe 的环节可一键加入画布试装）、**核心件深讲**（向量库专章含 8-12 分钟口述提纲，另有 Embedding / Chunk / Rerank / 生成 / Eval 五章）、**实验手册**（改一项配置 → 再跑 → 看 Trace 哪一行变了，含「依赖 live/fallback」实验）。装配过程全程可定制，讲解跟着节点与 Trace 走；关闭讲解后界面回到干净控制室。

开发：`cd web && npm run dev`（Vite :5173，`/api` 代理到 :18000）；`npm run build` 后 FastAPI 直接伺服 `web/dist`。

## What makes it different

- **Recipe Compiler**: a drag-and-drop graph is compiled into a typed, immutable, hash-addressed Recipe before it can run.
- **Evidence Capsule**: every run exports configuration, model IDs, evidence, citations, safety decisions and Trace as one reproducible artifact.
- **Parser transparency**: upload routing, fallback decisions, blocks and chunks are visible and can be reprocessed without overwriting the source version.
- **Evaluation-first**: labeled metrics, safety slices and confidence intervals ship with the framework, and every eval row links back to a run's full Trace.
- **Profiles**: `lite`, `production`, `observability`, `graph`, `multimodal` and `agent` add capability without making the default install heavy.

## Where it can go next

The compiler catalog, Store port, profile extras and Pack isolation are deliberate extension slots. Highlights (full list with priorities in [`docs/roadmap.md`](docs/roadmap.md)):

- **P0** — replace the local BM25 baseline with a persistent sparse backend (Qdrant named sparse or OpenSearch), run a real local Cross-Encoder reranker, then compare V0.1 vs V0.2 vs V0.4 on a frozen Golden Set; freeze a 50+ case reviewed Golden Set and commit report snapshots.
- **P1** — PostgreSQL/MinIO implementations of the Store port, Celery-based async ingest, OpenTelemetry export of Trace events; an eval-driven Recipe A/B and regression-gate loop on top of recipe hashes.
- **P2** — graph-augmented retrieval (Neo4j extras declared), layout-aware PDF/multimodal retrieval, multi-step controlled Agent on the same `forbidden_actions` invariants, and more domain Packs following the CFPB pattern.

## Status and honest limitations

The current branch is the framework extraction baseline. The CFPB consumer-support implementation lives in `packs/customer-support-cfpb` and is intentionally separate from the generic core.

Known limitations, stated deliberately ([`docs/modules.md`](docs/modules.md) 末节): `graph_query` remains a compile-complete / runtime-stub node (needs Neo4j) and is labelled as such in the UI and Trace; `sparse_retrieve` runs on a real built-in BM25 backend but the Qdrant named-sparse backend is not wired yet; `reranker` performs a real `/rerank` call only when a compatible endpoint is registered and otherwise records an honest passthrough; cache/rate-limit are in-process (no Redis); the evaluation corpus is one local document; production Store adapters (Postgres/MinIO) are declared but not implemented; raw eval report JSON under `reports/` is gitignored — committed snapshots are a release requirement.

## Documentation map

| Document | Contents |
|---|---|
| [`docs/design.md`](docs/design.md) | Design rationale: every key decision, its alternatives and trade-offs, with source paths |
| [`docs/modules.md`](docs/modules.md) | 中文：每个产品板块的生产级模块设计（契约 / API / UI / 失败模式 / 调参旋钮 / Trace 呈现） |
| [`docs/architecture.md`](docs/architecture.md) | One-page architecture contract |
| [`docs/recipes.md`](docs/recipes.md) | Recipe DAG / compiler contract |
| [`docs/experiments.md`](docs/experiments.md) | Experiment log: failures, fixes, before/after numbers, and experiments still to run |
| [`docs/evaluation.md`](docs/evaluation.md) | Metric definitions, current results, reproduction steps, release gaps |
| [`docs/benchmark-smoke-v0.1.md`](docs/benchmark-smoke-v0.1.md) | Raw smoke-benchmark write-up |
| [`docs/golden-eval-v0.1-dev.md`](docs/golden-eval-v0.1-dev.md) | Raw labeled Golden Eval write-up |
| [`docs/roadmap.md`](docs/roadmap.md) | Prioritized future directions and explicit non-goals |
| [`docs/scenarios.md`](docs/scenarios.md) | Scenario Gallery presets |
| [`docs/migration-from-opensupport.md`](docs/migration-from-opensupport.md) | How the CFPB case study maps onto framework contracts |
| [`docs/resume-and-interview.md`](docs/resume-and-interview.md) | 中文：简历项目描述与 AI 产品经理面试准备材料 |
