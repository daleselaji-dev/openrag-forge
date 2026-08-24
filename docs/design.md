# Design rationale / 设计思路

本文解释 OpenRAG Forge 每个关键设计决策背后的动机、备选方案与取舍。所有描述均对应仓库内的真实代码，标注了源码路径；尚未实现的部分明确标记为「边界与已知限制」或指向 `docs/roadmap.md`。

## 0. 一句话定位

> 把 RAG 从"一段脚本里的黑盒"变成**可拆（组件化）、可跑（本地优先）、可证明（每次运行产出完整证据链）**的系统。

大多数 RAG demo 的问题不是"答不出来"，而是**答了却无法审计**：不知道用了哪个版本的文档、哪条 chunk、哪个模型、为什么这样路由、安全策略有没有真正生效。OpenRAG Forge 的所有设计都围绕这一个问题展开。

## 1. 核心决策一览

| # | 决策 | 备选方案 | 为什么这样选 | 源码 |
|---|---|---|---|---|
| 1 | SQLite + 本地文件作为唯一真相源，Qdrant 只是可重建的派生索引 | 直接把 Qdrant 当存储；或一开始就上 Postgres | 真相源必须能被完整备份、diff、离线检查；向量库丢了可以随时 `index/rebuild`，反过来不行 | `src/openrag_forge/store.py`、`src/openrag_forge/adapters/qdrant.py` |
| 2 | 拖拽图必须先经 Recipe Compiler 编译成带 SHA-256 哈希的不可变 Recipe 才能运行 | 画布即执行（所见即所得的低代码执行器） | 编译期就拒绝非法图（未知节点、端口类型不兼容、未声明的环），运行结果才可以被哈希唯一定位与复现 | `src/openrag_forge/pipeline/compiler.py` |
| 3 | 每次运行导出一个 Evidence Capsule（配置 + 模型 ID + 证据 + 引用 + 安全决策 + Trace 的单文件 JSON） | 只写日志 | 单文件可下载、可归档、可作为评测输入和争议仲裁依据 | `src/openrag_forge/app.py`（`_execute` 末尾）、`GET /api/v1/runs/{run_id}/capsule` |
| 4 | 解析路由是确定性的内容感知规则，不是 LLM 判断 | 用 LLM 给文件分类 | 路由必须可解释、可复测、零成本；每次决策带 `confidence` 与 `reason_codes` 落库 | `src/openrag_forge/parsers/router.py` |
| 5 | 请求级安全门在检索之前执行，高风险问题直接改写为安全回答并全程留痕 | 只在生成后过滤答案 | 「承诺退款/认定违法/账户处置」类请求根本不应该进入生成；被跳过的节点在 Trace 里标记为 `skipped` 而不是消失 | `src/openrag_forge/policies/basic.py`、`app.py` `_execute` |
| 6 | 生成端三级降级：OpenAI 兼容模型 → 抽取式回答 → 明确说无证据 | 模型不可用时抛异常 | 框架在模型服务离线时依然可用、可演示；降级路径写入 Trace（`provider` 字段） | `src/openrag_forge/generation/client.py` |
| 7 | 受控 Agent 只产出结构化草稿并强制停在人工审批门 | 让 Agent 直接执行动作 | 客服场景中错误动作的代价不对称；草稿显式列出 `missing_fields` 与 `forbidden_actions` | `app.py`（`build_ticket_draft` / `approval` 节点） |
| 8 | 能力用 profile 与 optional extras 增量开启（lite / production / observability / graph / office） | 单一重型安装 | 默认安装只依赖 FastAPI + SQLite + httpx 等少量库，30 秒可跑；生产件是选装适配器 | `pyproject.toml`、`docker-compose.yml` |
| 9 | 领域内容（CFPB 客服包）与通用内核物理隔离 | 领域逻辑混入核心 | 内核 provider-agnostic；换成任何公司自己的文档不需要改核心契约 | `packs/customer-support-cfpb/`、`docs/migration-from-opensupport.md` |

## 2. 数据面：从上传到索引

```text
Upload → Route → Parse → Blocks → Chunk → Enrich → Embed/Index (derived)
```

### 2.1 真相源与派生索引的严格分层

