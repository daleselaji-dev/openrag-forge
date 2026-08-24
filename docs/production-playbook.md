# OpenRAG Forge 生产级演进手册（v0.1）

本文档用于把当前仓库从“可运行基线”演进到“企业生产可控”。

## 1. 系统定位

- **真相源（source of truth）**：SQLite + 本地 artifacts（Lite）；生产可替换为 PostgreSQL + MinIO。
- **派生索引（derived state）**：Qdrant，任何时候都应可重建。
- **执行入口**：FastAPI（`src/openrag_forge/app.py`）。
- **可观测核心**：TraceEvent、Run Capsule、请求级 request_id。

## 2. 生产最小闭环

先确保这四件事同时成立：

1. **可追踪**：每次 API 请求有 `x-request-id`，每次 Run 有 trace/capsule。
2. **可调参**：所有关键组件参数由 `OPENRAG_*` 环境变量控制。
3. **可回放**：`/api/v1/runs/{run_id}` 与 `/api/v1/runs/{run_id}/capsule` 可复盘。
4. **可部署**：通过 `.env` + `docker-compose.yml` 配置运行时依赖。

## 3. 组件级 trace 与调配入口

### 3.1 API 网关层

- Trace 观察：
  - 响应头：`x-request-id`、`x-openrag-env`
  - 日志：`request_id=... method=... path=... status=... duration_ms=...`
- 调配环境变量：
  - `OPENRAG_SERVICE_NAME`
  - `OPENRAG_SERVICE_ENV`
  - `OPENRAG_LOG_LEVEL`
  - `OPENRAG_ENABLE_REQUEST_LOG`
  - `OPENRAG_CORS_ALLOW_ORIGINS`

### 3.2 Ingest（上传/解析/切块）

- Trace 观察：
  - 上传接口返回 `trace_id` 与节点事件（`route/chunk/meta/index`）
- 调配环境变量：
  - `OPENRAG_MAX_UPLOAD_MB`
  - `OPENRAG_CHUNK_MAX_CHARS`
  - `OPENRAG_CHUNK_OVERLAP`

### 3.3 Retrieval（检索）

- Trace 观察：
  - trace 节点中 `candidate_count`、`backend`（qdrant 或 lexical fallback）
- 调配环境变量：
  - `OPENRAG_DEFAULT_TOP_K`
  - `OPENRAG_RETRIEVAL_SCORE_THRESHOLD`
  - `OPENRAG_QDRANT_URL`
  - `OPENRAG_QDRANT_COLLECTION`

### 3.4 Generation（生成）

- Trace 观察：
  - `llm_generate` 节点 details 包含 provider、citation_count
- 调配环境变量：
  - `OPENRAG_CHAT_BASE_URL`
  - `OPENRAG_CHAT_MODEL`
  - `OPENRAG_EMBEDDING_BASE_URL`
  - `OPENRAG_EMBEDDING_MODEL`
  - `OPENRAG_RERANKER_BASE_URL`
  - `OPENRAG_RERANKER_MODEL`

### 3.5 Trace 与运行审计

- Trace 观察：
  - `/api/v1/runs/{run_id}/events`（SSE）
  - `/api/v1/ops/runs`（最近运行）
  - `/api/v1/ops/runtime`（当前生效参数与配置入口）
- 调配环境变量：
  - `OPENRAG_TRACE_PERSISTENCE`
  - `OPENRAG_TRACE_SAMPLE_RATE`

## 4. 从哪里配置部署

1. **环境变量模板**：`.env.example`
2. **本地/容器编排**：`docker-compose.yml`
3. **后端服务入口**：`src/openrag_forge/app.py`
4. **运行时可视检查**：
   - `GET /api/v1/health`
   - `GET /api/v1/ops/runtime`
   - `GET /api/v1/ops/runs`

## 5. 推荐的下一阶段（真正企业级）

1. **身份与权限**：在 API 前增加 OIDC/JWT 与租户隔离。
2. **可观测升级**：接入 OpenTelemetry Trace + Metrics + Log Correlation。
3. **数据分层**：SQLite 迁移 PostgreSQL，Artifacts 上 MinIO 版本策略。
4. **异步作业**：重建索引/Eval 改为后台任务队列（Celery + Redis）。
5. **变更安全**：引入迁移脚本、灰度策略、SLO 与告警阈值。
