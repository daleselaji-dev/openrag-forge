"""请求上下文中间件：Request ID + 结构化访问日志 + HTTP 指标。

三件事为什么放在一个中间件里：它们共享同一次计时与同一个请求上下文，
拆开反而要重复测量。与 telemetry.TraceContextMiddleware 的分工是——
那边负责分布式追踪（跨服务），这边负责单服务的日志与指标。

Request ID 约定（行业通用做法）：
- 客户端/网关可以通过 ``X-Request-ID`` 头传入自己的关联 ID（例如 Nginx 的
  ``$request_id``），实现网关日志与应用日志的串联；
- 未传入时由本中间件生成；
- 无论哪种来源，都会回写到响应头，并注入当前上下文使所有日志自动携带。
"""

from __future__ import annotations

import time
from typing import Any
from uuid import uuid4

from .logging import get_logger, request_id_var
from .metrics import observe_http_request

logger = get_logger("openrag_forge.access")


class RequestContextMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        request_id = headers.get("x-request-id") or f"req_{uuid4().hex[:16]}"
        # 不做 reset：uvicorn 为每个请求创建独立的任务上下文，变量不会跨请求泄漏；
        # 保留值可以让最外层的 500 兜底处理器（app.unhandled_exception_handler）
        # 在异常穿过本中间件之后仍能把 request_id 返回给客户端。
        request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_with_request_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            duration = time.perf_counter() - started
            # 指标的 path label 必须用路由模板（低基数），路由未命中统一记 unmatched，
            # 防止扫描器乱打路径撑爆时间序列。原始路径只进日志（日志天然支持高基数）。
            route = scope.get("route")
            path_label = getattr(route, "path", "unmatched")
            method = scope.get("method", "GET")
            observe_http_request(method, path_label, status_holder["status"], duration)
            # 健康探测每几秒打一次，全量记录只会淹没有效日志——降为 debug 级。
            probe = scope.get("path", "") in {"/livez", "/readyz", "/metrics"}
            logger.log(
                10 if probe else 20,  # DEBUG / INFO
                "http_request",
                extra={
                    "method": method,
                    "path": scope.get("path", ""),
                    "route": path_label,
                    "status": status_holder["status"],
                    "duration_ms": round(duration * 1000, 2),
                    "client": (scope.get("client") or ("", 0))[0],
                },
            )