- 上传文件原样落盘（`data/uploads/`），记录 SHA-256、大小、MIME；`Document` 带 `version` 字段，重新解析（`POST /documents/{id}/reprocess`）只会 `version + 1`，永不覆盖源文件。
- `ParsedBlock` 和 `Chunk` 全部持久化在 SQLite（`store.py`），是检索证据的最终出处。
- Qdrant 中每个 point 的 payload 都带 `chunk_id`；查询返回后会先用 SQLite 里的 chunk 集合过滤（`app.py` 中 `truth_chunk_ids` 过滤），**索引里存在但真相源里已不存在的"幽灵点"不会成为证据**。这条防线在实测中真实拦截过一次索引血统问题（见 `docs/experiments.md` 实验 4）。

### 2.2 内容感知解析路由

`ParserRouter.decide()` 按内容签名（`%PDF`、`PK` zip 头）+ 扩展名 + MIME 决定 7 条路由之一：`native_text` / `html_structure` / `pdf_page_text` / `pdf_layout` / `office_structure` / `tabular` / `json_structure`。每个决策返回 `route + confidence + reason_codes + fallback_route`，随文档持久化，并在 Web 端逐文档展示。用户可显式指定路由覆盖自动决策（记录为 `user_selected_route`）。

PDF 内部还有一个启发式：正文前 200KB 出现 table/column/figure 等布局线索时走 `pdf_layout`（置信度 0.90），否则走 `pdf_page_text`（0.96）——置信度差异本身就是给运营者的信号。

### 2.3 上传即产出 Ingest Trace

上传不是静默批处理：`custom_ingest` Recipe（route → chunk → meta → index）每一步都写 `TraceEvent`。索引失败（Embedding 服务或 Qdrant 未启动）时状态是 `deferred` 并附带 `next_action`，而不是抛 500——**API 在模型服务离线时依然可用**是刻意保证的第一体验。

## 3. 控制面：Recipe 编译与执行

```text
Question → Recipe Compiler → Retrieve → Context → Generate → Policy Gate
                                   ↓
                    Evidence Capsule + Trace + Eval
```

### 3.1 为什么要"编译"

画布（React Flow）只是创作面；执行的唯一事实是发布后的 Recipe JSON。编译器（`compiler.py`）做四类静态检查：

1. **节点合法性**：只有 27 种注册节点类型可用（`NODE_CATALOG`），未知类型直接 `CompileError`；
2. **端口类型检查**：每条边的 `source_port → target_port` 必须匹配目录声明的输出/输入类型（如 `candidates` 不能直接接到 `context` 上）；
3. **环检测**：Kahn 拓扑排序发现环即拒绝；纠错检索必须使用显式声明 `max_retries` 的 `bounded_corrective` 节点，**不允许无界循环**；
4. **规范化哈希**：对排除 `hash/status/created_at` 后的规范 JSON 求 SHA-256，作为 Recipe 的不可变身份。Recipe 状态机为 `draft → validated → published → deprecated`。

这样做的直接收益：Evidence Capsule 里的 `recipe_hash` 可以唯一对应一份图定义，评测结果、线上问题都能溯源到确切的一版装配。

### 3.2 内置 Recipe 谱系（V0.1 → V1）

仓库内置 11 个 Recipe，刻意组织成一条"能力演进阶梯"，每一级只增加一个可讲清楚的概念：

| Recipe | 新增概念 |
|---|---|
| `custom_ingest` | 文档解析链路本身也是 Recipe |
| `v0_1_dense` | 最小可用基线：q → dense → context → llm → policy_gate |
| `v0_2_hybrid` | Dense + Sparse 双路召回 + RRF 融合 |
| `v0_3_intent` | 意图路由 + Metadata 过滤前置 |
| `v0_4_rerank` | Cross-Encoder 重排（candidate_k=50 → final_k=6） |
| `v0_5_context` | 父子块扩展（parent expansion） |
| `v0_6_corrective` | 证据评分 + 有界纠错重试（max_retries=1） |
| `v0_7_graph` | 图谱检索旁路 |
| `v0_8_multimodal` | PDF 版面/页级检索旁路 |
| `v0_9_operations` | 生产信封：rate_limit + cache 前置 |
| `v1_controlled_agent` | 受控 Agent：检索 → 工单草稿 → 人工审批 |

### 3.3 执行器的诚实边界（重要）

当前基线执行器（`app.py` `_execute`）对节点的实现深度不一，这是**有意公开的事实**而非隐藏细节：

