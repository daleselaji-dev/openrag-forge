# 模块设计（生产级板块说明书）/ Production Module Design

本文是 OpenRAG Forge 每一个产品板块的生产级设计说明：目的、契约、数据模型、API、UI 呈现、失败模式、运营调参旋钮，以及它如何出现在 Trace 和 Blocks 中。**所有描述与仓库当前代码一一对应**；仍是运行桩（runtime-stub）的能力在文中和 UI 中都明确标注，不伪装。

运行层实现深度的三档标签（与 `/api/v1/plugins` 返回的 `runtime` 字段、工作台组件库与检查器中的徽标一致）：

| 标签 | 含义 |
|---|---|
| `implemented` | 运行层有真实行为；降级路径会写入 Trace（如 `backend=lexical_fallback`） |
| `degradable` | 有真实后端调用路径；后端不可用时如实直通并在 Trace 记录原因 |
| `stub` | compile-complete / runtime-stub：编译期端口类型完整，运行层暂无真实后端，执行时记 `skipped` |

当前 27 节点中唯一的 `stub` 是 `graph_query`（需要 Neo4j）；`reranker` 是 `degradable`（端点提供 Cohere/Jina/TEI 兼容 `/rerank` 时真实调用，否则如实直通）；其余节点均为 `implemented`。

---

## 1. Ingest 与解析路由

**目的**：把任意上传文件确定性地变成带出处的 ParsedBlock，而且每个决策可解释、可复测。

**契约**
- 路由是规则引擎而不是 LLM：内容签名（`%PDF`、`PK` zip 头）+ 扩展名 + MIME → 7 条路由之一（`native_text` / `html_structure` / `pdf_page_text` / `pdf_layout` / `office_structure` / `tabular` / `json_structure`）。
- 每个决策返回 `route + confidence + reason_codes + fallback_route`，随 Document 持久化。
- 用户显式指定路由时记录 `user_selected_route`（confidence=1.0）。
- **重解析不覆盖源**：`POST /documents/{id}/reprocess` 只把 `version + 1`，源文件与 SHA-256 不变。
- Ingest 链路配置来自 ingest Recipe（默认 `custom_ingest`）的节点 config：`parse_route.route`、`chunker.max_chars/overlap`、`metadata_enricher.keywords_top_k`、`embed_index.model_ref`；query 参数可临时覆盖。

**数据模型**：`Document`（document_id、sha256、size、media_type、status: uploaded/parsed/failed/quarantined、parser_route、parser_confidence、reason_codes、version）。

**API**
- `POST /api/v1/knowledge-bases/{kb}/documents?route=&embedding_model_id=&ingest_recipe_id=&max_chars=&overlap=`
- `POST /api/v1/documents/{id}/reprocess?route=&max_chars=&overlap=&embedding_model_id=`
- `GET /api/v1/documents/{id}`

**UI 呈现**：底部「文档 / Blocks」页——上传控件（路由 / Embedding 模型 / max_chars / overlap 全部可调）、文档列表逐行显示路由、置信度、reason_codes、版本；「按当前配置重解析」按钮。

**失败模式**：解析失败 → 文档状态 `failed` + `parser_failed` reason code，源文件保留，HTTP 422 带完整上下文；空文件 400；超限 413。索引失败不影响解析结果（见 §4）。

**调参旋钮**：route 覆盖、max_chars（200–4000）、overlap（0–400）、keywords_top_k、embedding_model_id、ingest_recipe_id。

**Trace / Blocks 呈现**：每次 ingest 产出 4 条 TraceEvent（route → chunk → meta → index），`details.impact` 携带 confidence、reason_codes、config_used、chunks 计数；上传响应与文档面板都展示该 Trace。

## 2. Blocks（ParsedBlock）

**目的**：解析结果的原子单位，是"检索证据从哪里来"的最终出处。

