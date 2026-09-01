"""Run a labeled Golden Set against the real OpenRAG Forge API."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

import httpx

from openrag_forge.eval import GoldenCase, evaluate_case, wilson_interval


def load_cases(path: Path) -> list[GoldenCase]:
    return [GoldenCase.model_validate(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18003")
    parser.add_argument("--recipe-id", default="v0_1_dense")
    parser.add_argument("--dataset", default="packs/customer-support-cfpb/evals/golden_v0_1_dev.jsonl")
    parser.add_argument("--output", default="reports/golden_eval_latest.json")
    args = parser.parse_args()
    cases = load_cases(Path(args.dataset))
    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=120) as client:
        health = client.get("/api/v1/health").json()
        documents = client.get("/api/v1/knowledge-bases/default/documents").json().get("items", [])
        chunks_by_filename: dict[str, list[str]] = {}
        for document in documents:
            chunks = client.get(f"/api/v1/documents/{document['document_id']}/chunks").json().get("items", [])
            chunks_by_filename.setdefault(document["filename"], []).extend(item["chunk_id"] for item in chunks)
        rows = []
        latencies: list[float] = []
        for case in cases:
            bound = {chunk_id for filename in case.expected_source_filenames for chunk_id in chunks_by_filename.get(filename, [])}
            started = time.perf_counter()
            response = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": args.recipe_id, "question": case.question, "mode": "run"})
            latency = (time.perf_counter() - started) * 1000
            response.raise_for_status()
            latencies.append(latency)
            row = evaluate_case(case, response.json(), bound)
            result_payload = response.json()
            otel_trace_id = next((event.get("otel_trace_id") for event in result_payload.get("trace", []) if event.get("otel_trace_id")), None)
            row.update({"question": case.question, "intent": case.intent, "risk_level": case.risk_level, "latency_ms": round(latency, 2), "bound_chunk_ids": sorted(bound), "tags": case.tags, "otel_trace_id": otel_trace_id})
            rows.append(row)
    def mean(key: str, selector=lambda row: True) -> float:
        selected = [row for row in rows if selector(row) and row[key] is not None]
        return round(sum(float(row[key]) for row in selected) / max(1, len(selected)), 3)
    def rate(key: str, selector=lambda row: True) -> tuple[float, tuple[float, float]]:
        selected = [row for row in rows if selector(row)]
        successes = sum(bool(row[key]) for row in selected)
        return round(successes / max(1, len(selected)), 3), wilson_interval(successes, len(selected))
    answerable = lambda row: row["risk_level"] == "normal" and row["intent"] != "out_of_domain"
    refusal = lambda row: row["risk_level"] != "normal" or row["intent"] == "out_of_domain"
    report = {"benchmark": "openrag_golden_eval_v0.1_dev", "recipe_id": args.recipe_id, "dataset": str(Path(args.dataset)), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "health": health, "metrics": {"cases": len(rows), "hit_at_k": rate("hit_at_k", answerable), "recall": mean("recall", answerable), "mrr": mean("mrr", answerable), "ndcg": mean("ndcg", answerable), "citation_validity": rate("citation_validity", answerable), "citation_completeness": rate("citation_completeness", answerable), "refusal_correctness": rate("refusal_correct", refusal), "trace_complete_rate": rate("trace_complete"), "p50_latency_ms": round(statistics.median(latencies), 2) if latencies else 0, "p95_latency_ms": round(sorted(latencies)[max(0, int(len(latencies) * .95) - 1)], 2) if latencies else 0}, "rows": rows, "limitations": ["This dev Golden Set is bound to the currently loaded local snapshot; freeze the corpus and review labels before release.", "Confidence intervals are Wilson intervals for case-level binary metrics, not a claim of population-level generalization.", "Answer support is deterministic term/citation coverage; human review is still required for final Citation Support."]}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".md").write_text("# " + report["benchmark"] + "\n\n" + "\n".join(f"- `{key}`: `{value}`" for key, value in report["metrics"].items()) + "\n", encoding="utf-8")
    print(json.dumps({"status": "completed", "output": str(output), "metrics": report["metrics"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
