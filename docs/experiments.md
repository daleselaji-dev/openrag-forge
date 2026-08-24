# Experiments log / 实验记录

本文汇总仓库中**真实做过并留下证据**的实验，以及每个实验的动机、方法、结果与由此产生的代码/产品变更。所有数字均来自仓库内已提交的评测文档（来源路径在每节标注）；未做过的实验不在此列，缺失的证明材料标记为「待补充」。

运行环境（两组实验相同）：本地 Lite profile，Qdrant 单机，LM Studio 提供 `deepseek-r1-distill-qwen-7b`（chat）与 `text-embedding-qwen3-embedding-0.6b`（embedding），语料为 1 个本地支持文档（9 个 Qdrant points）。

---

## 实验 1：框架冒烟基准的首轮失败与同集修复（before/after）

- **来源**：`docs/benchmark-smoke-v0.1.md`；脚本 `scripts/run_framework_benchmark.py`
- **动机**：验证框架能否通过真实 API + Qdrant + LM Studio 端到端跑完一个固定测试集，并且**能暴露问题而不是掩盖问题**。
- **方法**：7 个固定 case（英文/中文/精确术语/域外/退款承诺/法律结论/账户处置），Recipe `v0_1_dense`，逐条调用 `POST /api/v1/runs`，统计证据率、引用率、拒答正确率、Trace 完整率与延迟分位数。
- **首轮暴露的两个真实失败**：
  1. 停用词未过滤，导致域外问题（员工休假政策）在词法回退路径上召回了一条无关支持文档 chunk；
  2. 本地 7B 模型有时省略 `[S#]` 引用，且漏判英文高风险措辞。
- **修复**：加入停用词表与分数阈值过滤（`app.py` `_STOPWORDS`、`retrieval_score_threshold`）；加入引用修复回退（答案无 `[S#]` 时替换为抽取式证据摘要）；补充英文高风险正则（`policies/basic.py`）。
- **同集 before/after 结果**（来源：`docs/benchmark-smoke-v0.1.md`）：

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| Answerable citation presence | 0.500 | 1.000 |
| Refusal correctness | 0.333 | 1.000 |
| Out-of-domain evidence | 1 条无关命中 | 0 条 |

- **修复后完整指标**：answerable evidence rate 1.000、citation presence 1.000、refusal correctness 1.000、trace completeness 1.000、p50 623 ms、p95 9,619 ms。
- **结论与克制**：延迟差异不作为质量改进声明（本地模型负载随运行波动）；该实验证明的是"框架能检测检索失败与安全路由失败，并在同一测试集上验证修复"，不证明企业级质量。

## 实验 2：引用修复回退（citation repair fallback）

- **来源**：实验 1 的失败 2 直接催生；实现见 `src/openrag_forge/app.py`（`citation_repair_fallback`）与 `src/openrag_forge/generation/client.py`
- **假设**：与其提示工程反复"求"小模型带引用，不如在框架层保证契约：**有证据的回答必然带引用**。
- **做法**：生成后检测 `[S#]` 标记；缺失时替换为确定性的抽取式证据摘要（前 3 条证据 + 免责声明），Trace 中 `provider` 记为 `citation_repair_fallback`，`citation_repaired: true` 落入事件详情——修复行为本身可审计。
- **效果**：answerable citation presence 从 0.500 → 1.000（同实验 1 表格）。
- **代价/取舍**：被修复的回答流畅度下降（从生成式变为摘要式）。这是刻意的取舍：在客服合规场景，"可引用"优先于"好看"。

## 实验 3：带标注的 Golden Eval（v0.1-dev）

- **来源**：`docs/golden-eval-v0.1-dev.md`；脚本 `scripts/run_golden_eval.py`；指标实现 `src/openrag_forge/eval/golden.py`；数据集 `packs/customer-support-cfpb/evals/golden_v0_1_dev.jsonl`（3 条 answerable + 4 条 refusal）
- **动机**：冒烟基准只测"有没有证据"，无法回答"召回的是不是**对的**证据"。需要把评测从存在性检查升级为标注对齐检查。
- **方法上比冒烟基准更客观的五点**：
  1. 检索按证据标签打分（`expected_source_filenames` 绑定到当前真相源的实际 chunk ID），而非只看是否存在证据；
  2. refusal case 从 Recall/MRR/nDCG 中排除，进入独立安全切片；
  3. 引用有效性校验 `[S#]` 索引是否落在返回证据范围内；
  4. 引用完备性校验必需事实词是否出现在答案或被引证据中；
  5. 报告记录语料健康度、模型 ID、recipe ID、run ID、延迟与逐 case 失败明细，二元指标附 Wilson 95% 置信区间。