**契约**：`ParsedBlock = {block_id(内容哈希), document_id, block_type(heading/paragraph/table/row/page/code/unknown), text, order, page?, heading_path, metadata}`。block_id 由 `document_id:order:text` 的 SHA-256 前缀生成——同样内容重复解析得到同样 ID。

**角色如何影响下游**：heading 提供 title/heading_path 元数据；paragraph 是最常见的 Chunk 来源；row 保留行号支持按行引用；page 保留页码并构成 `pdf_page_retrieve` 的候选池；Chunk 通过 `block_ids` 回链到 Block。

**API**：`GET /api/v1/documents/{id}/blocks`。

**UI 呈现**：文档面板 Blocks 页逐块显示类型徽标 + 角色说明（悬浮与行内均有中文解释）+ 顺序/页码/ID；检查器「Block 作用」页解释画布节点级别的 block 职责。

**失败模式**：空文本块在解析期即被丢弃；`unknown` 类型提示用户检查路由。

## 3. Chunking 与 Metadata 增强

**目的**：把 Block 切成检索原子（Chunk），并补充可过滤、可展示的元数据。

**契约**
- `chunk_blocks(blocks, max_chars, overlap)`：固定窗口 + 重叠切分；`chunk_id = chunk:{document_id}:{block_id}:{index}`；`block_ids` 记录出处。
- `enrich_chunks`（真实实现，`parsers/enricher.py`）：title（最近前置 heading 或文件名）、language（zh/en/mixed/unknown，按汉字/拉丁比例）、keywords（词频 Top-K）。增强字段随 Chunk 持久化，进入 Qdrant payload 与 Evidence metadata。

**API**：`GET /api/v1/documents/{id}/chunks`。

**UI 呈现**：文档面板 Chunks 页显示切分文本、title/language/keywords；检查器中 chunker / metadata_enricher 节点提供结构化旋钮。

**失败模式**：无。纯本地确定性计算。

**调参旋钮**：max_chars、overlap、keywords_top_k——ingest 时真实生效（写入 Trace 的 config_used）。

## 4. Embedding / 派生索引（Qdrant）+ 词法兜底

**目的**：语义检索加速层。**Qdrant 永远是可重建的派生索引，绝不是真相源。**

**契约**
- 每个 point 的 payload 带 `chunk_id`；查询结果先经 SQLite 真相源 chunk 集合过滤——索引里的"幽灵点"永远不会成为证据。
- 索引失败 → `status=deferred + reason + next_action`，不阻塞上传，不 500。
- `POST /knowledge-bases/{kb}/index/rebuild` 随时全量重建。
- 词法兜底：`retrieval/lexical.py` 内置纯 Python BM25（sparse 主后端）与词重叠打分（dense 降级路径），直接在真相源 Chunk 上运行，完全离线可用。
- Embedding 端点支持 per-model API key（服务端 Authorization 头，见 §5）。

**失败模式与 Trace 呈现**：Qdrant/Embedding 离线 → dense 节点 Trace 记 `backend=lexical_fallback + fallback_reason`；ingest 的 index 步记 `failed / deferred + next_action`。工作台顶栏健康芯片实时显示 qdrant / 模型服务状态。

**调参旋钮**：`score_threshold`（默认 0.5，全局 `OPENRAG_RETRIEVAL_SCORE_THRESHOLD` 或节点级覆盖）、collection、模型绑定。

## 5. 模型注册表（导入 API / 模型）

**目的**：以 OpenAI-compatible 协议接入任意 chat / embedding / reranker 服务（LM Studio、Ollama、vLLM、llama.cpp、云端），**权重永远不进 Web 应用**。

