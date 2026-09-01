"""Prometheus 指标（生产指标实践教学）。

指标回答的是"系统整体处于什么状态"：QPS、错误率、延迟分布（RED 方法），
以及本框架特有的业务指标——降级次数、Recipe 运行分布。告警规则应该建立在
指标上（如"5 分钟错误率 > 1%"），而不是靠人盯日志。

**怎么接入？** 应用暴露 ``GET /metrics``（Prometheus 文本格式），由 Prometheus
按 15s 间隔拉取（配置见 ``deploy/prometheus.yml``），Grafana 出图
（``docker compose --profile observability up -d`` 一键启动全套）。

**基数纪律（最容易翻车的地方）**：label 的取值集合必须是小而有限的。
路径 label 用路由模板（``/api/v1/runs/{run_id}``）而不是真实路径，否则每个
run_id 生成一条时间序列，Prometheus 内存会被打爆。同理绝不要把 user_id、
question 文本等无界值放进 label。

依赖 ``prometheus-client``（含在 observability extra 中）；未安装时所有
记录函数为 no-op，``/metrics`` 返回 501 并附带安装指引。
"""

from __future__ import annotations

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

    _AVAILABLE = True
except ImportError:  # 未安装 observability extra：全部退化为 no-op
    _AVAILABLE = False

if _AVAILABLE:
    # RED 方法核心三件套之二：Rate（请求量）与 Duration（延迟分布）。
    # Errors 通过 status label 上的 5xx 聚合得到，无需单独的 counter。
    HTTP_REQUESTS = Counter(
        "openrag_http_requests_total",
        "HTTP 请求总数（path 为路由模板以控制基数）",
        ["method", "path", "status"],
    )
    HTTP_DURATION = Histogram(
        "openrag_http_request_duration_seconds",
        "HTTP 请求耗时分布",
        ["method", "path"],
        # bucket 按本服务实际延迟形态设定：本地检索毫秒级，LLM 生成可到几十秒
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    )
    RUNS = Counter(
        "openrag_runs_total",
        "Recipe 运行总数（含 preview 与安全门拒绝）",
        ["recipe_id", "status"],
    )
    RUN_DURATION = Histogram(
        "openrag_run_duration_seconds",
        "Recipe 端到端运行耗时",
        ["recipe_id"],
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    )
    FALLBACKS = Counter(
        "openrag_degraded_fallbacks_total",
        "降级事件次数（component 标识哪个依赖不可用触发了降级）",
        ["component"],
    )


def metrics_available() -> bool:
    return _AVAILABLE


def observe_http_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    if not _AVAILABLE:
        return
    HTTP_REQUESTS.labels(method=method, path=path, status=str(status)).inc()
    HTTP_DURATION.labels(method=method, path=path).observe(duration_seconds)


def observe_run(recipe_id: str, status: str, duration_seconds: float) -> None:
    if not _AVAILABLE:
        return
    RUNS.labels(recipe_id=recipe_id, status=status).inc()
    RUN_DURATION.labels(recipe_id=recipe_id).observe(duration_seconds)


def observe_fallback(component: str) -> None:
    """记录一次降级：Qdrant 不可用退回词法检索、LLM 不可用退回摘要式回答等。

    这是"降级可用"设计的配套监控——降级让服务不中断，但**必须可见**，
    否则你会在无人察觉的情况下用降级质量服务用户数周。
    对应告警建议：rate(openrag_degraded_fallbacks_total[5m]) > 0 时通知值班。
    """
    if not _AVAILABLE:
        return
    FALLBACKS.labels(component=component).inc()


def render_metrics() -> tuple[bytes, str] | None:
    """渲染 /metrics 响应体；未安装 prometheus-client 时返回 None。"""
    if not _AVAILABLE:
        return None
    return generate_latest(), CONTENT_TYPE_LATEST
