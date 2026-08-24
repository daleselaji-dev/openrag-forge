"""命令行入口：``openrag``（安装后可直接运行）。

生产部署不建议用这个入口，而是显式运行 uvicorn 并交给进程管理器
（容器 CMD / systemd）托管，参数含义见 docs/deployment.md：

    uvicorn openrag_forge.app:app \
        --host 0.0.0.0 --port 8000 \
        --timeout-graceful-shutdown 30 \
        --proxy-headers   # 部署在反向代理之后时，正确解析 X-Forwarded-For

本入口用于本地开发，主机/端口/优雅关机窗口均读取 OPENRAG_* 配置。
"""

import uvicorn

from .config import settings


def main() -> None:
    uvicorn.run(
        "openrag_forge.app:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        # 优雅关机：收到 SIGTERM/SIGINT 后等待在途请求完成的时间上限，
        # 应与 K8s terminationGracePeriodSeconds 协同（探针细节见 deploy/k8s/）。
        timeout_graceful_shutdown=settings.graceful_shutdown_seconds,
    )
