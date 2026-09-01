# 生产就绪清单（Production Readiness Checklist）

上线前逐项打勾。每一项都标注了在本仓库的落地位置——既是清单，也是"生产级到底改了什么"的导览图。

## 自动检查（先跑这个）

```bash
# environment=production 时启动日志与 /api/v1/health 会列出未达标项
curl -s localhost:18000/api/v1/health | jq .production_readiness
# 上线验收标准：warnings 数组为空
```

实现：`config.py :: Settings.production_warnings()`。

## 可观测性

- [ ] JSON 结构化日志已开启（`OPENRAG_LOG_FORMAT=json`），日志进平台（Loki/ELK）——`observability/logging.py`
- [ ] OTel 追踪已开启且能在 Jaeger/Tempo 查到 span（`OPENRAG_OTEL_ENABLED=true`）——`observability/telemetry.py`
- [ ] 采样率按流量设定（`OPENRAG_OTEL_SAMPLE_RATIO`），成本可控
- [ ] Prometheus 正在抓 `/metrics`，Grafana 有 RED 仪表盘——`deploy/prometheus.yml`
- [ ] 告警规则已配置并接通知渠道：5xx 率、P95 延迟、**降级次数**——模板在 `deploy/prometheus.yml` 底部
- [ ] 用户报障流程要求附带 `X-Request-ID` / `X-Trace-Id`

## 安全

- [ ] `OPENRAG_API_KEY` 已设置且经 Secret 注入（非明文提交）——`security.py::ApiKeyMiddleware`
- [ ] CORS 只允许确切域名（`OPENRAG_CORS_ALLOW_ORIGINS`）
- [ ] TLS 在代理/Ingress 层终结，HSTS 已配置——`docs/deployment.md`
- [ ] 限流已启用：网关层为主（`deploy/k8s/service.yaml` Ingress 注解）+ 应用层兜底（`OPENRAG_RATE_LIMIT_PER_MINUTE`）
- [ ] `/metrics` 与 Qdrant、模型端点仅内网可达（安全组/NetworkPolicy）
- [ ] 容器非 root 运行（UID 10001）、drop ALL capabilities——`Dockerfile` + `deploy/k8s/deployment.yaml`
- [ ] 镜像用不可变 tag/digest，CI 含漏洞扫描
- [ ] 依赖锁定并定期更新（Dependabot/Renovate）

## 可靠性

- [ ] 三类健康端点接入编排器探针——`app.py` `/livez` `/readyz` + `deploy/k8s/deployment.yaml`
- [ ] 优雅关机链路完整：preStop → SIGTERM → 排空 → 超时兜底，各层时间窗协调——`docs/deployment.md`
- [ ] 所有出站调用有显式超时——`config.py` 超时段 + `net.py`
- [ ] 出站连接池有并发上限（保护下游）——`net.py`
- [ ] 降级路径已验证：关掉 Qdrant/LLM 后上传、检索仍降级可用，且 `openrag_degraded_fallbacks_total` 有计数
- [ ] SQLite 已启用 WAL（`store.py`）；写并发增长时切 production profile（PostgreSQL）

## 数据

- [ ] `OPENRAG_DATA_DIR` 挂持久卷（compose volume / K8s PVC）
- [ ] 备份已配置且**演练过恢复**：SQLite 文件 + uploads + artifacts 快照即完整真相源；Qdrant 无需备份（`POST /api/v1/knowledge-bases/{id}/index/rebuild` 可重建）
- [ ] 上传大小限制在应用与代理两层对齐

## 容量与发布

- [ ] 资源 requests/limits 来自压测数据——`deploy/k8s/deployment.yaml`
- [ ] Lite 档位保持单副本（SQLite+RWO 卷）；需要横向扩容先迁 production profile
- [ ] 发布流程含 staging 冒烟（golden eval）与金丝雀观察指标回滚

## 流程

- [ ] 值班与升级路径明确；`/api/v1/health` 纳入巡检
- [ ] 事故后把新的检查项补回本清单（清单是活文档）
