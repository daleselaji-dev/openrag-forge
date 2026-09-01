# 配置指南（Configuration）

本框架遵循 [12-Factor](https://12factor.net/zh_cn/config) 配置原则：**同一个镜像，靠环境变量适配所有环境**。所有配置项定义在 `src/openrag_forge/config.py`（每项都有注释），前缀统一为 `OPENRAG_`。

## 配置在哪里改（按部署方式）

| 部署方式 | 非敏感配置 | 敏感配置（key/口令） |
|---|---|---|
| 本地开发 | `.env`（从 `.env.example` 复制） | `.env`（已被 .gitignore 排除） |
| Docker Compose | `docker-compose.yml` 的 `environment:` | 宿主环境变量注入 `${OPENRAG_API_KEY:-}` |
| Kubernetes | `deploy/k8s/configmap.yaml` | `deploy/k8s/secret.example.yaml`（模板；真实值经 CI/CD 或 Vault 注入） |

改完配置如何生效：本地重启进程；Compose `docker compose up -d api`；K8s `kubectl rollout restart deploy/openrag-forge`。

## 全部配置项

### 运行档位

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENRAG_PROFILE` | `lite` | 能力档位：`lite`（SQLite+本地文件）/ `production` / `graph` 等 |
| `OPENRAG_ENVIRONMENT` | `dev` | 部署环境：`dev` / `staging` / `production`。设为 production 时启用生产就绪检查 |
| `OPENRAG_HOST` / `OPENRAG_PORT` | `127.0.0.1` / `18000` | 监听地址与端口（容器内用 `0.0.0.0`） |
| `OPENRAG_GRACEFUL_SHUTDOWN_SECONDS` | `30` | SIGTERM 后等待在途请求的窗口，需小于编排层强杀超时 |

### 存储与依赖

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENRAG_DATA_DIR` | `./data` | SQLite / 上传原件 / Evidence Capsule 根目录，**生产必须挂持久卷** |
| `OPENRAG_QDRANT_URL` | `http://localhost:6333` | 派生向量索引（可随时重建，不是真相源） |
| `OPENRAG_QDRANT_COLLECTION` | `openrag_forge` | 集合名 |
| `OPENRAG_QDRANT_API_KEY` | 空 | Qdrant Cloud/鉴权集群的 key，经 Secret 注入 |
| `OPENRAG_CHAT_BASE_URL` 等 | `http://localhost:1234/v1` | OpenAI 兼容模型端点（LM Studio/vLLM/云端） |
| `OPENRAG_MODEL_API_KEY` | 空 | 模型端点 Bearer token |

### 出站超时

任何出站调用都必须有显式超时——一个卡死的下游不该拖垮本服务。数值按“下游 P99 延迟 + 余量”设定：

| 变量 | 默认 | 适用 |
|---|---|---|
| `OPENRAG_HTTP_TIMEOUT_SECONDS` | `10` | 默认出站（Qdrant 管理操作等） |
| `OPENRAG_PROBE_TIMEOUT_SECONDS` | `2` | 健康探测（必须短） |
| `OPENRAG_EMBEDDING_TIMEOUT_SECONDS` | `60` | 批量 Embedding |
| `OPENRAG_CHAT_TIMEOUT_SECONDS` | `90` | LLM 生成 |
| `OPENRAG_INDEX_TIMEOUT_SECONDS` | `120` | 批量写 Qdrant |

### 安全

| 变量 | 默认 | 生产建议 |
|---|---|---|
| `OPENRAG_CORS_ALLOW_ORIGINS` | `*` | 列出确切前端域名，逗号分隔 |
| `OPENRAG_API_KEY` | 空 | 必须设置（`python -c "import secrets; print(secrets.token_urlsafe(32))"` 生成）；客户端用 `X-API-Key` 或 `Authorization: Bearer` 携带 |
| `OPENRAG_RATE_LIMIT_PER_MINUTE` | `0`（关） | 单副本兜底限流；多副本在网关限流（见 `deploy/k8s/service.yaml` 注解） |
| `OPENRAG_MAX_UPLOAD_MB` | `64` | 与反向代理的 body 大小限制对齐 |

### 可观测性

| 变量 | 默认 | 生产建议 |
|---|---|---|
| `OPENRAG_LOG_LEVEL` | `info` | `info`；排障临时调 `debug` |
| `OPENRAG_LOG_FORMAT` | `text` | **`json`**——可被日志平台解析，且自动携带 request_id / trace_id |
| `OPENRAG_OTEL_ENABLED` | `false` | **`true`**，配合 OTLP 接收端（见 docs/observability.md） |
| `OPENRAG_OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4318` | Compose 内为 `http://otel-collector:4318` |
| `OPENRAG_OTEL_SERVICE_NAME` | `openrag-forge` | 多服务共用追踪后端时区分来源 |
| `OPENRAG_OTEL_SAMPLE_RATIO` | `1.0` | 高流量降到 0.05~0.2 控制成本 |
| `OPENRAG_METRICS_ENABLED` | `true` | 保持开启；网络层限制 /metrics 只对 Prometheus 可达 |

### Langfuse（可选）

Langfuse 是独立的 LLM 观测与评测后端，不是会改变 RAG 结果的画布节点。开启后，
OpenTelemetry span 会通过 OTLP `/api/public/otel/v1/traces` 发往自托管 Langfuse，
同时可以继续发往 Jaeger/Tempo。public/secret key 只从环境变量读取，不会写入业务 Trace、
Evidence Capsule 或日志。

| 变量 | 默认 | 说明 |
|---|---|---|
| `OPENRAG_LANGFUSE_ENABLED` | `false` | 是否启用 Langfuse OTLP exporter |
| `OPENRAG_LANGFUSE_BASE_URL` | `http://localhost:3000` | 自托管 Langfuse 地址 |
| `OPENRAG_LANGFUSE_PUBLIC_KEY` | 空 | Langfuse project public key，经 Secret 注入 |
| `OPENRAG_LANGFUSE_SECRET_KEY` | 空 | Langfuse project secret key，经 Secret 注入 |
| `OPENRAG_LANGFUSE_SAMPLE_RATIO` | `1.0` | Langfuse trace 采样率；高流量可降到 0.05~0.2 |
| `OPENRAG_LANGFUSE_CAPTURE_CONTENT` | `false` | 是否把 prompt/completion 写入 OTel span；本地调试可开，生产默认关闭并配合 PII 策略 |

## 启动时的配置校验

`OPENRAG_ENVIRONMENT=production` 时，应用启动会执行生产就绪检查（`Settings.production_warnings()`），结果同时输出到：

1. 启动日志（每条 warning 一行结构化日志）；
2. `GET /api/v1/health` 的 `production_readiness.warnings` 字段。

**上线前验收标准：该数组必须为空。** 检查项包括 CORS 全开、未设 API key、非 JSON 日志、未开追踪、SQLite 多副本风险等，完整逻辑见 `config.py` 末尾。