- **实测结果**（来源：`docs/golden-eval-v0.1-dev.md`）：

| 指标 | 值 | Wilson 95% CI |
|---|---:|---:|
| Hit@k | 1.000 | 0.438–1.000 |
| Recall@k | 1.000 | — |
| MRR | 1.000 | — |
| nDCG | 1.000 | — |
| Citation validity | 1.000 | 0.438–1.000 |
| Citation completeness | 1.000 | 0.438–1.000 |
| Refusal correctness | 1.000 | 0.510–1.000 |
| Trace completeness | 1.000 | 0.646–1.000 |
| p50 端到端 | 690 ms | — |
| p95 端到端 | 8,050 ms | — |

- **诚实解读**：样本量太小（3+4），置信区间下界低至 0.438——报告置信区间正是为了防止把满分误读为质量证明。这是"评测机制已就位"的证明，不是"系统达到满分质量"的证明。

## 实验 4：索引血统控制——过滤 Qdrant 幽灵点

- **来源**：`docs/golden-eval-v0.1-dev.md`（"A stale Qdrant point outside the SQLite truth source was observed and filtered"）；实现见 `src/openrag_forge/app.py`（`truth_chunk_ids` 过滤）
- **现象**：Golden Eval 运行中观测到一个 Qdrant point，其 chunk 已不在 SQLite 真相源中（早期上传残留）。
- **处理**：不删库重来、不隐藏，而是在查询路径加入真相源过滤——Qdrant 命中必须能在 SQLite chunk 集合中对上号才能成为证据；该事件在评测文档中记录为 index-lineage control。
- **意义**：这是"真相源 vs 派生索引"分层（`docs/design.md` 第 2.1 节）第一次在实测中兑现价值，也验证了「派生索引永远可疑、真相源永远兜底」的设计假设。

## 实验 5：模型离线可用性（降级路径验证）

- **来源**：设计与测试佐证：`tests/test_core.py`（`test_api_upload_preview_and_capsule` 在无 Qdrant/LM Studio 环境下通过）、`src/openrag_forge/generation/client.py` 三级降级、上传接口的 `deferred` 索引状态
- **验证内容**：在 Qdrant 与模型服务全部离线时，上传、解析、Preview、拒答安全门与 Evidence Capsule 下载全部可用；索引报告 `deferred + next_action` 而非 500。CI（`.github/workflows/ci.yml`）在无任何模型服务的 runner 上跑通全部测试，即为持续回归证明。
- **待补充**：一份专门的"服务降级矩阵"实验记录（Qdrant 单独离线 / Embedding 单独离线 / Chat 单独离线三种组合下的行为对照表）。

---

## 尚未做、建议补做的实验（用于面试与发布）

| 实验 | 目的 | 建议做法 |
|---|---|---|
| Recipe 横向对比（V0.1 vs V0.2 vs V0.4） | 证明混合检索与重排的增益 | 先接通 sparse/rerank 真实后端，再在同一冻结语料上跑 Golden Eval，报告成对差异与置信区间 |
| 扩大 Golden Set 至 50+ case | 收窄置信区间，支持发布级声明 | 双人独立标注 `expected_chunk_ids`，冻结语料快照，记录标注一致率（Cohen's kappa） |
| 多文档跨域语料 | 验证跨文档泛化 | 导入 CFPB 官方指引 + 脱敏工单混合语料（`packs/customer-support-cfpb/sources.json` 已列出 4 个公开来源） |
| 分块参数扫描（max_chars/overlap） | 量化 chunking 对 Recall 的影响 | 网格实验 + 同一 Golden Set，产出参数-指标曲线 |
| 人工引用支持度标注 | 补齐自动指标测不到的"引用是否真正支持结论" | 抽样人工双评 + 冲突仲裁，报告 Citation Support 率 |
| 延迟分解 | p95 8–9.6s 的归因 | 利用 TraceEvent 的 `duration_ms` 按节点分解，区分模型加载抖动与框架开销 |

原始评测 JSON（`reports/framework_smoke_latest.json`、`reports/golden_eval_latest.json`）目前被 `.gitignore` 排除，仓库内仅保留文档化结论。**建议**：发布级评测将冻结的报告快照连同语料清单一起提交到 `docs/reports/`（或以 GitHub Release 附件形式归档），让每个数字可点击溯源。
