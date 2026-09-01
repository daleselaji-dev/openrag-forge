# 可观测性指南（Observability）

生产系统必须能回答三个问题——每个问题对应一根支柱、一个模块、一个查看入口：

| 问题 | 支柱 | 实现模块 | 在哪里看 |
|---|---|---|---|
| 这一次请求发生了什么？ | 追踪 Traces | `observability/telemetry.py` + `pipeline/trace.py` | Jaeger `http://localhost:16686` |
| 系统整体处于什么状态？ | 指标 Metrics | `observability/metrics.py` | Prometheus `:19090` / Grafana `:13000` |
| 那个时刻具体报了什么错？ | 日志 Logs | `observability/logging.py` | `docker compose logs api` / 日志平台 |

## Langfuse 作为独立观测节点

除了 Jaeger/Tempo，OpenRAG Forge 可以把同一批 OpenTelemetry span 发送到自托管 Langfuse。
Langfuse v4 推荐使用 OTLP ingestion（`/api/public/otel/v1/traces`），而不是旧版 legacy ingestion。
因此 Langfuse 是独立的观测/评测服务：它不会改变 Recipe 的检索或生成结果，只负责保存 LLM 调用、
运行上下文、延迟、模型版本和后续 Score/Eval。配置 `OPENRAG_LANGFUSE_ENABLED=true`、
`OPENRAG_LANGFUSE_BASE_URL` 以及项目 public/secret key 后重启 API，顶栏会显示 Langfuse 健康状态。

Langfuse 的 Score 可以来自确定性代码、人工标注或 LLM-as-a-Judge；本项目的 Recall@k、MRR、nDCG、
Citation validity、拒答正确性和 p95 仍以本地 Golden Set 为主门禁，再把逐条结果和 Trace ID
同步到 Langfuse 做切片、回放和长期趋势分析。

将本地报告同步到 Langfuse：

```powershell
$env:OPENRAG_LANGFUSE_BASE_URL = "http://localhost:3000"
$env:OPENRAG_LANGFUSE_PUBLIC_KEY = "lf_pk_..."
$env:OPENRAG_LANGFUSE_SECRET_KEY = "lf_sk_..."
python scripts/push_langfuse_scores.py --report reports/framework_smoke_latest.json
```

适配器只发送可复现的确定性分数（`citation_presence`、`evidence_presence`、
`refusal_correctness`、`latency_ms`），不会把“模型觉得正确”冒充成事实。发布门禁仍读取
本地报告；Langfuse 负责长期趋势和逐条 Trace 复盘。

## 一键启动本地可观测性栈

```bash
OPENRAG_OTEL_ENABLED=true docker compose --profile observability up -d
```

启动了什么：API → OTLP → **OTel Collector**（`deploy/otel-collector-config.yaml`）→ **Jaeger**（追踪 UI）；**Prometheus**（`deploy/prometheus.yml`）抓取 API 的 `/metrics`；**Grafana**（数据源已自动装配，`deploy/grafana/provisioning/`）。

## 双层 Trace 设计（本框架的核心特色）

同一次运行会产生两套互相关联的 Trace：

1. **业务审计 Trace**：`TraceRecorder` 记录每个 Recipe 节点的决策（召回几条、安全门为何拒绝），持久化在 SQLite 并随 Evidence Capsule 导出——给领域专家与审计看，永久保存；
2. **OTel 性能 Trace**：每个 HTTP 请求一个 Server Span，之下挂着 `recipe.node.*`（每个 Recipe 节点）、`rag.embed`、`rag.qdrant.search`、`rag.llm.generate` 等子 span——给 SRE 看，按采样率保留。

两者通过 **trace_id** 关联，三个获取入口：

- 任意 API 响应头 `X-Trace-Id`；
- `GET /api/v1/runs/{run_id}` 里每个 trace 事件的 `otel_trace_id` 字段；
- JSON 日志的 `trace_id` 字段。

把这个值粘贴到 Jaeger 搜索框，就能看到该请求的完整耗时瀑布图：**回答“这次提问慢在 embedding、向量检索还是 LLM 生成”只需要 10 秒**。

想给新组件加追踪？一行搞定（tracing 关闭时零开销）：

```python
from openrag_forge.observability import start_span

with start_span("my.component", {"batch_size": len(items)}):
    do_work()
```

## 指标（Metrics）

| 指标 | 类型 | 用途 |
|---|---|---|
| `openrag_http_requests_total{method,path,status}` | Counter | QPS 与错误率（RED 中的 R/E） |
| `openrag_http_request_duration_seconds` | Histogram | 延迟分布，`histogram_quantile(0.95, ...)` 算 P95 |
| `openrag_runs_total{recipe_id,status}` | Counter | Recipe 运行分布（含 preview 与安全门拒绝） |
| `openrag_run_duration_seconds{recipe_id}` | Histogram | 端到端 RAG 延迟，按 Recipe 对比 |
| `openrag_degraded_fallbacks_total{component}` | Counter | **降级可见性**：Qdrant 不可用退词法检索、LLM 不可用退摘要回答的次数 |

`openrag_degraded_fallbacks_total` 是本框架"降级可用"设计的配套监控：降级让服务不中断，但绝不能无声——否则你会用降级质量服务用户数周而无人察觉。建议告警：`rate(openrag_degraded_fallbacks_total[5m]) > 0`。更多告警规则模板见 `deploy/prometheus.yml` 底部注释。

基数纪律（新增指标时必读）：label 取值必须小而有限。路径用路由模板（`/api/v1/runs/{run_id}`），绝不放 user_id、问题文本等无界值——详见 `observability/metrics.py` 模块注释。

## 日志（Logs）

设 `OPENRAG_LOG_FORMAT=json` 后每行输出一个 JSON 对象：

```json
{"timestamp": "...", "level": "info", "logger": "openrag_forge.access",
 "message": "http_request", "request_id": "req_ab12...", "trace_id": "4bf9...",
 "method": "POST", "route": "/api/v1/runs", "status": 200, "duration_ms": 843.2}
```

关键约定：

- **Request ID**：客户端/网关可传 `X-Request-ID` 头（如 Nginx `$request_id`）串联网关与应用日志；未传则自动生成，并总是回写到响应头。用户报障时让其附带这个值；
- **日志↔链路互跳**：日志里的 `trace_id` 直接粘到 Jaeger；在 Grafana Explore 中选 Jaeger 数据源同样可查；
- 应用只写 stdout，收集/轮转交给平台（Docker logging driver、K8s + Fluent Bit → Loki/ELK）。

## 健康检查

| 端点 | 语义 | 消费者 |
|---|---|---|
| `/livez` | 进程存活（不查外部依赖，防雪崩） | K8s livenessProbe / Docker HEALTHCHECK |
| `/readyz` | 可接流量（只查真相源 SQLite；Qdrant/LLM 可降级故不拖垮就绪） | K8s readinessProbe / Compose healthcheck |
| `/api/v1/health` | 人类可读的详细诊断 + 生产就绪告警 | 运维人员 / 前端状态面板 |

探针参数配置见 `deploy/k8s/deployment.yaml`（含逐行注释）。
