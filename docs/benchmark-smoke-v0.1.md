# OpenRAG Forge framework smoke benchmark

运行命令：

```powershell
$env:PYTHONPATH = ".\\src"
.\.venv\Scripts\python.exe scripts/run_framework_benchmark.py --base-url http://127.0.0.1:18003
```

## Current run

| 项目 | 实测值 |
|---|---:|
| Recipe | `v0_1_dense` |
| Cases | 7 |
| Corpus | 1 local document / 9 Qdrant points |
| Chat model | `deepseek-r1-distill-qwen-7b` |
| Embedding model | `text-embedding-qwen3-embedding-0.6b` |
| Answerable evidence rate | 1.000 |
| Citation presence | 1.000 |
| Refusal correctness | 1.000 |
| Trace completeness | 1.000 |
| p50 end-to-end latency | 623 ms |
| p95 end-to-end latency | 9,619 ms |

Cases include English, Chinese, exact-term, out-of-domain, refund promise, legal conclusion and account-action requests.

## Same-set diagnostic before/after

The first run intentionally exposed two baseline failures: stopwords caused an out-of-domain question to retrieve an unrelated support chunk, and the local model sometimes omitted citations or missed English high-risk wording.

| Metric | Before fixes | After fixes |
|---|---:|---:|
| Answerable citation presence | 0.500 | 1.000 |
| Refusal correctness | 0.333 | 1.000 |
| Out-of-domain evidence | 1 unrelated hit | 0 hits |

The latency difference is not treated as a quality improvement claim because local model load varies between runs.

## What this proves

- The current framework can execute a fixed test set through the real API, Qdrant and LM Studio.
- It can detect a retrieval failure and a safety-routing failure, then verify the fixes on the same cases.
- Every row has a run ID and a complete `q → d → c → g → p` Trace.

## What this does not prove

This is a smoke benchmark, not an enterprise quality claim. It has one small local document and no labeled expected Chunk IDs, so it does not provide Recall@k, MRR, nDCG, answer completeness or cross-document generalization. Those require a frozen Golden Set with real source IDs and a larger corpus.