**契约**
- Profile：`{model_id, display_name, kind, base_url, model_name, api_key?, parameters}`。
- **API key 只保存在服务端 SQLite**：`GET /models`、注册响应一律脱敏成 `has_api_key: bool`；key 永不进入 Trace / Capsule（执行器记录 config 前统一 `_redact`）。
- `POST /models/{id}/probe`：embedding 走真实 `/embeddings` 调用，chat/reranker 走 `/models`，带 Authorization 头。
- 节点级绑定：任何带 `model_ref` 旋钮的节点（dense_retrieve / embed_index / llm_generate / reranker）可绑定注册的 profile；上传时可指定 `embedding_model_id` 实现"不同知识库不同向量模型"。
- 全局默认走 `.env`（`OPENRAG_CHAT_API_KEY` 等）。

**API**：`GET/POST /api/v1/models`、`POST /api/v1/models/{id}/probe`。

**UI 呈现**：顶栏「导入 API / 模型 / Recipe」抽屉——注册表单（含密码型 key 输入）、逐模型探测按钮与结果、「用于下次 ingest」快捷绑定；检查器的 model 型旋钮按 kind 过滤下拉。

**失败模式**：探测失败返回 `unreachable + error + next_action`，不抛 500；绑定不存在/类型不符的模型时执行器回退默认并如实记录。

## 6. Recipe 编译器

**目的**：画布只是创作面，执行的唯一事实是编译后的不可变 Recipe。

**契约**（`pipeline/compiler.py`）
1. 节点合法性：仅 27 种注册类型（`NODE_CATALOG`）；
2. 端口类型检查：每条边 `source_port → target_port` 必须与目录声明匹配；
3. 环检测：Kahn 拓扑排序拒绝未声明的环——纠错必须用带显式 `max_retries` 的 `bounded_corrective`；
4. 规范化 SHA-256 哈希（排除 hash/status/created_at）作为不可变身份；
5. 状态机 `draft → validated → published → deprecated`；已发布 Recipe 不可变。

**节点目录即文档**：`node_catalog()` 为每个节点输出 title / description / why / downstream / runtime / tunables（名称、类型、范围、说明）/ config_defaults——组件库与检查器直接消费，保证 UI 说明与代码同源。

**API**：`GET /recipes`、`POST /recipes`、`PUT /recipes/{id}`、`POST /recipes/{id}/validate`、`POST /recipes/{id}/publish`、`POST /recipes/import`（单个 / `{"recipe":…}` / `{"recipes":[…]}`；与已发布同名自动 `_imported` 后缀）、`GET /recipes/{id}/export`（附件下载）。

**UI 呈现**：画布工具栏——Recipe 切换、dirty 草稿 / hash 徽标、编辑副本 / 保存草稿 / 校验 / 发布 / 导出 JSON / 撤销（Ctrl+Z）。

**失败模式**：编译错误以 422 + 中文原因返回（未知节点 / 端口不兼容 / 环）；UI 在保存与校验时原样展示。

## 7. 运行时执行器 + Trace

**目的**：按已编译 DAG 的**真实数据流**逐节点执行，让"图结构差异 = 运行行为差异"。

