# Golden Eval v0.1-dev

Command:

```powershell
$env:PYTHONPATH = ".\\src"
.\.venv\Scripts\python.exe scripts/run_golden_eval.py --base-url http://127.0.0.1:18003
```

This evaluator binds `expected_source_filenames` to actual Chunk IDs from the current truth source, then scores the returned ranked evidence. For a release dataset, reviewers should replace filename binding with manually reviewed `expected_chunk_ids` and freeze the corpus snapshot.

## Current measured run

| Metric | Value | 95% Wilson interval |
|---|---:|---:|
| Hit@k | 1.000 | 0.438–1.000 |
| Recall@k | 1.000 | — |
| MRR | 1.000 | — |
| nDCG | 1.000 | — |
| Citation validity | 1.000 | 0.438–1.000 |
| Citation completeness | 1.000 | 0.438–1.000 |
| Refusal correctness | 1.000 | 0.510–1.000 |
| Trace completeness | 1.000 | 0.646–1.000 |
| p50 end-to-end | 690 ms | — |
| p95 end-to-end | 8,050 ms | — |

The answerable slice contains three labeled cases; the refusal slice contains four. The corpus is still one local support document, so the intervals are intentionally wide and the result is not an enterprise deployment claim.

## Why this is more objective than Smoke Eval

- Retrieval is scored against evidence labels instead of only checking whether any evidence exists.
- Refusal cases are excluded from Recall/MRR/nDCG and evaluated in a separate safety slice.
- Citation validity checks citation indices against returned evidence.
- Citation completeness checks required fact terms against the answer and cited evidence.
- The report records corpus health, model IDs, recipe ID, run IDs, latency and per-case failures.
- A stale Qdrant point outside the SQLite truth source was observed and filtered; this is recorded as an index-lineage control, not hidden.

## Release requirements still missing

1. Freeze a 50+ case CFPB Golden Set with two independent reviewers.
2. Replace filename binding with reviewed expected Chunk IDs and source URLs.
3. Add difficulty, language, intent and source-authority slices.
4. Add human Citation Support and Answer Completeness labels.
5. Compare V0.1, V0.2 and V0.4 on exactly the same snapshot.

