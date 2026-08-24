# Roadmap / 可以做的空间与未来扩展方向

本文列出 OpenRAG Forge 在当前 framework-extraction baseline 之上可以生长的方向。排序依据：**先补齐已声明契约的真实实现，再扩能力，最后扩生态**。每一项标注了它建立在哪个现有契约之上——这些方向不是空想清单，而是现有架构刻意预留的插槽（编译器节点目录、Store 端口、profile extras、Pack 隔离）。

## P0：把已注册的节点接到真实后端

这些节点类型已存在于编译器目录（`src/openrag_forge/pipeline/compiler.py` `NODE_CATALOG`）并有类型端口约束，但运行层目前是记录性实现（见 `docs/design.md` 第 3.3 节）。接通后即可解锁 V0.2–V0.8 Recipe 的真实横向对比：

| 节点 | 现有插槽 | 落地做法 |
|---|---|---|
| `sparse_retrieve` | 配置提示已写 `backend: qdrant_named_sparse` | Qdrant named sparse vector（BM25/SPLADE 权重），与 dense 并行召回 |
| `rrf_fusion` | 配置提示 `k=60, weights` | 对双路候选做真实 Reciprocal Rank Fusion 排名合并 |
| `reranker` | Model Registry 已有 `reranker` kind 与 `configured-reranker` 绑定 | 接 OpenAI 兼容 rerank 端点或本地 cross-encoder，candidate_k=50 → final_k=6 |
| `bounded_corrective` | `max_retries=1` 配置已声明 | 实现 evidence_grade 低分触发的一次查询改写重试，重试事件全程 Trace |
| `metadata_filter` / `intent_router` | 配置提示 `on_empty: fallback_once` | 按 Chunk metadata（生效日期、来源权威度）做检索前过滤 |
| `parent_expansion` | Block→Chunk 关系已持久化（`block_ids`） | 命中子块后按 `block_ids`/heading_path 扩展父级上下文 |

接通后立即执行：**同一冻结语料 + 同一 Golden Set 上跑 V0.1 vs V0.2 vs V0.4 对比**（`docs/evaluation.md` 第 6 节的第 5 项）。

## P0：发布级 Golden Set 与证据归档

- 冻结 50+ case 的 CFPB Golden Set，双人独立标注 `expected_chunk_ids`，记录标注一致率；
- 增加难度/语言/意图/来源权威度切片；人工 Citation Support 与 Answer Completeness 标注；
- 把冻结的评测报告快照与语料清单提交进仓库（`docs/reports/`）或 Release 附件，替代目前被 gitignore 的 `reports/`；
- 在 CI 中加一条"评测冒烟"任务：用 stub 模型端点跑 L1 基准的管线完整性（不做质量断言，只保证脚本与契约不回归）。

## P1：生产 profile 的真实适配器

`Store` 被刻意写成可替换端口（`store.py` docstring："production adapters can implement this port"），`pyproject.toml` 的 `production` extras 与 `docker-compose.yml` 的 production profile 已就位：

- PostgreSQL 实现 Store 端口（文档/块/Chunk/Recipe/Run/Trace 同一契约）；
- MinIO 存原始文件与 Evidence Capsule；Redis + Celery 把 ingest 与 index/rebuild 变成异步任务（上传接口已有 `job_id` 语义预留）；
- `observability` extras（OpenTelemetry + Prometheus 依赖已声明）：把 TraceEvent 同步导出为 OTel span，`duration_ms` 已在数据模型中预留。

## P1：评测驱动的 Recipe 迭代闭环

现有 L3 Eval API（`POST /api/v1/evals`）+ Recipe hash 可组合出产品化闭环：

- Recipe A/B 页面：同一 Golden Set 对比两个 recipe hash 的逐 case diff；
- 回归门禁：发布 Recipe（`/publish`）前强制跑指定 Golden Set，指标低于上一发布版本则阻断；
- 失败样本工作台：从评测行的 `run_id` 一键打开 Trace 与 Capsule，把 bad case 沉淀回 Golden Set。

## P2：能力扩展（已有 profile/节点占位）

- **Graph profile**：`graph_query` 节点 + `neo4j` extras 已声明；落地实体/关系抽取入图，与向量召回做证据融合；
- **多模态**：`pdf_layout` 路由与 `pdf_page_retrieve` 节点已占位；接入版面解析（如 docling，`office` extras 已声明）与页级图文检索；
- **Agent profile**：在 `build_ticket_draft`/`approval` 契约上扩展多步受控 Agent（追问缺失字段 → 检索 → 草稿 → 审批），保持 `forbidden_actions` 与 `side_effects=false` 不变量；
- **审批工作流落地**：approval 节点目前是"停靠点"，可扩展为带审批人、审批记录、驳回原因的真实工作流，审批决定写入 Capsule。

## P2：更多领域 Pack

CFPB 包（`packs/customer-support-cfpb/`）验证了"内核与领域隔离"的可行性。同一契约可复制：

- 内部政策包（版本化 SOP + 生效日期 metadata + 过期政策拦截）；
- 法务/合规包（来源权威度分级已有先例：`sources.json` 的 `authority_policy`）；
- 每个 Pack 自带：sources 清单、authority 策略、默认 Recipe、Golden Set、安全正则扩展。

## P3：生态与开发者体验

- Recipe/Pack 的导入导出与分享格式（Recipe JSON 已是可移植单文件）；
- 插件 SDK：第三方 parser/retriever/policy 以稳定描述符注册进 `NODE_CATALOG`（`CONTRIBUTING.md` 已约定描述符 + 确定性测试 + 失败模式三要素）；
- Capsule 查看器：独立静态页面渲染任意 Evidence Capsule JSON，供审计人员离线查看；
- 安全门从正则升级为可配置策略引擎（规则 + 小模型分类器双通道，正则保底），并对策略本身做评测切片。

## 刻意不做的事（non-goals）

- 不做无界 Agent 循环——纠错必须有显式 `max_retries`；
- 不在 Web 应用内执行用户上传的模型权重——模型永远通过 OpenAI 兼容端点接入；
- 不让向量库成为真相源——Qdrant 永远可重建；
- 不发布没有数据集快照 + recipe hash + 原始报告支撑的基准声明。
