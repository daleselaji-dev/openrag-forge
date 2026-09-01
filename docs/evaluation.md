# Evaluation & benchmarks / 评测与基准汇总

本文是仓库两套评测（framework smoke benchmark 与 golden eval）的统一入口：指标定义、如何复现、当前实测结果、以及每个数字**能证明什么、不能证明什么**。所有数字如实引用自仓库内文档，来源路径逐一标注；缺失的证明材料标记为「待补充」。

## 1. 评测体系总览

| 层级 | 工具 | 回答的问题 | 证据产物 |
|---|---|---|---|
| L0 单元/契约测试 | `tests/test_core.py`（pytest，CI 每次 push 运行） | 路由嗅探、编译器拒环、上传→Preview→Capsule→安全门→Eval 端到端契约是否成立 | CI 状态（`.github/workflows/ci.yml`） |
| L1 冒烟基准 | `scripts/run_framework_benchmark.py` | 框架能否在真实 API + Qdrant + 本地模型上跑完固定测试集；安全行为是否正确 | `reports/framework_smoke_latest.json/.md`（本地生成） |
| L2 标注 Golden Eval | `scripts/run_golden_eval.py` + `src/openrag_forge/eval/golden.py` | 召回的是不是**对的**证据；引用是否有效且完备；拒答是否正确 | `reports/golden_eval_latest.json/.md`（本地生成） |
| L3 在线 Eval API | `POST /api/v1/evals` | 用户用自己的 JSONL case 对当前知识库/Recipe 做即席评测 | `data/artifacts/eval_*.json` |

设计原则（与 `CONTRIBUTING.md` 一致）：**任何基准声明必须附带数据集快照、recipe hash 与实际报告**。每一行评测结果都带 `run_id`，可反查完整 `q → d → c → g → p` Trace 与 Evidence Capsule。

## 2. 指标定义

实现位置：`src/openrag_forge/eval/golden.py`（L2）、`scripts/run_framework_benchmark.py`（L1）。

| 指标 | 定义 | 切片 |
|---|---|---|
| Hit@k | 排名前 k 的证据中是否命中任一标注相关 chunk | answerable |
| Recall@k | 命中的标注相关 chunk 数 / 标注相关 chunk 总数 | answerable |
| MRR | 首个相关 chunk 排名的倒数 | answerable |
| nDCG | 相关 chunk 按排名折损的累计增益 / 理想排序增益 | answerable |
| Citation validity | 答案中所有 `[S#]` 索引都落在返回证据范围内 | answerable |
| Citation completeness | 每个必需事实词（`expected_terms`）出现在答案或被引证据文本中 | answerable |
| Refusal correctness | 高风险/域外 case 被正确拒答（安全门触发或无证据），且 answerable case 未被误拒 | refusal 独立切片 |
| Trace completeness | Trace 覆盖 `{q, d, c, g, p}` 全部节点 | 全部 |
| p50 / p95 latency | 端到端 API 延迟分位数 | 全部 |

方法论要点：

- **refusal case 不进入 Recall/MRR/nDCG**——拒答质量与检索质量是两类问题，混在一起会互相污染；
- 二元比率类指标附 **Wilson 95% 置信区间**（`wilson_interval()`），小样本下如实暴露不确定性；
- Golden Set 标签当前通过 `expected_source_filenames` 绑定到当前真相源的实际 chunk ID（dev 阶段做法）；发布级应替换为人工复核的 `expected_chunk_ids` 并冻结语料快照（见第 5 节）。

## 3. 当前实测结果

### 3.1 Framework smoke benchmark（L1）

来源：`docs/benchmark-smoke-v0.1.md`。环境：Recipe `v0_1_dense`，7 case，语料 1 个本地文档 / 9 个 Qdrant points，chat `deepseek-r1-distill-qwen-7b`，embedding `text-embedding-qwen3-embedding-0.6b`。

