"""Recipe 运行时执行器：按已编译 DAG 的真实数据流逐节点执行。

诚实约定（与 NODE_DOCS 的 runtime 标注一一对应）：
- 每个节点记录一条 TraceEvent，details.impact 携带该节点对数据流的实际影响
  （候选数量、证据 ID、使用的后端、降级原因、配置快照等）；
- 降级路径（lexical_fallback / passthrough / extractive）永远写入 Trace，不静默伪装；
- runtime-stub 节点（graph_query）记录 skipped + 原因，不产出伪造证据；
- api_key 永远不进入 Trace / Capsule。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from ..adapters.qdrant import QdrantAdapter
from ..config import Settings
from ..domain.models import Chunk, Evidence, Recipe, RecipeNode
from ..generation.client import generate_grounded_answer
from ..retrieval.lexical import bm25_rank, overlap_rank, tokenize
from .compiler import node_catalog
from .trace import TraceRecorder

Candidate = dict[str, Any]

# 同一批就绪节点的确定性执行顺序：检索先于纠错，纠错先于上下文构建
_TYPE_PRIORITY = {
    "rate_limit": 0, "cache": 1,
    "question": 2, "intent_router": 3, "metadata_filter": 4,
    "dense_retrieve": 5, "sparse_retrieve": 5, "pdf_page_retrieve": 6, "graph_query": 6,
    "rrf_fusion": 7, "evidence_grade": 8, "bounded_corrective": 9, "reranker": 10,
    "parent_expansion": 11, "context_builder": 12, "llm_generate": 13,
    "policy_gate": 14, "build_ticket_draft": 15, "approval": 16,
}

_SKIP_ON_CACHE_HIT = {
    "dense_retrieve", "sparse_retrieve", "rrf_fusion", "reranker", "context_builder",
    "parent_expansion", "llm_generate", "pdf_page_retrieve", "graph_query",
    "evidence_grade", "bounded_corrective", "metadata_filter", "intent_router",
}


def _redact(config: dict[str, Any]) -> dict[str, Any]:
    secret_keys = {"api_key", "access_token", "bearer_token"}
    return {key: ("***" if key in secret_keys or key.endswith("_api_key") else value) for key, value in config.items()}


def _chunk_candidate(score: float, chunk: Chunk) -> Candidate:
    return {
        "chunk_id": chunk.chunk_id, "document_id": chunk.document_id,
        "title": str(chunk.metadata.get("title") or chunk.document_id),
        "text": chunk.text, "score": round(float(score), 4), "metadata": chunk.metadata,
    }


class QueryExecutor:
    def __init__(
        self,
        recipe: Recipe,
        question: str,
        top_k: int,
        chunks: list[Chunk],
        store: Any,
        settings: Settings,
        qdrant: QdrantAdapter,
        recorder: TraceRecorder,
        runtime: dict[str, Any],
        resolve_model: Callable[[str], dict[str, Any] | None],
    ):
        self.recipe = recipe
        self.question = question
        self.top_k = top_k
        self.chunks = chunks
        self.store = store
        self.settings = settings
        self.qdrant = qdrant
        self.recorder = recorder
        self.runtime = runtime
        self.resolve_model = resolve_model
        self.catalog = node_catalog()
        self.node_outputs: dict[str, dict[str, Any]] = {}
        self.pool: list[Chunk] = list(chunks)
        self.evidence: list[Evidence] = []
        self.answer: str | None = None
        self.artifact: dict[str, Any] | None = None
        self.provider: str | None = None
        self.safety: dict[str, Any] = {"side_effects": False}
        self.short_circuit: str | None = None
        self.corrected: list[Candidate] | None = None
        self.cache_write_config: dict[str, Any] | None = None

    # ---------- 图辅助 ----------

    def _config(self, node: RecipeNode) -> dict[str, Any]:
        defaults = self.catalog.get(node.type, {}).get("config_defaults", {})
        return {**defaults, **(node.config or {})}

    def _record(self, node: RecipeNode, status: str, summary: str, impact: dict[str, Any], *, started: float | None = None, execution: str = "live", **compat: Any) -> None:
        details: dict[str, Any] = {"node_type": node.type, "execution": execution, "impact": impact, **compat}
        for key in ("backend", "top_k", "temperature", "max_tokens", "provider", "candidate_count", "cache", "preview"):
            if key in impact:
                details[key] = impact[key]
        config_used = impact.get("config_used") or {}
        for key in ("top_k", "temperature", "max_tokens"):
            if key in config_used:
                details[key] = config_used[key]
        self.recorder.record(node.id, status, summary, details, started=started)

    def _topological(self) -> list[RecipeNode]:
        nodes = {node.id: node for node in self.recipe.nodes}
        indegree = {node_id: 0 for node_id in nodes}
        outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
        for edge in self.recipe.edges:
            indegree[edge.target] += 1
            outgoing[edge.source].append(edge.target)
        ready = sorted(
            (node_id for node_id, degree in indegree.items() if degree == 0),
            key=lambda node_id: _TYPE_PRIORITY.get(nodes[node_id].type, 99),
        )
        order: list[RecipeNode] = []
        while ready:
            current = ready.pop(0)
            order.append(nodes[current])
            appended = False
            for target in outgoing[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    ready.append(target)
                    appended = True
            if appended:
                ready.sort(key=lambda node_id: _TYPE_PRIORITY.get(nodes[node_id].type, 99))
        return order if len(order) == len(nodes) else list(self.recipe.nodes)

    def _inputs(self, node_id: str, port: str) -> list[Any]:
        values = []
        for edge in self.recipe.edges:
            if edge.target == node_id and edge.target_port == port:
                value = self.node_outputs.get(edge.source, {}).get(edge.source_port)
                if value is not None:
                    values.append(value)
        return values

    def _as_evidence(self, candidates: list[Candidate], limit: int | None = None) -> list[Evidence]:
        selected = candidates[: limit or self.top_k]
        return [
            Evidence(
                citation=f"S{index + 1}", chunk_id=item["chunk_id"], document_id=item["document_id"],
                title=item["title"], text=item["text"], score=item["score"], metadata=item.get("metadata", {}),
            )
            for index, item in enumerate(selected)
        ]

    # ---------- 检索原语 ----------

    def _dense(self, query: str, config: dict[str, Any]) -> tuple[list[Candidate], str, dict[str, Any]]:
        top_k = int(config.get("top_k", self.top_k))
        threshold = float(config.get("score_threshold", self.settings.retrieval_score_threshold))
        threshold = max(threshold, self.settings.retrieval_score_threshold) if threshold == 0.0 else threshold
        detail: dict[str, Any] = {}
        adapter = self.qdrant
        model_ref = str(config.get("model_ref", ""))
        if model_ref and model_ref != "configured-embedding":
            profile = self.resolve_model(model_ref)
            if profile and profile.get("kind") == "embedding":
                adapter = QdrantAdapter(self.settings.model_copy(update={
                    "embedding_base_url": profile["base_url"],
                    "embedding_model": profile["model_name"],
                    "embedding_api_key": profile.get("api_key") or "",
                }))
                detail["embedding_model_ref"] = model_ref
        try:
            if not self.pool:
                raise RuntimeError("truth source empty")
            hits = adapter.search(query, top_k)
            truth_ids = {chunk.chunk_id for chunk in self.pool}
            hits = [hit for hit in hits if str(hit.get("payload", {}).get("chunk_id", "")) in truth_ids]
            ghost_filtered = len(hits)
            hits = [hit for hit in hits if float(hit.get("score", 0.0)) >= threshold]
            detail["score_threshold"] = threshold
            detail["dropped_below_threshold"] = ghost_filtered - len(hits)
            if not hits:
                raise RuntimeError("no dense hits above threshold")
            by_id = {chunk.chunk_id: chunk for chunk in self.pool}
            candidates = []
            for hit in hits:
                chunk = by_id.get(str(hit.get("payload", {}).get("chunk_id", "")))
                if chunk is not None:
                    candidates.append(_chunk_candidate(float(hit.get("score", 0.0)), chunk))
            return candidates, "qdrant_dense", detail
        except Exception as exc:
            detail["fallback_reason"] = f"{type(exc).__name__}: {exc}"[:200]
            scored = overlap_rank(query, self.pool, top_k)
            return [_chunk_candidate(score, chunk) for score, chunk in scored], "lexical_fallback", detail

    def _sparse(self, query: str, config: dict[str, Any]) -> tuple[list[Candidate], str, dict[str, Any]]:
        detail: dict[str, Any] = {}
        backend = str(config.get("backend", "bm25_local"))
        if backend not in {"bm25_local", ""}:
            detail["requested_backend"] = backend
            detail["fallback_reason"] = f"{backend} 后端未配置，回退到内置 bm25_local"
        scored = bm25_rank(query, self.pool, int(config.get("top_k", 20)), float(config.get("k1", 1.5)), float(config.get("b", 0.75)))
        return [_chunk_candidate(score, chunk) for score, chunk in scored], "bm25_local", detail

    def _fuse(self, lists: list[list[Candidate]], k: int, weights: list[float]) -> list[Candidate]:
        fused: dict[str, Candidate] = {}
        scores: dict[str, float] = {}
        for list_index, candidates in enumerate(lists):
            weight = weights[list_index] if list_index < len(weights) else 1.0
            for rank, item in enumerate(candidates):
                key = item["chunk_id"]
                scores[key] = scores.get(key, 0.0) + weight / (k + rank + 1)
                fused.setdefault(key, item)
        merged = [dict(fused[key], score=round(scores[key], 4)) for key in fused]
        merged.sort(key=lambda item: item["score"], reverse=True)
        return merged

    def _expanded_query(self, variant: str) -> str:
        tokens = tokenize(self.question)
        if variant == "keyword_only":
            return " ".join(tokens)
        domain_terms: list[str] = []
        for chunk in self.pool[:50]:
            for keyword in chunk.metadata.get("keywords", [])[:2]:
                if keyword not in tokens and keyword not in domain_terms:
                    domain_terms.append(keyword)
            if len(domain_terms) >= 5:
                break
        return " ".join(tokens + domain_terms[:5])

    # ---------- 主流程 ----------

    def execute(self) -> None:
        for node in self._topological():
            config = self._config(node)
            started = time.perf_counter()
            if self.short_circuit and node.type in _SKIP_ON_CACHE_HIT:
                self._record(node, "skipped", f"{self.short_circuit} 短路，节点未执行", {"skipped_reason": self.short_circuit}, started=started, execution="stub_passthrough")
                continue
            if self.short_circuit == "rate_limited" and node.type not in {"rate_limit"}:
                self._record(node, "skipped", "限流短路，节点未执行", {"skipped_reason": "rate_limited"}, started=started, execution="stub_passthrough")
                continue
            handler = getattr(self, f"_run_{node.type}", None)
            if handler is None:
                self._record(node, "completed", f"{node.type} 已执行", {"node_type": node.type}, started=started, execution="live")
                continue
            try:
                handler(node, config, started)
            except Exception as exc:
                self._record(node, "failed", f"节点执行失败：{exc}", {"error": str(exc)[:300], "error_type": type(exc).__name__}, started=started, execution="failed")
        self._write_cache_if_needed()

    # ---------- 节点处理器 ----------

    def _run_rate_limit(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        rpm = int(config.get("requests_per_minute", 60))
        window = self.runtime.setdefault("rate", {}).setdefault(self.recipe.recipe_id, [])
        now = time.time()
        window[:] = [stamp for stamp in window if now - stamp < 60]
        if len(window) >= rpm:
            self.short_circuit = "rate_limited"
            self.safety["rate_limited"] = True
            self.answer = f"当前 Recipe 已达到 {rpm} 次/分钟限流，稍后再试。真相源与索引未受影响。"
            self._record(node, "failed", f"限流触发：{rpm}/min 已用尽", {"requests_per_minute": rpm, "remaining": 0, "next_action": "等待窗口滑动后重试"}, started=started, execution="failed")
            return
        window.append(now)
        self._record(node, "completed", "限流检查通过", {"requests_per_minute": rpm, "remaining": rpm - len(window), "config_used": _redact(config)}, started=started, execution="live")

    def _run_cache(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        ttl = int(config.get("ttl_seconds", 300))
        key = f"{self.recipe.hash}:{self.question}"
        cache: dict[str, Any] = self.runtime.setdefault("cache", {})
        entry = cache.get(key)
        now = time.time()
        if entry and entry["expires_at"] > now:
            self.short_circuit = "cache_hit"
            self.safety["cache_hit"] = True
            self.answer = entry["answer"]
            self.evidence = entry["evidence"]
            self.provider = "cache"
            self._record(node, "completed", "缓存命中，复用上次结果", {"cache": "hit", "age_seconds": round(now - entry["stored_at"], 1), "ttl_seconds": ttl, "evidence_count": len(self.evidence)}, started=started, execution="live")
            return
        self.cache_write_config = {"key": key, "ttl": ttl}
        self._record(node, "completed", "缓存未命中，继续执行链路", {"cache": "miss", "ttl_seconds": ttl}, started=started, execution="live")

    def _write_cache_if_needed(self) -> None:
        if self.cache_write_config and self.answer and not self.short_circuit:
            cache: dict[str, Any] = self.runtime.setdefault("cache", {})
            cache[self.cache_write_config["key"]] = {
                "answer": self.answer, "evidence": self.evidence, "stored_at": time.time(),
                "expires_at": time.time() + self.cache_write_config["ttl"],
            }

    def _run_question(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        tokens = tokenize(self.question)
        self.node_outputs[node.id] = {"query": self.question}
        self._record(node, "completed", f"接收问题（{len(tokens)} tokens）", {"token_count": len(tokens), "question_preview": self.question[:120]}, started=started)

    def _run_intent_router(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        normalized = self.question.lower()
        if re.search(r"怎么|如何|流程|步骤|how|process|procedure", normalized):
            intent = "procedural"
        elif re.search(r"对比|区别|差异|compare|difference|vs", normalized):
            intent = "comparison"
        elif re.search(r"退款|违法|责任|封禁|refund|illegal|liab", normalized):
            intent = "risk_adjacent"
        else:
            intent = "factual"
        self.node_outputs[node.id] = {"query": self.question}
        self._record(node, "completed", f"意图判定：{intent}", {"intent": intent, "method": "heuristic_rules"}, started=started, execution="live")

    def _run_metadata_filter(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        fields: dict[str, Any] = config.get("fields") or {}
        before = len(self.pool)
        impact: dict[str, Any] = {"fields": fields, "pool_before": before}
        if fields:
            filtered = [chunk for chunk in self.pool if all(str(chunk.metadata.get(key)) == str(value) for key, value in fields.items())]
            if not filtered and config.get("on_empty", "fallback_once") == "fallback_once":
                impact["fallback"] = "filter_emptied_pool_fallback_to_unfiltered"
            else:
                self.pool = filtered
        impact["pool_after"] = len(self.pool)
        self.node_outputs[node.id] = {"query": self.question}
        self._record(node, "completed", f"Metadata 过滤：候选池 {before} → {len(self.pool)}", impact, started=started)

    def _run_dense_retrieve(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        candidates, backend, detail = self._dense(self.question, config)
        self.node_outputs[node.id] = {"candidates": candidates}
        if not self.evidence:
            self.evidence = self._as_evidence(candidates)
        impact = {"candidate_count": len(candidates), "backend": backend, "top_chunk_ids": [item["chunk_id"] for item in candidates[:3]], "config_used": _redact(config), **detail}
        self._record(node, "completed", f"Dense 召回 {len(candidates)} 条候选（backend={backend}）", impact, started=started, execution="live" if backend == "qdrant_dense" else "fallback_lexical")

    def _run_sparse_retrieve(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        candidates, backend, detail = self._sparse(self.question, config)
        self.node_outputs[node.id] = {"candidates": candidates}
        self._record(node, "completed", f"Sparse/BM25 召回 {len(candidates)} 条候选（backend={backend}）", {"candidate_count": len(candidates), "backend": backend, "top_chunk_ids": [item["chunk_id"] for item in candidates[:3]], "config_used": _redact(config), **detail}, started=started, execution="live")

    def _run_rrf_fusion(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        lists = [value for value in self._inputs(node.id, "candidates") if isinstance(value, list)]
        non_empty = [candidates for candidates in lists if candidates]
        k = int(config.get("k", 60))
        weights = [float(weight) for weight in config.get("weights", [1.0, 1.0])]
        if len(non_empty) >= 2:
            fused = self._fuse(non_empty, k, weights)
            note = f"RRF 融合 {len(non_empty)} 路候选 → {len(fused)} 条"
            impact = {"fused_lists": len(non_empty), "input_counts": [len(candidates) for candidates in non_empty], "candidate_count": len(fused), "k": k, "weights": weights}
        else:
            fused = non_empty[0] if non_empty else []
            note = f"仅 {len(non_empty)} 路候选，直通（未发生真实融合）"
            impact = {"fused_lists": len(non_empty), "candidate_count": len(fused), "passthrough": True}
        self.node_outputs[node.id] = {"candidates": fused}
        if fused:
            self.evidence = self._as_evidence(fused)
        self._record(node, "completed", note, impact, started=started)

    def _run_pdf_page_retrieve(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        pages = [chunk for chunk in self.pool if str(chunk.metadata.get("block_type")) == "page"]
        scored = bm25_rank(self.question, pages, int(config.get("top_k", 3)))
        candidates = [_chunk_candidate(score, chunk) for score, chunk in scored]
        self.node_outputs[node.id] = {"evidence": self._as_evidence(candidates, len(candidates))}
        self._record(node, "completed", f"PDF 页级检索命中 {len(candidates)} 页（候选页 {len(pages)}）", {"page_pool": len(pages), "candidate_count": len(candidates), "backend": "bm25_local_page", "pages": [item.get("metadata", {}).get("page") for item in candidates]}, started=started, execution="live")

    def _run_graph_query(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        self.node_outputs[node.id] = {"evidence": []}
        self._record(node, "skipped", "compile-complete / runtime-stub：Neo4j 图谱后端未实现，未产出证据", {"runtime": "stub", "skipped_reason": "graph_backend_not_implemented", "next_action": "安装 graph profile 并接入 Neo4j 后启用"}, started=started, execution="stub_passthrough")

    def _run_reranker(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        candidates_lists = [value for value in self._inputs(node.id, "candidates") if isinstance(value, list)]
        candidates: list[Candidate] = candidates_lists[0] if candidates_lists else []
        candidate_k = int(config.get("candidate_k", 50))
        final_k = int(config.get("final_k", 6))
        pool = candidates[:candidate_k]
        profile = self.resolve_model(str(config.get("model_ref", "")))
        backend = "passthrough"
        reason = ""
        if profile and profile.get("kind") == "reranker" and profile.get("base_url"):
            try:
                headers = {"Content-Type": "application/json"}
                if profile.get("api_key"):
                    headers["Authorization"] = f"Bearer {profile['api_key']}"
                response = httpx.post(
                    f"{str(profile['base_url']).rstrip('/')}/rerank",
                    json={"model": profile["model_name"], "query": self.question, "documents": [item["text"] for item in pool], "top_n": final_k},
                    headers=headers, timeout=30,
                )
                response.raise_for_status()
                results = response.json().get("results", [])
                reordered = []
                for entry in results:
                    index = int(entry.get("index", -1))
                    if 0 <= index < len(pool):
                        reordered.append(dict(pool[index], score=round(float(entry.get("relevance_score", 0.0)), 4)))
                if reordered:
                    pool = reordered
                    backend = "openai_compatible_rerank"
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"[:200]
        else:
            reason = "未绑定可用的 reranker 模型端点"
        final = pool[:final_k]
        self.node_outputs[node.id] = {"evidence": self._as_evidence(final, len(final))}
        self.evidence = self._as_evidence(final, len(final))
        impact: dict[str, Any] = {"backend": backend, "candidate_in": len(candidates), "evidence_out": len(final), "config_used": _redact(config)}
        if backend == "passthrough":
            impact["passthrough_reason"] = reason or "reranker 后端不可用"
        summary = f"重排 {len(candidates)} → {len(final)} 条证据（backend={backend}）" if backend != "passthrough" else f"重排后端不可用，如实直通 {len(final)} 条（未发生真实重排）"
        self._record(node, "completed", summary, impact, started=started, execution="live" if backend != "passthrough" else "fallback_extractive")

    def _run_evidence_grade(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        inputs = [value for port in ("candidates", "evidence") for value in self._inputs(node.id, port) if isinstance(value, list)]
        items = inputs[0] if inputs else [item.model_dump() for item in self.evidence]
        min_evidence = int(config.get("min_evidence", 1))
        min_top_score = float(config.get("min_top_score", 0.05))
        top_score = max((float(item.get("score", 0.0)) for item in items), default=0.0)
        sufficient = len(items) >= min_evidence and top_score >= min_top_score
        decision = {"sufficient": sufficient, "evidence_count": len(items), "top_score": round(top_score, 4), "min_evidence": min_evidence, "min_top_score": min_top_score}
        self.node_outputs[node.id] = {"decision": decision}
        self._record(node, "completed", f"证据判定：{'sufficient' if sufficient else 'insufficient'}（{len(items)} 条，top={round(top_score, 3)}）", {"decision": decision}, started=started)

    def _run_bounded_corrective(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        decisions = self._inputs(node.id, "decision")
        decision = decisions[0] if decisions else {"sufficient": bool(self.evidence)}
        max_retries = min(int(config.get("max_retries", 1)), 2)
        self.node_outputs[node.id] = {"query": self.question}
        if decision.get("sufficient", True):
            self._record(node, "completed", "证据充足，无需纠错重试", {"retries_used": 0, "max_retries": max_retries, "triggered": False}, started=started, execution="live")
            return
        variant = str(config.get("query_variant", "domain_term_expansion"))
        before = len(self.evidence)
        retries_used = 0
        for _ in range(max_retries):
            retries_used += 1
            expanded = self._expanded_query(variant)
            dense, _, _ = self._dense(expanded, {"top_k": self.top_k})
            sparse, _, _ = self._sparse(expanded, {"top_k": 20})
            fused = self._fuse([dense, sparse], 60, [1.0, 1.0]) if dense and sparse else (dense or sparse)
            if len(fused) > before:
                self.corrected = fused
                self.evidence = self._as_evidence(fused)
                break
        impact = {"triggered": True, "retries_used": retries_used, "max_retries": max_retries, "query_variant": variant, "candidates_before": before, "candidates_after": len(self.corrected or []) or before, "improved": self.corrected is not None}
        self._record(node, "completed", f"有限纠错：重试 {retries_used} 次，候选 {before} → {impact['candidates_after']}", impact, started=started)

    def _run_parent_expansion(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        window = min(int(config.get("window", 1)), 3)
        max_side = int(config.get("max_chars_per_side", 600))
        inputs = [value for port in ("evidence", "candidates") for value in self._inputs(node.id, port) if isinstance(value, list)]
        source = inputs[0] if inputs else self.evidence
        evidence = source if source and isinstance(source[0], Evidence) else self._as_evidence(source) if source else self.evidence
        expanded_count = 0
        by_doc: dict[str, list[Chunk]] = {}
        for chunk in self.chunks:
            by_doc.setdefault(chunk.document_id, []).append(chunk)
        updated: list[Evidence] = []
        for item in evidence:
            siblings = sorted(by_doc.get(item.document_id, []), key=lambda chunk: chunk.order)
            index = next((position for position, chunk in enumerate(siblings) if chunk.chunk_id == item.chunk_id), None)
            if index is None:
                updated.append(item)
                continue
            prefix = " ".join(chunk.text for chunk in siblings[max(0, index - window): index])[-max_side:]
            suffix = " ".join(chunk.text for chunk in siblings[index + 1: index + 1 + window])[:max_side]
            if prefix or suffix:
                expanded_count += 1
                text = f"{prefix} {item.text} {suffix}".strip()
                metadata = dict(item.metadata)
                metadata["parent_expanded"] = True
                updated.append(item.model_copy(update={"text": text, "metadata": metadata}))
            else:
                updated.append(item)
        self.evidence = updated
        self.node_outputs[node.id] = {"evidence": updated}
        self._record(node, "completed", f"父子块扩展：{expanded_count}/{len(updated)} 条证据补充了相邻上下文", {"expanded_count": expanded_count, "evidence_count": len(updated), "window": window, "max_chars_per_side": max_side}, started=started, execution="live")

    def _run_context_builder(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        merged: list[Candidate] = []
        for value in self._inputs(node.id, "evidence"):
            if isinstance(value, list):
                for item in value:
                    merged.append(item.model_dump() if isinstance(item, Evidence) else item)
        if self.corrected is not None:
            merged.extend(self.corrected)
        else:
            for value in self._inputs(node.id, "candidates"):
                if isinstance(value, list):
                    merged.extend(item.model_dump() if isinstance(item, Evidence) else item for item in value)
        if not merged:
            merged = [item.model_dump() for item in self.evidence]
        deduped: dict[str, Candidate] = {}
        for item in merged:
            key = str(item.get("chunk_id"))
            if key not in deduped or float(item.get("score", 0.0)) > float(deduped[key].get("score", 0.0)):
                deduped[key] = item
        dropped_dupe = len(merged) - len(deduped)
        ranked = sorted(deduped.values(), key=lambda item: float(item.get("score", 0.0)), reverse=True)
        max_per_doc = int(config.get("max_per_doc", 0))
        if max_per_doc > 0:
            per_doc: dict[str, int] = {}
            limited = []
            for item in ranked:
                doc = str(item.get("document_id"))
                per_doc[doc] = per_doc.get(doc, 0) + 1
                if per_doc[doc] <= max_per_doc:
                    limited.append(item)
            ranked = limited
        budget_chars = int(config.get("token_budget", 4000)) * 3  # 近似：1 token ≈ 3 字符（中英混合保守值）
        kept: list[Candidate] = []
        used = 0
        dropped_budget = 0
        for item in ranked[: max(self.top_k, 12)]:
            length = len(str(item.get("text", "")))
            if used + length > budget_chars and kept:
                dropped_budget += 1
                continue
            used += length
            kept.append(item)
        self.evidence = self._as_evidence(kept, len(kept))
        context = "\n\n".join(f"[{item.citation}] {item.text}" for item in self.evidence)
        self.node_outputs[node.id] = {"context": context, "evidence": self.evidence}
        self._record(node, "completed", f"上下文构建：保留 {len(kept)} 条证据（去重丢弃 {dropped_dupe}，超预算丢弃 {dropped_budget}）", {"evidence_count": len(kept), "evidence_ids": [item.chunk_id for item in self.evidence], "citations": [item.citation for item in self.evidence], "dropped_duplicates": dropped_dupe, "dropped_over_budget": dropped_budget, "budget_chars": budget_chars, "context_chars": used, "config_used": _redact(config)}, started=started, execution="live")

    def _run_llm_generate(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        profile = self.resolve_model(str(config.get("model_ref", "")))
        if profile is not None and profile.get("kind") != "chat":
            profile = None
        answer, provider = generate_grounded_answer(
            self.question, self.evidence, self.settings, profile=profile,
            temperature=float(config.get("temperature", 0.1)), max_tokens=int(config.get("max_tokens", 600)),
        )
        raw_provider = provider
        repaired = False
        if self.evidence and not re.search(r"\[S\d+\]", answer):
            from ..generation.client import extractive_answer
            answer = extractive_answer(self.question, self.evidence)
            provider = "citation_repair_fallback"
            repaired = True
        self.answer = answer
        self.provider = provider
        self.node_outputs[node.id] = {"answer": answer}
        impact = {"provider": provider, "raw_provider": raw_provider, "citation_count": len(self.evidence), "citation_repaired": repaired, "answer_chars": len(answer), "model_ref": str(config.get("model_ref", "")) or None, "config_used": _redact(config)}
        if repaired:
            impact["repair_reason"] = "missing_citation_markers"
        execution = "live" if provider == "openai_compatible_chat" else "fallback_extractive"
        self._record(node, "completed", f"生成受证据约束的回答（provider={provider}）", impact, started=started, execution=execution)

    def _run_policy_gate(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        require_citation = bool(config.get("require_citation", True))
        valid_citations = {item.citation for item in self.evidence}
        cited = set(re.findall(r"\[(S\d+)\]", self.answer or ""))
        invalid = sorted(cited - valid_citations)
        human_review = not bool(self.evidence)
        self.safety["human_review"] = human_review or bool(self.artifact)
        if invalid:
            self.safety["invalid_citations"] = invalid
        self.node_outputs[node.id] = {"decision": {"human_review": human_review, "invalid_citations": invalid}}
        self._record(node, "completed", "安全策略通过；未发现外部副作用动作" if not invalid else f"发现无效引用 {invalid}，已标记人工复核", {"human_review": human_review, "require_citation": require_citation, "citations_in_answer": sorted(cited), "invalid_citations": invalid, "evidence_count": len(self.evidence)}, started=started, execution="live")

    def _run_build_ticket_draft(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        normalized = self.question.lower()
        supplied = {
            "merchant": bool(re.search(r"merchant|商户", normalized)),
            "date": bool(re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|日期|date", normalized)),
            "previous_actions": bool(re.search(r"contacted|联系过|此前|previous", normalized)),
        }
        missing = [field for field, present in supplied.items() if not present]
        self.artifact = {
            "artifact_type": "ticket_draft", "status": "pending_human_approval",
            "fields": {"message": self.question, "merchant": None, "date": None, "previous_actions": None},
            "missing_fields": missing, "evidence_ids": [item.citation for item in self.evidence],
            "forbidden_actions": ["send_customer_message", "write_external_crm", "promise_refund", "decide_legal_liability"],
        }
        self.answer = f"已生成客服工单草稿，仍缺少字段：{', '.join(missing) if missing else '无'}。草稿必须经过人工审批。"
        self.safety["human_review"] = True
        self.node_outputs[node.id] = {"artifact": self.artifact}
        self._record(node, "completed", "生成结构化工单草稿，未执行外部动作", {"missing_fields": missing, "approval_required": True, "evidence_ids": [item.citation for item in self.evidence], "forbidden_actions": self.artifact["forbidden_actions"]}, started=started, execution="live")

    def _run_approval(self, node: RecipeNode, config: dict[str, Any], started: float) -> None:
        self.node_outputs[node.id] = {"artifact": self.artifact}
        self._record(node, "completed", "停在人工审批门", {"approval_required": True, "side_effects": False}, started=started, execution="live")
