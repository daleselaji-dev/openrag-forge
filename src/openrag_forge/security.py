"""API 安全加固（生产安全实践教学）。

分层防御的分工，先说清楚哪些不归应用层管：

- **TLS 终结**、HSTS、CSP、WAF、DDoS 防护 → 反向代理 / Ingress / CDN 层
  （见 docs/deployment.md 的 Nginx 与 K8s Ingress 示例）；
- **网络隔离** → Qdrant、模型服务、/metrics 只在内网可达，用安全组/NetworkPolicy 收口；
- 应用层（本文件）负责：认证、基础响应头、单实例限流兜底。

配置入口：``OPENRAG_API_KEY``（生产用 Secret 注入）、
``OPENRAG_RATE_LIMIT_PER_MINUTE``、``OPENRAG_CORS_ALLOW_ORIGINS``。
"""

from __future__ import annotations

import hmac
import time
from collections import deque
from typing import Any

from starlette.responses import JSONResponse

from .config import settings

# 编排器探活与 Prometheus 抓取不带业务凭证，必须豁免认证与限流；
# 作为交换，这三个端点绝不返回敏感信息，且生产环境应在网络层限制 /metrics 来源。
EXEMPT_PATHS = {"/livez", "/readyz", "/metrics"}


class SecurityHeadersMiddleware:
    """为所有响应追加基础安全响应头。

    这些头在应用层加是因为它们与响应内容强相关；HSTS/CSP 之类与部署拓扑
    相关的头则应在 TLS 终结处（Nginx/Ingress）配置，避免开发环境误伤。
    """

    HEADERS = [
        (b"x-content-type-options", b"nosniff"),   # 禁止 MIME 嗅探，防止上传内容被当脚本执行
        (b"x-frame-options", b"DENY"),             # 禁止被 iframe 嵌套，防点击劫持
        (b"referrer-policy", b"no-referrer"),      # 不向第三方泄露带 ID 的内部 URL
    ]

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend(self.HEADERS)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class ApiKeyMiddleware:
    """可选的 API Key 认证：设置 ``OPENRAG_API_KEY`` 后自动启用。

    要点：
    - 支持 ``X-API-Key: <key>`` 与 ``Authorization: Bearer <key>`` 两种写法；
    - 用 ``hmac.compare_digest`` 常数时间比较，防止时序侧信道逐字节猜 key；
    - 只保护 ``/api/`` 前缀；探活/指标端点豁免（见 EXEMPT_PATHS）。

    进阶路线：多租户或细粒度权限时，把这里替换为 OIDC/JWT 校验
    （如企业 IdP + oauth2-proxy），中间件结构不变。
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        # 每次请求读取 settings 而不是启动时快照，保证测试可注入、热更新可生效
        api_key = settings.api_key
        path = scope.get("path", "")
        if scope["type"] != "http" or not api_key or not path.startswith("/api/") or path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        provided = headers.get("x-api-key", "")
        if not provided:
            authorization = headers.get("authorization", "")
            if authorization.lower().startswith("bearer "):
                provided = authorization[7:]
        if not hmac.compare_digest(provided.encode(), api_key.encode()):
            response = JSONResponse(
                status_code=401,
                content={"detail": "缺少或无效的 API key；请携带 X-API-Key 或 Authorization: Bearer 头"},
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RateLimitMiddleware:
    """基于滑动窗口的单进程限流兜底（``OPENRAG_RATE_LIMIT_PER_MINUTE``，0=关闭）。

    诚实地说明边界（这比实现本身更重要）：
    - 状态在进程内存里，**多副本部署时每个副本独立计数**，总放行量 = 限额 × 副本数；
    - 生产的第一道限流应放在网关（Nginx ``limit_req`` / Envoy / 云 WAF），
      需要跨副本精确限流时用 Redis 计数器；
    - 这里按客户端 IP 分桶——经过反向代理时要保证代理正确传递并由 uvicorn
      ``--proxy-headers`` 解析 X-Forwarded-For，否则所有请求都算同一个 IP。
    """

    _windows: dict[str, deque[float]] = {}

    def __init__(self, app: Any):
        self.app = app

    @classmethod
    def reset(cls) -> None:
        """清空计数状态（供测试与运维演练使用）。"""
        cls._windows.clear()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        limit = settings.rate_limit_per_minute
        path = scope.get("path", "")
        if scope["type"] != "http" or limit <= 0 or not path.startswith("/api/") or path in EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return
        client = (scope.get("client") or ("unknown", 0))[0]
        now = time.monotonic()
        window = self._windows.setdefault(client, deque())
        while window and now - window[0] > 60.0:
            window.popleft()
        if len(window) >= limit:
            retry_after = max(1, int(60.0 - (now - window[0])) + 1)
            response = JSONResponse(
                status_code=429,
                content={"detail": f"请求超过每分钟 {limit} 次限制，请稍后重试"},
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        window.append(now)
        await self.app(scope, receive, send)
