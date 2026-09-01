"""可观测性三大支柱的落地实现（教学模块）。

- ``logging.py``    结构化日志：JSON 输出 + request_id / trace_id 自动关联
- ``telemetry.py``  分布式追踪：OpenTelemetry span、OTLP 导出、请求级 Server Span
- ``metrics.py``    指标：Prometheus 计数器 / 直方图与 /metrics 端点
- ``middleware.py`` 请求上下文：X-Request-ID、访问日志、HTTP 指标采集

设计原则：全部**优雅降级**。未安装 ``.[observability]`` extra 或未开启开关时，
所有函数变成 no-op，Lite 档位零依赖启动的能力不受影响。
如何本地跑起完整可观测性栈（Jaeger + Prometheus + Grafana）见 docs/observability.md。
"""

from .logging import get_logger, request_id_var, setup_logging
from .metrics import metrics_available, observe_fallback, observe_http_request, observe_run, render_metrics
from .telemetry import current_trace_id, get_tracer, setup_tracing, shutdown_tracing, start_span

__all__ = [
    "current_trace_id",
    "get_logger",
    "get_tracer",
    "metrics_available",
    "observe_fallback",
    "observe_http_request",
    "observe_run",
    "render_metrics",
    "request_id_var",
    "setup_logging",
    "setup_tracing",
    "shutdown_tracing",
    "start_span",
]
