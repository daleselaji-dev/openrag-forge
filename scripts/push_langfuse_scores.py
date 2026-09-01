"""Push deterministic local Eval results to Langfuse trace scores.

This is deliberately an adapter, not the source of truth for release gates:
the local JSON/Golden Eval remains reproducible and CI-owned; Langfuse is the
long-lived UI for slicing traces, replaying failures and tracking score trends.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from typing import Any

import httpx


def _auth_header(public_key: str, secret_key: str) -> str:
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    return f"Basic {token}"


def _score_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get("rows") or []
    scores: list[dict[str, Any]] = []
    for row in rows:
        # OpenRAG's business run_id is not Langfuse's OTel trace_id. New
        # benchmark rows persist the latter explicitly; the older fallbacks
        # keep dry-run compatibility with historical reports.
        trace_id = row.get("otel_trace_id") or row.get("trace_id") or row.get("run_id")
        if not trace_id:
            continue
        if "citation_present" in row:
            scores.append({"traceId": trace_id, "name": "citation_presence", "value": float(bool(row["citation_present"])), "dataType": "NUMERIC"})
        if "evidence_count" in row:
            scores.append({"traceId": trace_id, "name": "evidence_presence", "value": float(bool(row["evidence_count"])), "dataType": "NUMERIC"})
        if "must_answer" in row and "refused" in row:
            expected_refusal = not bool(row["must_answer"])
            scores.append({"traceId": trace_id, "name": "refusal_correctness", "value": float(bool(row["refused"]) == expected_refusal), "dataType": "NUMERIC"})
        if row.get("latency_ms") is not None:
            scores.append({"traceId": trace_id, "name": "latency_ms", "value": float(row["latency_ms"]), "dataType": "NUMERIC"})
    return scores


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="framework smoke or golden eval JSON report")
    parser.add_argument("--base-url", default=os.getenv("OPENRAG_LANGFUSE_BASE_URL", "http://localhost:3000"))
    parser.add_argument("--public-key", default=os.getenv("OPENRAG_LANGFUSE_PUBLIC_KEY", ""))
    parser.add_argument("--secret-key", default=os.getenv("OPENRAG_LANGFUSE_SECRET_KEY", ""))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.public_key or not args.secret_key:
        raise SystemExit("缺少 --public-key/--secret-key，或 OPENRAG_LANGFUSE_PUBLIC_KEY/SECRET_KEY")
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    scores = _score_rows(report)
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "scores": scores}, ensure_ascii=False, indent=2))
        return 0
    headers = {"Authorization": _auth_header(args.public_key, args.secret_key), "Content-Type": "application/json"}
    url = f"{args.base_url.rstrip('/')}/api/public/scores"
    sent = 0
    with httpx.Client(timeout=20) as client:
        for score in scores:
            response = client.post(url, headers=headers, json=score)
            response.raise_for_status()
            sent += 1
    print(json.dumps({"status": "completed", "sent": sent, "report": args.report}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
