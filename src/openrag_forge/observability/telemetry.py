"""OpenTelemetry 分布式追踪（生产追踪实践教学）。

这里回答三个问题：

**为什么要 OTel？** 业务层的 TraceRecorder（pipeline/trace.py）记录"Recipe 的
哪个节点做了什么决策"，是给领域专家看的审计证据；OTel 记录"每次调用花了多少
毫秒、卡在哪个下游"，是给 SRE 看的性能剖面。两者通过 trace_id 关联：
TraceEvent.otel_trace_id 字段 + 响应头 X-Trace-Id，可直接拿去 Jaeger 搜索。

**怎么部署接收端？** ``docker compose --profile observability up -d`` 会启动
OTel Collector（4318 收 OTLP）→ Jaeger（16686 查看 UI）。应用只需要配两个
环境变量：``OPENRAG_OTEL_ENABLED=true`` 与
``OPENRAG_OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318``。
生产环境把 Collector 换成集中部署，导出到 Tempo/Jaeger/厂商 APM 均可——
这正是引入 Collector 这一中间层的意义：应用侧协议永远是 OTLP，后端随便换。

**怎么控制成本？** ``OPENRAG_OTEL_SAMPLE_RATIO`` 控制头部采样率；
ParentBased 采样器保证一条链路上的所有 span 采样决策一致（不会出现断链）。

依赖是可选的（``pip install ".[observability]"``）；未安装或未开启时，
本模块所有函数退化为 no-op，不影响 Lite 档位运行。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)

_provider: Any = None
_enabled = False


def setup_tracing(settings: Settings) -> bool:
    """初始化 TracerProvider 与 OTLP 导出。在 lifespan 启动阶段调用一次。"""
    global _provider, _enabled
    if not settings.otel_enabled and not settings.langfuse_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    except ImportError:
        logger.warning("OPENRAG_OTEL_ENABLED=true 但缺少依赖；请执行 pip install \".[observability]\"")
        return False

    # Resource 属性会附着在每个 span 上，是在 Jaeger/Tempo 里区分服务与环境的关键。
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": "0.2.0",
            "deployment.environment": settings.environment,
        }
    )
    sample_ratio = min(
        settings.otel_sample_ratio if settings.otel_enabled else 1.0,
        settings.langfuse_sample_ratio if settings.langfuse_enabled else 1.0,
    )
    provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_ratio)))
    if settings.otel_enabled:
        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/")
        # BatchSpanProcessor 在后台批量异步导出，不阻塞请求路径；
        # 与之相对的 SimpleSpanProcessor 每个 span 同步导出，只适合调试。
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
    if settings.langfuse_enabled:
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            auth = base64.b64encode(f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()).decode("ascii")
            endpoint = f"{settings.langfuse_base_url.rstrip('/')}/api/public/otel/v1/traces"
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"})))
            logger.info("Langfuse OTLP tracing 已启用", extra={"langfuse_endpoint": endpoint})
        else:
            logger.warning("OPENRAG_LANGFUSE_ENABLED=true 但缺少 Langfuse public/secret key；exporter 未启用")
    trace.set_tracer_provider(provider)
    _provider = provider
    _enabled = True
    logger.info("OpenTelemetry tracing 已启用", extra={"otel_enabled": settings.otel_enabled, "langfuse_enabled": settings.langfuse_enabled, "sample_ratio": sample_ratio})
    return True


def shutdown_tracing() -> None:
    """优雅关机的一部分：flush 缓冲区里尚未导出的 span，避免丢失最后一批数据。"""
    global _provider, _enabled
    if _provider is not None:
        _provider.shutdown()
        _provider = None
    _enabled = False


def get_tracer() -> Any:
    """返回 tracer；tracing 未启用时返回 None，调用方据此走 no-op 分支。"""
    if not _enabled:
        return None
    from opentelemetry import trace

    return trace.get_tracer("openrag_forge")


def current_span_context() -> Any:
    """当前活跃 span 的 SpanContext；无有效 span 时返回 None。"""
    if not _enabled:
        return None
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    return context if context.is_valid else None


def current_trace_id() -> str | None:
    """当前请求的 trace_id（32 位十六进制），可直接在 Jaeger 搜索框粘贴。"""
    context = current_span_context()
    return format(context.trace_id, "032x") if context is not None else None


@contextmanager
def start_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """给任意组件包一层 span 的统一入口。

    用法（各适配器均已示范）::

        with start_span("rag.embed", {"chunk_count": len(chunks)}):
            vectors = self.embed(...)

    tracing 关闭时零开销直通，因此业务代码可以放心地在所有关键路径埋点。
    """
    tracer = get_tracer()
    if tracer is None:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        yield span


class TraceContextMiddleware:
    """为每个 HTTP 请求创建 SERVER 级 span 的 ASGI 中间件。

    职责：
    1. 从请求头提取 W3C traceparent（上游网关/前端注入的链路上下文），
       使跨服务调用串成同一条 trace；
    2. 创建 server span 并记录 HTTP 语义属性（method / route / status）；
    3. 把 trace_id 写回响应头 ``X-Trace-Id``——排障时用户报障附带这个值，
       就能直接在 Jaeger 定位到那一次请求。

    这里用纯 ASGI 协议实现而非 BaseHTTPMiddleware，避免额外的任务调度开销，
    也是编写高性能中间件的推荐方式。
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        tracer = get_tracer()
        if tracer is None or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from opentelemetry.propagate import extract
        from opentelemetry.trace import SpanKind, Status, StatusCode

        headers = {key.decode("latin-1"): value.decode("latin-1") for key, value in scope.get("headers", [])}
        parent_context = extract(headers)
        method = scope.get("method", "GET")
        path = scope.get("path", "/")

        with tracer.start_as_current_span(f"{method} {path}", context=parent_context, kind=SpanKind.SERVER) as span:
            span.set_attribute("http.request.method", method)
            span.set_attribute("url.path", path)
            trace_id = format(span.get_span_context().trace_id, "032x")
            status_holder = {"status": 500}

            async def send_with_trace_header(message: dict[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    status_holder["status"] = message["status"]
                    message.setdefault("headers", []).append((b"x-trace-id", trace_id.encode("latin-1")))
                await send(message)

            try:
                await self.app(scope, receive, send_with_trace_header)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                # 路由匹配完成后 scope 里才有 route 模板，用它替换原始路径重命名
                # span（"GET /api/v1/runs/{run_id}" 而不是携带具体 ID 的高基数名字）。
                route = scope.get("route")
                if route is not None and getattr(route, "path", None):
                    span.update_name(f"{method} {route.path}")
                    span.set_attribute("http.route", route.path)
                span.set_attribute("http.response.status_code", status_holder["status"])
                if status_holder["status"] >= 500:
                    span.set_status(Status(StatusCode.ERROR))
