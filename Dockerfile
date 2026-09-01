# =============================================================================
# 生产级容器镜像（每条指令的"为什么"都在注释里）
#
# 构建:  docker build -t openrag-forge:0.2.0 .
# 运行:  docker run -p 18000:8000 --env-file .env openrag-forge:0.2.0
# 编排:  docker compose up -d qdrant api            （Lite 栈）
#        docker compose --profile observability up -d （+ Jaeger/Prometheus/Grafana）
# =============================================================================

# ---- 阶段 1：前端构建（多阶段构建让 Node 工具链不进入最终镜像）----
FROM node:24-slim AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
# npm ci 严格按 lockfile 安装，保证构建可复现；npm install 可能悄悄升级依赖
RUN npm ci
COPY web ./
RUN npm run build

# ---- 阶段 2：运行时镜像 ----
FROM python:3.12-slim
WORKDIR /app

# 安全加固：创建非 root 用户。容器以 root 运行时，一旦应用被攻破，
# 攻击者直接获得容器内 root，配合内核漏洞可能逃逸到宿主机。
# 固定 UID 便于在 K8s securityContext 与卷权限中引用（见 deploy/k8s/deployment.yaml）。
RUN groupadd --gid 10001 openrag && useradd --uid 10001 --gid 10001 --no-create-home openrag

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# 安装 observability extra：生产镜像默认具备导出 trace/metrics 的能力，
# 是否启用由环境变量决定（OPENRAG_OTEL_ENABLED），做到"一个镜像多处部署"。
RUN pip install --no-cache-dir ".[observability]"
COPY --from=web-build /web/dist ./web/dist
COPY recipes ./recipes
COPY packs ./packs

# 数据目录属主必须是运行用户，否则非 root 进程无法写 SQLite 与上传文件。
# 注意：bind mount（compose 的 ./data:/app/data）会沿用宿主目录的属主，
# 宿主上需要 chown -R 10001:10001 ./data，或改用命名卷（见 docker-compose.yml）。
RUN mkdir -p /app/data && chown -R openrag:openrag /app/data

USER openrag
EXPOSE 8000

# 容器自愈：Docker/编排器据此判断容器健康并自动重启。
# 探测 /livez（无外部依赖），用标准库发请求避免为 curl 增加镜像体积与攻击面。
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/livez', timeout=2)"

# 生产启动参数：
#   --timeout-graceful-shutdown 30  收到 SIGTERM 后最多等 30s 让在途请求完成
#   --proxy-headers                 部署在反向代理后时正确还原客户端 IP（限流依赖它）
# 水平扩容建议增加副本数而不是 --workers：每个副本独立的 /metrics 与生命周期更易观测。
CMD ["uvicorn", "openrag_forge.app:app", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "30", "--proxy-headers"]