- **真实实现**：dense 检索（Qdrant + 分数阈值 0.5 + 真相源过滤 + 词法回退）、LLM 生成（OpenAI 兼容端点 + 三级降级 + 引用修复）、请求级安全门、工单草稿与审批停靠、Trace 与 Capsule 落盘。
- **记录性实现**：`sparse_retrieve` / `rrf_fusion` / `reranker` / `graph_query` / `pdf_page_retrieve` 等节点当前在编译层有完整类型约束，但在运行层复用同一召回结果或仅记录证据数量。也就是说 **V0.2–V0.8 的图结构差异是真实的，运行时行为差异目前有限**。把这些节点接到真实后端（Qdrant named sparse、cross-encoder 服务、Neo4j）是 roadmap 的第一优先级（见 `docs/roadmap.md`）。

### 3.4 检索降级与引用修复

- 检索：优先 Qdrant dense（`retrieval_score_threshold=0.5` 过滤低分噪声），无命中时回退到带停用词表的词法重叠打分（`lexical_fallback`），Trace 中的 `backend` 字段如实记录用的是哪条路。
- 生成：答案若未包含任何 `[S#]` 引用标记，自动替换为抽取式证据摘要（`citation_repair_fallback`），保证"有证据的回答必然带引用"这一产品契约成立。该机制来自一次真实的失败实验（见 `docs/experiments.md` 实验 2）。

## 4. 安全面：请求级安全门 + 受控 Agent

### 4.1 三类高风险请求（`policies/basic.py`）

| 风险码 | 含义 | 中英正则示例 |
|---|---|---|
| `refund_promise` | 要求承诺/保证退款赔偿 | `promise`、`guarantee`、`一定…退款`、`保证…赔偿` |
| `legal_conclusion` | 要求认定违法/责任归属 | `illegal`、`broke the law`、`违法`、`认定…责任` |
| `account_decision` | 要求做账户处置决定 | `close the customer account`、`决定…封禁` |

命中后：`question` / `policy_gate` / `approval` 节点记录拒绝理由，其余节点标记 `skipped`（不是消失），返回一段固定的安全改写话术，`safety.request_safety_gate` 写入风险码，`human_review=true`。**拒答也生成完整 Evidence Capsule**——拒绝本身是需要审计的决策。

### 4.2 受控 Agent 的"不能做"清单

`v1_controlled_agent` 的产出是结构化工单草稿（artifact），显式携带 `forbidden_actions: [send_customer_message, write_external_crm, promise_refund, decide_legal_liability]`、`missing_fields`（商户/日期/此前动作三个字段的正则探测）与 `status: pending_human_approval`。草稿永远停在 `approval` 节点，框架层面没有任何外部副作用执行路径（`safety.side_effects` 恒为 `false`）。

## 5. 模型接入：注册端点而不是托管权重

所有模型（chat / embedding / reranker）通过 OpenAI 兼容协议接入（LM Studio、Ollama、vLLM、llama.cpp 或云端服务均可）。Model Registry（`POST /api/v1/models` + `probe`）只保存 `base_url + model_name + parameters`，**Web 应用永不执行用户上传的权重文件**——这同时是安全边界和部署解耦：升级模型 = 换一条注册记录，Capsule 里的模型 ID 保证结果可归因。

上传文档时可为单次 ingest 指定 embedding 模型（`embedding_model_id` 参数），实现"不同知识库用不同向量模型"而不改全局配置。

## 6. 评测作为一等公民

评测不是外挂脚本，而是与 Trace/Capsule 同一套契约的消费者：

- `POST /api/v1/evals`：对一组 case 逐条真实运行，产出带 `trace_id` 的行级报告并落盘 `data/artifacts/eval_*.json`；
- `scripts/run_framework_benchmark.py`：7 case 冒烟基准（证据率/引用率/拒答正确率/Trace 完整率/延迟分位数）；
- `scripts/run_golden_eval.py` + `src/openrag_forge/eval/golden.py`：带标注的 Golden Eval（Hit@k / Recall@k / MRR / nDCG / 引用有效性 / 引用完备性 / 拒答正确性 + Wilson 置信区间），answerable 与 refusal 两个切片分开计分。

指标定义、实测数字与局限见 `docs/evaluation.md`；实验过程见 `docs/experiments.md`。

## 7. 边界与已知限制（如实陈述）

1. 运行层多个检索增强节点是记录性实现（见 3.3）；
2. 评测语料目前只有 1 个本地文档、7 个标注 case，置信区间刻意报告得很宽，**不构成企业级质量声明**；
3. `Store` 的生产适配器（Postgres/MinIO）只定义了端口与 extras 依赖，实现未提交；
4. sparse / rerank / graph / 多模态检索无真实后端；
5. 原始评测 JSON 报告（`reports/`）被 gitignore，仓库内只保留文档化结论——发布级评测应把冻结快照一并入库（见 `docs/roadmap.md`）。