**契约**（`pipeline/executor.py`）
- 拓扑执行 + 确定性类型优先级（检索 → 纠错 → 上下文 → 生成 → 策略）；节点输入通过入边端口从上游输出取值。
- 每节点一条 `TraceEvent{status: completed/failed/skipped, summary, duration_ms, details.impact}`。impact 是 UI 可直接渲染的影响字段：candidate_count、backend、evidence_ids、fallback/skipped/passthrough 原因、retries_used、dropped_over_budget、config_used（已脱敏）、next_action 等。
- 各节点运行级行为：
  - `dense_retrieve`：Qdrant + 阈值 + 真相源过滤；离线降级词法（`lexical_fallback`）。
  - `sparse_retrieve`：内置 BM25（`bm25_local`），k1/b 可调；请求了未配置的 backend 会记录回退原因。
  - `rrf_fusion`：≥2 路候选真实融合（`Σ weight/(k+rank)`）；单路直通并标注 `passthrough: true`。
  - `reranker`：绑定端点提供 `/rerank` 时真实调用；否则截断直通并记 `backend=passthrough + 原因`。
  - `parent_expansion`：按真相源相邻 Chunk（order±window）扩展证据文本。
  - `context_builder`：去重 → 排序 → max_per_doc → token_budget 字符预算截断 → 产出带 [S#] 的最终 Evidence。
  - `evidence_grade` + `bounded_corrective`：显式判定 insufficient 后最多重试 `max_retries`（硬上限 2）次查询变体检索。
  - `llm_generate`：OpenAI 兼容端点（可绑定 profile）三级降级 → 抽取式 → 无证据声明；无 [S#] 触发 `citation_repair_fallback`。
  - `metadata_filter` / `intent_router` / `pdf_page_retrieve`（页级 BM25）/ `cache`（TTL 命中短路，下游记 `skipped: cache_hit`）/ `rate_limit`（滑动窗口，超限安全短路、HTTP 仍 200）。
  - `graph_query`：**runtime-stub**，记 `skipped + next_action`，不产出伪造证据。
- 请求级安全门在执行器之前（见 §8）。

**API**：`POST /runs`（mode: run/preview）、`GET /runs?limit=`、`GET /runs/{id}`、`GET /runs/{id}/events`（SSE）。Preview = dry compile：逐节点记录 runtime 标签与合并后的 config，不调模型、不写索引。

**UI 呈现**：画布节点按最近一次 Trace 高亮（completed 绿 / failed 红 / skipped 虚线），真实触发的边有流动动画；底部「Trace 时间线」逐行显示状态、耗时、摘要、impact chips，点击展开完整 details 并联动选中画布节点；检查器 Trace 页过滤出所选节点的事件。

**失败模式**：单节点异常记 `failed + error_type` 后继续（不 500）；限流 / 缓存短路都是显式 Trace 事件。

## 8. 安全策略门与受控 Agent

**目的**：拒绝与放行都是需要审计的决策。

**契约**
- **请求级**（检索之前）：`refund_promise` / `legal_conclusion` / `account_decision` 中英正则命中 → 安全改写话术，其余节点标 `skipped`（不消失），`safety.request_safety_gate` 写风险码，拒答同样产出完整 Capsule。
- **回答级**（policy_gate 节点）：校验 [S#] 引用必须对应真实证据（无效引用写 `safety.invalid_citations` 并标人工复核）；无证据强制 `human_review=true`。
- **受控 Agent**：`build_ticket_draft` 只产出结构化草稿——`missing_fields` + `forbidden_actions`（发消息 / 写 CRM / 承诺退款 / 认定责任）+ `status=pending_human_approval`，永远停在 `approval` 节点；`safety.side_effects` 恒为 `false`；无界循环被编译器禁止。

**UI 呈现**：结果面板的安全徽标（安全门拦截 / 需人工复核 / cache hit / rate limited）；Agent 产物以独立折叠块展示。

## 9. Evidence Capsule

**目的**：一次运行 = 一个可下载、可归档、可仲裁的单文件 JSON。

**契约**：`{capsule_version, created_at, settings(profile+模型ID), run_id, recipe_id, recipe_hash, answer, artifact, evidence[], safety, trace[]}`。recipe_hash 唯一锚定图定义；API key 不在其中。

**API / UI**：`GET /runs/{id}/capsule`（附件下载）；结果面板与「下载 / 自托管」抽屉都有下载入口。

## 10. Eval 评测

**目的**：评测与 Trace/Capsule 同一套契约，杜绝无据基准声明。

**契约**：`POST /api/v1/evals` 对每个 case 真实运行，行级结果带 `trace_id` 回链完整 Trace；报告落盘 `data/artifacts/eval_*.json`。指标：hit_at_k（answerable 切片）、refusal_correctness（refusal 切片）。离线脚本：`scripts/run_framework_benchmark.py`（冒烟）与 `scripts/run_golden_eval.py`（Hit@k/Recall/MRR/nDCG/引用有效性 + Wilson 区间）。**非目标**：不带数据集快照 + recipe hash + 报告的基准声明。

## 11. 工作台 UX

**目的**：访问者打开即用的装配工作台，始终能回答三个问题——每一步的 Trace 与影响、Block 的作用、如何调配。

**结构**（`web/src/`，React + React Flow，无新增重型框架）
- `components/TopBar`：知识库选择/新建、真相源与 Qdrant/模型服务健康芯片、导入与下载入口。
- `components/Palette`（左）：按 ingest/index/query/retrieve/generate/policy/agent/operations 分组的 27 节点，含"这个 Block 做什么"、runtime 徽标、端口、搜索、一键加入画布。
- `components/FlowCanvas`（中）：类型化端口连线、拖拽布局、Backspace/Delete 删除、Ctrl+Z 撤销、Trace 状态高亮与触发边动画。
- `components/Inspector`（右，三页签）：**调配**（结构化旋钮：范围校验、枚举、模型绑定下拉、原始 JSON 切换，应用 → dirty 草稿）；**Block 作用**（做什么 / 为什么 / 对下游影响 / 端口 / 诚实声明）；**Trace**（所选节点的事件与 impact）。
- `components/RunDock`（底）：问题输入 + top_k + Preview/真实运行（请求中止保护：快速切换 Recipe 不会串台）、结果（答案 / 证据 / Agent 产物 / Capsule 下载 / 安全徽标）、Trace 时间线、文档 / Blocks、场景库、运行历史（可回放任一历史 run 的 Trace 到画布）。
- `components/ImportsDrawer` / `DownloadDrawer`：见 §5、§12。

**失败模式**：API 不可达时顶栏与消息区提示明确的下一步；上传 / 运行 / 编译错误都以中文原样呈现；离线降级在 Trace 与消息（"N 个节点走了降级/跳过路径"）中大声显示。

## 12. 下载 / 自托管 / 预览分发

- **预览网页**：工作台就在 `/`（FastAPI 伺服 `web/dist` 构建产物；开发模式 Vite 代理 `/api`）；API 全部在 `/api/v1` 下。
- **下载**：工作台「下载 / 自托管」抽屉内置完整命令（clone → venv → `pip install -e ".[dev]"` → `uvicorn openrag_forge.app:app --port 18000`；可选 `docker compose up -d qdrant api`、`npm run build`），加上当前 Recipe JSON 导出与最近一次运行的 Capsule 下载。
- Lite 模式**零外部依赖可跑**：无 Qdrant / 模型服务时上传解析、BM25 检索、抽取式回答、Trace、Capsule 全部可用。

## 13. 场景库 / Packs

- 内置 3 个场景（客服投诉 / 内部政策 / 受控客服 Agent），每个声明业务问题、所需资料、默认 Recipe、应观察的 Trace；`POST /api/v1/scenarios` + 工作台导入 Scenario JSON 支持用户自定义。
- 领域内容与内核物理隔离：`packs/customer-support-cfpb/` 是可替换的示例包，内核 provider-agnostic。

---

## 诚实边界（当前仍未做的事）

1. `graph_query` 是 runtime-stub（需要 graph profile + Neo4j）；UI 与 Trace 均有明确标注。
2. `sparse_retrieve` 的 Qdrant named-sparse 后端未接（内置 BM25 是真实但本地的稀疏检索）；配置该 backend 会在 Trace 记录回退。
3. `reranker` 依赖外部 `/rerank` 端点；无端点时如实直通。
4. Store 的 Postgres/MinIO 生产适配器仍只有端口与 extras 声明，实现未提交。
5. cache / rate_limit 是进程内实现（重启即失效），未接 Redis。
6. 评测语料与 case 数仍然很小，不构成企业级质量声明（见 `docs/evaluation.md`）。
