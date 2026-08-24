# 部署指南（Deployment）

三档部署路径，从零到生产逐级演进。所有部署产物都在仓库内、带教学注释：

| 产物 | 用途 |
|---|---|
| `Dockerfile` | 多阶段构建 + 非 root + HEALTHCHECK + 优雅关机参数 |
| `docker-compose.yml` | Lite / observability / production 三个 profile |
| `deploy/otel-collector-config.yaml` | 追踪管道（OTLP → Jaeger） |
| `deploy/prometheus.yml` | 指标抓取与告警规则模板 |
| `deploy/grafana/provisioning/` | Grafana 数据源即代码 |
| `deploy/k8s/` | ConfigMap / Secret 模板 / Deployment（探针+资源+安全上下文）/ Service / Ingress |

## 第一档：本地开发（无容器）

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,observability]"
cp .env.example .env
uvicorn openrag_forge.app:app --reload --port 18000
```

## 第二档：Docker Compose

```bash
# Lite：API + Qdrant
docker compose up -d qdrant api

# 加可观测性栈（Jaeger/Prometheus/Grafana，入口地址见 docker-compose.yml 头部注释）
OPENRAG_OTEL_ENABLED=true docker compose --profile observability up -d

# 加生产存储（PostgreSQL/Redis/MinIO）
POSTGRES_PASSWORD=$(openssl rand -hex 16) docker compose --profile production up -d
```

要点（详见 compose 文件内注释）：

- `depends_on.condition: service_healthy` 让 API 等待 Qdrant 真正可用而非仅进程启动；
- `stop_grace_period: 35s` 必须大于 uvicorn 的 `--timeout-graceful-shutdown 30`；
- 敏感值用 `${VAR:-default}` 从宿主环境注入，不写死在 compose 文件里。

## 第三档：Kubernetes

```bash
# 1. 准备 Secret（真实值不进 git；也可用 Sealed Secrets / External Secrets）
kubectl create secret generic openrag-forge-secrets \
  --from-literal=OPENRAG_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --from-literal=OPENRAG_QDRANT_API_KEY="" \
  --from-literal=OPENRAG_MODEL_API_KEY=""

# 2. 按环境修改 configmap.yaml（域名、模型端点、采样率）后应用全部清单
kubectl apply -f deploy/k8s/

# 3. 验证
kubectl rollout status deploy/openrag-forge
kubectl logs deploy/openrag-forge | jq 'select(.readiness_warning)'   # 应无输出
```

`deploy/k8s/deployment.yaml` 内含全部生产要点的逐行注释，重点包括：

- **探针三分法**：livez（重启）/ readyz（摘流量）/ startupProbe（启动宽限）；
- **优雅下线时序**：preStop sleep 5s（等 LB 摘除）→ SIGTERM → uvicorn 排空 30s → 45s 总窗口兜底；
- **安全上下文**：runAsNonRoot + UID 10001（与 Dockerfile 一致）+ drop ALL capabilities；
- **资源声明**：requests 定调度、limits 防失控，数值来自压测而非拍脑袋；
- Lite 档位（SQLite + RWO 卷）必须 `replicas: 1` + `strategy: Recreate`，横向扩容前先切 production profile。

## 优雅关机原理（贯穿三档）

```text
编排器发 SIGTERM
  → uvicorn 停止接收新连接，等待在途请求完成（--timeout-graceful-shutdown 30）
  → lifespan 关机段执行（app.py）：flush OTel span 缓冲、关闭出站连接池
  → 进程退出；若超时未退，编排器 SIGKILL 强杀（所以窗口要留够）
```

没有这条链路的后果：每次发版/扩缩容都会掐断用户正在进行的 LLM 生成请求。

## 反向代理层职责（Nginx 示例）

TLS、HSTS、请求体限制属于代理层而不是应用层：

```nginx
server {
    listen 443 ssl;
    server_name kb.example.com;
    # TLS 证书由 certbot/ACME 管理
    client_max_body_size 64m;                 # 与 OPENRAG_MAX_UPLOAD_MB 对齐
    add_header Strict-Transport-Security "max-age=31536000" always;
    location / {
        proxy_pass http://127.0.0.1:18000;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;  # 应用限流按真实 IP
        proxy_set_header X-Request-ID $request_id;                    # 网关↔应用日志串联
        proxy_read_timeout 120s;              # 必须大于 OPENRAG_CHAT_TIMEOUT_SECONDS
    }
}
```

对应地，uvicorn 需带 `--proxy-headers` 启动（Dockerfile 已配置）。

## CI/CD 建议流水线

`.github/workflows/ci.yml` 已含 lint + 测试 + 前端构建。发布链路建议追加：

1. `docker build` 并推送到镜像仓库，tag 用 git SHA（不可变，禁止 latest）；
2. 镜像漏洞扫描（Trivy/Grype），高危阻断；
3. 部署到 staging，跑 `scripts/run_golden_eval.py` 冒烟（框架自带评测即质量门禁）；
4. 人工审批后金丝雀/滚动发布至生产，观察 `openrag_http_requests_total` 错误率决定是否回滚。
