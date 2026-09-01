# Architecture

```text
Upload → Route → Parse → Blocks → Chunk → Enrich → Embed/Index
                                             ↓
Question → Recipe Compiler → Retrieve → Context → Generate → Policy Gate
                                             ↓
                              Evidence Capsule + Trace + Eval
```

Lite uses SQLite and local artifacts. Production adapters implement the same repository ports using PostgreSQL and MinIO. Qdrant is always treated as a rebuildable derived index.

The web canvas is an authoring surface; the published Recipe JSON is the execution source of truth. Only registered nodes with typed ports may run.

## 横切层（生产级基础设施）

```text
请求 → CORS → 安全响应头 → OTel Server Span → 请求上下文(ID/日志/指标) → 限流 → API Key → 路由
```

- `observability/`：结构化日志、OTel 追踪、Prometheus 指标——三者通过 request_id 与 trace_id 关联；
- `security.py`：API key 认证、限流、安全响应头（TLS/WAF 属于代理层，见 docs/deployment.md）；
- `net.py`：共享出站连接池与显式超时；
- 双层 Trace：`pipeline/trace.py` 的业务审计事件同时镜像为 OTel span，业务视角与性能视角用同一个 trace_id 互查（详见 docs/observability.md）。

