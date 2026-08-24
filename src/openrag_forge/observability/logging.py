"""结构化日志（生产日志实践教学）。

为什么生产环境必须用结构化（JSON）日志：

1. **机器可解析**：Loki / ELK / CloudWatch 直接按字段索引，不需要脆弱的正则；
2. **上下文关联**：每条日志自动携带 ``request_id`` 与 OpenTelemetry 的
   ``trace_id`` / ``span_id``——在 Grafana 里看到一条报错日志，复制 trace_id
   到 Jaeger 就能看到这次请求的完整调用链，这是排障效率的分水岭；
3. **单行输出到 stdout**：容器最佳实践——日志收集交给平台
   （Docker logging driver / K8s + Fluent Bit），应用自己绝不写日志文件、
   绝不负责轮转。

配置入口：``OPENRAG_LOG_FORMAT=json``（生产）/ ``text``（开发），
``OPENRAG_LOG_LEVEL=info``。见 .env.example 与 docs/configuration.md。
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# 请求作用域上下文：由 middleware.RequestContextMiddleware 写入。
# 使用 ContextVar 而不是全局变量，保证异步/多线程并发下每个请求互不串扰。
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# logging.LogRecord 的内置属性名，序列化时排除，其余键视为业务字段（extra=...）
_RESERVED_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "message", "asctime",
}


def _trace_context() -> dict[str, str]:
    """从当前 OTel span 提取 trace_id / span_id，用于日志↔链路互跳。"""
    from .telemetry import current_span_context

    context = current_span_context()
    if context is None:
        return {}
    return {"trace_id": format(context.trace_id, "032x"), "span_id": format(context.span_id, "016x")}


class JsonFormatter(logging.Formatter):
    """每行一个 JSON 对象，自动注入 request_id 与 trace 上下文。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        payload.update(_trace_context())
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """开发环境的人类可读格式，仍带 request_id 便于本地排障。"""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        request_id = request_id_var.get()
        extras = " ".join(
            f"{key}={value}"
            for key, value in record.__dict__.items()
            if key not in _RESERVED_ATTRS and not key.startswith("_")
        )
        parts = [base]
        if request_id:
            parts.append(f"request_id={request_id}")
        if extras:
            parts.append(extras)
        return " ".join(parts)


def setup_logging(log_level: str, log_format: str) -> None:
    """在应用启动（lifespan）时调用一次，接管根 logger。"""
    handler = logging.StreamHandler(sys.stdout)
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    # uvicorn 自带的 access log 不含 request_id/trace_id，与我们的访问日志重复；
    # 关闭它，统一使用 middleware.RequestContextMiddleware 输出的结构化访问日志。
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