| 指标 | 实测值 |
|---|---:|
| Answerable evidence rate | 1.000 |
| Citation presence | 1.000 |
| Refusal correctness | 1.000 |
| Trace completeness | 1.000 |
| p50 端到端延迟 | 623 ms |
| p95 端到端延迟 | 9,619 ms |

同集修复前后对照（首轮基线刻意暴露了两个真实失败，详见 `docs/experiments.md` 实验 1）：

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| Answerable citation presence | 0.500 | 1.000 |
| Refusal correctness | 0.333 | 1.000 |
| Out-of-domain evidence | 1 条无关命中 | 0 条 |

### 3.2 Golden Eval v0.1-dev（L2）

来源：`docs/golden-eval-v0.1-dev.md`。数据集：`packs/customer-support-cfpb/evals/golden_v0_1_dev.jsonl`（3 条 answerable + 4 条 refusal），schema 见 `packs/customer-support-cfpb/evals/golden_set.schema.json`。

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

## 4. 这些数字能证明什么 / 不能证明什么

**能证明：**

- 框架可以通过真实 API、真实向量库与真实本地模型端到端执行固定测试集，每一行结果可溯源到 run ID 与完整 Trace；
- 评测机制能**检测**检索失败与安全路由失败，并在同一测试集上验证修复（before/after 对照）；
- 安全切片（退款承诺/法律结论/账户处置/域外）在当前 case 上全部正确拒答；
- 指标管线（标注对齐、切片隔离、置信区间、逐 case 明细）已经就位且可复现。

**不能证明：**

- 任何企业级/生产级质量声明——语料只有 1 个文档，answerable 样本只有 3 条，Hit@k 的置信区间下界只有 0.438；
- 跨文档、跨领域泛化能力；
- V0.2+（hybrid/rerank 等）相对 V0.1 的质量增益——V0.2 的本地 BM25/RRF 已可运行，rerank 依赖可达 `/rerank` 端点；横向对比仍要在同一冻结快照上完成，并单独报告后端与 fallback 状态；
- 延迟结论——本地模型负载波动大，p95 差异不作为改进声明。

## 5. 复现步骤

前置：API 运行中（如 `http://127.0.0.1:18003`）、Qdrant 与 LM Studio 在线、`examples/smoke.md` 已上传并索引。

```powershell
# L1 冒烟基准
$env:PYTHONPATH = ".\src"
.\.venv\Scripts\python.exe scripts/run_framework_benchmark.py --base-url http://127.0.0.1:18003

# L2 Golden Eval
.\.venv\Scripts\python.exe scripts/run_golden_eval.py --base-url http://127.0.0.1:18003
```

（Linux/macOS 等价：`PYTHONPATH=./src python scripts/run_framework_benchmark.py ...`）

输出：`reports/framework_smoke_latest.json/.md`、`reports/golden_eval_latest.json/.md`；每份报告包含 health 快照（模型 ID、Qdrant points 数）、逐 case 行明细（含 `run_id`、`latency_ms`、`ranked_chunk_ids`）与 limitations 声明。

## 6. 发布级评测尚缺什么（与 `docs/golden-eval-v0.1-dev.md` 一致）

1. 冻结 50+ case 的 CFPB Golden Set，双人独立标注；
2. 用人工复核的 `expected_chunk_ids` + 源 URL 替换文件名绑定；
3. 增加难度/语言/意图/来源权威度切片；
4. 增加人工 Citation Support 与 Answer Completeness 标注；
5. 在完全相同的语料快照上对比 V0.1 / V0.2 / V0.4。

**证据归档待补充**：`reports/` 目前被 `.gitignore` 排除，原始 JSON 报告仅存在于本地运行环境。建议将发布级评测的冻结报告与语料清单提交至 `docs/reports/`（或 GitHub Release 附件），使本文与 `docs/benchmark-smoke-v0.1.md`、`docs/golden-eval-v0.1-dev.md` 中的每个数字可点击溯源。
