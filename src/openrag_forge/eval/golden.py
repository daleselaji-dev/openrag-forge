from __future__ import annotations

import math
import re
from typing import Any

from pydantic import BaseModel, Field


class GoldenCase(BaseModel):
    case_id: str
    question: str
    must_answer: bool = True
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_source_filenames: list[str] = Field(default_factory=list)
    expected_terms: list[str] = Field(default_factory=list)
    intent: str = "unknown"
    risk_level: str = "normal"
    tags: list[str] = Field(default_factory=list)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return (round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3))


def _citations(answer: str) -> list[int]:
    return [int(value) for value in re.findall(r"\[S(\d+)\]", answer or "")]


def evaluate_case(case: GoldenCase, result: dict[str, Any], bound_chunk_ids: set[str]) -> dict[str, Any]:
    evidence = result.get("evidence", [])
    ranked_ids = [str(item.get("chunk_id", "")) for item in evidence]
    relevant = set(case.expected_chunk_ids) | bound_chunk_ids
    relevant_hits = relevant.intersection(ranked_ids)
    if case.must_answer:
        hit = bool(relevant_hits) if relevant else bool(evidence)
        recall = len(relevant_hits) / len(relevant) if relevant else (1.0 if hit else 0.0)
        first_rank = next((index + 1 for index, chunk_id in enumerate(ranked_ids) if chunk_id in relevant), None)
        mrr = 1 / first_rank if first_rank else 0.0
        dcg = sum(1 / math.log2(index + 2) for index, chunk_id in enumerate(ranked_ids) if chunk_id in relevant)
        ideal = sum(1 / math.log2(index + 2) for index in range(min(len(relevant), len(ranked_ids))))
        ndcg = dcg / ideal if ideal else (1.0 if not relevant else 0.0)
    else:
        hit, recall, mrr, ndcg = None, None, None, None
    citations = _citations(result.get("answer", ""))
    citation_validity = bool(citations) and all(1 <= citation <= len(evidence) for citation in citations) if case.must_answer else True
    cited_text = " ".join(evidence[index - 1].get("text", "") for index in citations if 1 <= index <= len(evidence))
    answer_text = result.get("answer", "")
    citation_completeness = all(term.lower() in answer_text.lower() or term.lower() in cited_text.lower() for term in case.expected_terms) if case.expected_terms and case.must_answer else True
    refused = bool(result.get("safety", {}).get("request_safety_gate")) or not evidence
    refusal_correct = refused == (not case.must_answer)
    trace_nodes = {event.get("node_id") for event in result.get("trace", [])}
    trace_complete = {"q", "d", "c", "g", "p"}.issubset(trace_nodes)
    return {"case_id": case.case_id, "hit_at_k": hit, "recall": round(recall, 3) if recall is not None else None, "mrr": round(mrr, 3) if mrr is not None else None, "ndcg": round(ndcg, 3) if ndcg is not None else None, "citation_validity": citation_validity, "citation_completeness": citation_completeness, "refusal_correct": refusal_correct, "refused": refused, "trace_complete": trace_complete, "evidence_count": len(evidence), "ranked_chunk_ids": ranked_ids, "run_id": result.get("run_id")}
