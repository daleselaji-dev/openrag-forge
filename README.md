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
- 诚实标注：目录中尚未实现的节点（稀疏检索 / RRF / 重排等）在画布、检查器与 Trace 里都会标注「占位 / 退化」，Trace 的 `execution` 字段记录每一步真实发生了什么（live / fallback / stub_passthrough）。

### 面试讲解模式（RAG 设计课）

顶栏切到「面试讲解」后，左侧出现可收起的讲解面板（内容在 `web/src/interview/`），五章可导航：**设计历程**（V0 整文入上下文 → V0.1 Naive RAG → 可观察 → 可装配 → 生产横切 → 当前诚实态，每代含业务动机 / 取舍 / 面试追问）、**方案对比**（Naive/Advanced/Modular 范式 + 托管套件 / 开源编排 / 检索中台 / GraphRAG 逐维对比与 PM 结论）、**环节地图**（点击环节即选中画布对应节点，检查器同步显示该环节的规格级讲解 + 可改配置；不在当前 Recipe 的环节可一键加入画布试装）、**核心件深讲**（向量库专章含 8-12 分钟口述提纲，另有 Embedding / Chunk / Rerank / 生成 / Eval 五章）、**实验手册**（改一项配置 → 再跑 → 看 Trace 哪一行变了，含「占位证明」实验）。装配过程全程可定制，讲解跟着节点与 Trace 走；关闭讲解后界面回到干净控制室。

开发：`cd web && npm run dev`（Vite :5173，`/api` 代理到 :18000）；`npm run build` 后 FastAPI 直接伺服 `web/dist`。

## What makes it different

- **Recipe Compiler**: a drag-and-drop graph is compiled into a typed, immutable, hash-addressed Recipe before it can run.
- **Evidence Capsule**: every run exports configuration, model IDs, evidence, citations, safety decisions and Trace as one reproducible artifact.
- **Parser transparency**: upload routing, fallback decisions, blocks and chunks are visible and can be reprocessed without overwriting the source version.
- **Profiles**: `lite`, `production`, `observability`, `graph`, `multimodal` and `agent` add capability without making the default install heavy.

## Status

The current branch is the framework extraction baseline. The CFPB consumer-support implementation lives in `packs/customer-support-cfpb` and is intentionally separate from the generic core.

See `docs/architecture.md` and `docs/recipes.md` for the public contracts.
See `docs/benchmark-smoke-v0.1.md` for the reproducible local benchmark and its limitations.
See `docs/golden-eval-v0.1-dev.md` for labeled retrieval, citation and safety evaluation.
