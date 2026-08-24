"""共享出站 HTTP 客户端（连接池实践教学）。

修复的反模式：原实现每次调用 ``httpx.get(...)`` 都新建连接——TCP 三次握手
（HTTPS 还有 TLS 握手）在每个请求上重复发生，高并发下还会耗尽本地端口。

生产做法：进程级复用一个 ``httpx.Client``，收益是：
1. Keep-Alive 连接复用，消除握手开销；
2. ``httpx.Limits`` 给出站并发设上限，保护下游（Qdrant/模型服务）不被
   本服务突发流量打垮——这是"好公民"式的背压；
3. 统一的默认超时兜底：即使某个调用点忘记传超时，也不会无限等待。

各调用点仍会显式传入按用途细分的超时（settings.*_timeout_seconds），
默认值只是最后防线。客户端在应用关机时由 lifespan 统一关闭（见 app.py）。
"""

from __future__ import annotations

import httpx

from .config import settings

_client: httpx.Client | None = None


def get_http_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=httpx.Timeout(settings.http_timeout_seconds, connect=5.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            # 明确不跟随重定向：出站目标都是我们自己配置的内部服务，
            # 意外的重定向更可能是配置错误或 SSRF 尝试，应当尽早失败。
            follow_redirects=False,
        )
    return _client


def close_http_client() -> None:
    """优雅关机时调用：干净地关闭连接池中的所有连接。"""
    global _client
    if _client is not None:
        _client.close()
        _client = None
