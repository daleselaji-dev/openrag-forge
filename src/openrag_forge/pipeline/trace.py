from __future__ import annotations

import time
from typing import Any

from ..domain.models import TraceEvent
from ..observability import current_trace_id, get_tracer
from ..store import Store


class TraceRecorder:
    """业务 Trace 记录器：同一份事件同时写入两套观测体系。

    1. **业务审计 Trace**（SQLite / Evidence Capsule）：给领域专家看
       “哪个节点召回了几条证据、安全门为什么拒绝”，随 run 永久保存；
    2. **OpenTelemetry span**（Jaeger / Tempo）：给 SRE 看
       “每个节点耗时多少、挂在哪个下游”，按采样率保留。

    两者用同一个 otel_trace_id 关联：从 /api/v1/runs/{run_id} 或响应头
    X-Trace-Id 拿到 trace_id，粘贴到 Jaeger 搜索框即可看到该 run 的完整瀑布图。
    这就是"每个组件都可以查看 trace"的实现方式。
    """

    def __init__(self, run_id: str, recipe_hash: str, store: Store):
        self.run_id = run_id
        self.recipe_hash = recipe_hash
        self.store = store
        self.events: list[TraceEvent] = []

    def record(self, node_id: str, status: str, summary: str, details: dict[str, Any] | None = None, started: float | None = None) -> TraceEvent:
        # started 由调用方在节点开始时用 time.perf_counter() 采样；
        # 精度保留到微秒级（3 位小数），保证极快节点的耗时也不会四舍五入成 0。
        duration_ms = round((time.perf_counter() - started) * 1000, 3) if started is not None else 0.0
        event = TraceEvent(
            run_id=self.run_id,
            node_id=node_id,
            sequence=len(self.events) + 1,
            status=status,
            summary=summary,
            duration_ms=duration_ms,
            details=details or {},
            otel_trace_id=current_trace_id(),
        )
        self.events.append(event)
        self.store.save_trace(event)
        self._emit_otel_span(event)
        return event

    def _emit_otel_span(self, event: TraceEvent) -> None:
        """把业务节点事件镜像为一个 OTel span，使 Recipe 节点出现在 Jaeger 瀑布图中。

        span 会自动挂到当前请求的 Server Span 之下（上下文继承），因此在
        Jaeger 里能看到：HTTP 请求 → recipe.node.dense_retrieve →
        rag.embed / rag.qdrant.search 的完整层级。tracing 未启用时零开销跳过。
        """
        tracer = get_tracer()
        if tracer is None:
            return
        from opentelemetry.trace import Status, StatusCode

        end_ns = time.time_ns()
        start_ns = end_ns - int(event.duration_ms * 1_000_000)
        span = tracer.start_span(f"recipe.node.{event.node_id}", start_time=start_ns)
        span.set_attribute("recipe.run_id", event.run_id)
        span.set_attribute("recipe.node_id", event.node_id)
        span.set_attribute("recipe.node_status", event.status)
        span.set_attribute("recipe.summary", event.summary)
        if event.status == "failed":
            span.set_status(Status(StatusCode.ERROR, event.summary))
        span.end(end_time=end_ns)

    def run_node(self, node_id: str, func, **kwargs: Any):
        started = time.perf_counter()
        self.record(node_id, "running", "节点正在运行")
        try:
            value = func(**kwargs)
            self.record(node_id, "completed", "节点运行完成", started=started)
            return value
        except Exception as exc:
            self.record(node_id, "failed", str(exc), details={"error_type": type(exc).__name__}, started=started)
            raise
