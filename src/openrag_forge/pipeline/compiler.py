from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict, deque
from typing import Any

from ..domain.models import Recipe, RecipeEdge, RecipeNode, utc_now


class CompileError(ValueError):
    pass


NODE_CATALOG: dict[str, dict[str, Any]] = {
    "parse_route": {"inputs": ["raw"], "outputs": ["blocks"], "group": "ingest"},
    "native_parser": {"inputs": ["raw"], "outputs": ["blocks"], "group": "ingest"},
    "pdf_parser": {"inputs": ["raw"], "outputs": ["blocks"], "group": "ingest"},
    "office_parser": {"inputs": ["raw"], "outputs": ["blocks"], "group": "ingest"},
    "tabular_parser": {"inputs": ["raw"], "outputs": ["blocks"], "group": "ingest"},
    "chunker": {"inputs": ["blocks"], "outputs": ["chunks"], "group": "ingest"},
    "metadata_enricher": {"inputs": ["chunks"], "outputs": ["chunks"], "group": "ingest"},
    "embed_index": {"inputs": ["chunks"], "outputs": ["indexed"], "group": "index"},
    "question": {"inputs": ["query"], "outputs": ["query"], "group": "query"},
    "intent_router": {"inputs": ["query"], "outputs": ["query"], "group": "query"},
    "metadata_filter": {"inputs": ["query"], "outputs": ["query"], "group": "query"},
    "dense_retrieve": {"inputs": ["query"], "outputs": ["candidates"], "group": "retrieve"},
    "sparse_retrieve": {"inputs": ["query"], "outputs": ["candidates"], "group": "retrieve"},
    "rrf_fusion": {"inputs": ["candidates", "candidates"], "outputs": ["candidates"], "group": "retrieve"},
    "reranker": {"inputs": ["candidates", "query"], "outputs": ["evidence"], "group": "retrieve"},
    "context_builder": {"inputs": ["candidates", "evidence"], "outputs": ["context", "evidence"], "group": "generate"},
    "parent_expansion": {"inputs": ["evidence"], "outputs": ["evidence"], "group": "generate"},
    "llm_generate": {"inputs": ["query", "context"], "outputs": ["answer"], "group": "generate"},
    "evidence_grade": {"inputs": ["candidates", "evidence"], "outputs": ["decision"], "group": "policy"},
    "policy_gate": {"inputs": ["answer", "evidence"], "outputs": ["decision"], "group": "policy"},
    "bounded_corrective": {"inputs": ["query", "decision"], "outputs": ["query"], "group": "retrieve", "bounded": True},
    "graph_query": {"inputs": ["query"], "outputs": ["evidence"], "group": "optional"},
    "pdf_page_retrieve": {"inputs": ["query"], "outputs": ["evidence"], "group": "optional"},
    "cache": {"inputs": ["query"], "outputs": ["query"], "group": "operations"},
    "rate_limit": {"inputs": ["query"], "outputs": ["query"], "group": "operations"},
    "approval": {"inputs": ["artifact"], "outputs": ["artifact"], "group": "agent"},
    "build_ticket_draft": {"inputs": ["query", "candidates", "evidence"], "outputs": ["artifact"], "group": "agent"},
}


def node_catalog() -> dict[str, dict[str, Any]]:
    return NODE_CATALOG.copy()


def _canonical(recipe: Recipe) -> bytes:
    payload = recipe.model_dump(mode="json", exclude={"hash", "status", "created_at"})
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def compile_recipe(recipe: Recipe) -> Recipe:
    node_ids = {node.id for node in recipe.nodes}
    if len(node_ids) != len(recipe.nodes):
        raise CompileError("Recipe 节点 ID 必须唯一")
    if not node_ids:
        raise CompileError("Recipe 至少需要一个节点")
    for node in recipe.nodes:
        if node.type not in NODE_CATALOG:
            raise CompileError(f"未知节点类型：{node.type}")
    incoming: dict[str, list[RecipeEdge]] = defaultdict(list)
    outgoing: dict[str, list[RecipeEdge]] = defaultdict(list)
    for edge in recipe.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise CompileError("Edge 引用了不存在的节点")
        source_spec = NODE_CATALOG[recipe_node(recipe, edge.source).type]
        target_spec = NODE_CATALOG[recipe_node(recipe, edge.target).type]
        if edge.source_port not in source_spec["outputs"] or edge.target_port not in target_spec["inputs"]:
            raise CompileError(f"类型端口不兼容：{edge.source_port} → {edge.target_port}")
        incoming[edge.target].append(edge)
        outgoing[edge.source].append(edge)
    indegree = {node_id: len(incoming[node_id]) for node_id in node_ids}
    queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for edge in outgoing[node_id]:
            indegree[edge.target] -= 1
            if indegree[edge.target] == 0:
                queue.append(edge.target)
    if visited != len(node_ids):
        raise CompileError("Recipe 包含未声明的环；纠错检索必须使用 bounded_corrective")
    hash_value = hashlib.sha256(_canonical(recipe)).hexdigest()
    return recipe.model_copy(update={"status": "validated", "hash": hash_value, "created_at": recipe.created_at or utc_now()})


def recipe_node(recipe: Recipe, node_id: str) -> RecipeNode:
    for node in recipe.nodes:
        if node.id == node_id:
            return node
    raise CompileError(f"找不到节点：{node_id}")


def default_recipes() -> list[Recipe]:
    recipes = [
        Recipe(recipe_id="v0_1_dense", name="V0.1 Dense baseline", version="0.1.0", nodes=[RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="c", type="context_builder"), RecipeNode(id="g", type="llm_generate"), RecipeNode(id="p", type="policy_gate")], edges=[RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="c", target_port="candidates"), RecipeEdge(source="q", source_port="query", target="g", target_port="query"), RecipeEdge(source="c", source_port="context", target="g", target_port="context"), RecipeEdge(source="g", source_port="answer", target="p", target_port="answer"), RecipeEdge(source="c", source_port="evidence", target="p", target_port="evidence")]),
        Recipe(recipe_id="v0_2_hybrid", name="V0.2 Dense + Sparse + RRF", version="0.2.0", nodes=[RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="s", type="sparse_retrieve"), RecipeNode(id="f", type="rrf_fusion"), RecipeNode(id="c", type="context_builder"), RecipeNode(id="g", type="llm_generate"), RecipeNode(id="p", type="policy_gate")], edges=[RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="q", source_port="query", target="s", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="f", target_port="candidates"), RecipeEdge(source="s", source_port="candidates", target="f", target_port="candidates"), RecipeEdge(source="f", source_port="candidates", target="c", target_port="candidates"), RecipeEdge(source="q", source_port="query", target="g", target_port="query"), RecipeEdge(source="c", source_port="context", target="g", target_port="context"), RecipeEdge(source="g", source_port="answer", target="p", target_port="answer"), RecipeEdge(source="c", source_port="evidence", target="p", target_port="evidence")]),
        Recipe(recipe_id="v0_9_operations", name="V0.9 Production envelope", version="0.9.0", nodes=[RecipeNode(id="limit", type="rate_limit"), RecipeNode(id="cache", type="cache"), RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="c", type="context_builder"), RecipeNode(id="g", type="llm_generate"), RecipeNode(id="p", type="policy_gate")], edges=[RecipeEdge(source="limit", source_port="query", target="cache", target_port="query"), RecipeEdge(source="cache", source_port="query", target="q", target_port="query"), RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="c", target_port="candidates"), RecipeEdge(source="q", source_port="query", target="g", target_port="query"), RecipeEdge(source="c", source_port="context", target="g", target_port="context"), RecipeEdge(source="g", source_port="answer", target="p", target_port="answer"), RecipeEdge(source="c", source_port="evidence", target="p", target_port="evidence")]),
        Recipe(recipe_id="v1_controlled_agent", name="V1 Controlled Agent", version="1.0.0", nodes=[RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="draft", type="build_ticket_draft"), RecipeNode(id="approval", type="approval")], edges=[RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="q", source_port="query", target="draft", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="draft", target_port="candidates"), RecipeEdge(source="draft", source_port="artifact", target="approval", target_port="artifact")]),
    ]
    hybrid = recipes[1]
    def hybrid_variant(recipe_id: str, name: str, extra_nodes: list[RecipeNode], extra_edges: list[RecipeEdge], remove_edges: set[tuple[str, str, str, str]] = set()) -> Recipe:
        edges = [edge for edge in hybrid.edges if (edge.source, edge.source_port, edge.target, edge.target_port) not in remove_edges]
        match = re.match(r"v(\d+)_(\d+)", recipe_id)
        version = f"{match.group(1)}.{match.group(2)}.0" if match else "0.1.0"
        return Recipe(recipe_id=recipe_id, name=name, version=version, nodes=hybrid.nodes + extra_nodes, edges=edges + extra_edges)

    recipes.extend([
        hybrid_variant("v0_3_intent", "V0.3 Intent + Metadata", [RecipeNode(id="intent", type="intent_router"), RecipeNode(id="filter", type="metadata_filter")], [RecipeEdge(source="q", source_port="query", target="intent", target_port="query"), RecipeEdge(source="intent", source_port="query", target="filter", target_port="query"), RecipeEdge(source="filter", source_port="query", target="d", target_port="query"), RecipeEdge(source="filter", source_port="query", target="s", target_port="query")], {("q", "query", "d", "query"), ("q", "query", "s", "query")}),
        hybrid_variant("v0_4_rerank", "V0.4 Hybrid + Cross-Encoder", [RecipeNode(id="r", type="reranker")], [RecipeEdge(source="f", source_port="candidates", target="r", target_port="candidates"), RecipeEdge(source="q", source_port="query", target="r", target_port="query"), RecipeEdge(source="r", source_port="evidence", target="c", target_port="evidence")], {("f", "candidates", "c", "candidates")}),
        hybrid_variant("v0_5_context", "V0.5 Contextual + Parent-Child", [RecipeNode(id="parent", type="parent_expansion")], [RecipeEdge(source="f", source_port="candidates", target="parent", target_port="evidence"), RecipeEdge(source="parent", source_port="evidence", target="c", target_port="evidence")], {("f", "candidates", "c", "candidates")}),
        hybrid_variant("v0_6_corrective", "V0.6 Adaptive + Corrective", [RecipeNode(id="grade", type="evidence_grade"), RecipeNode(id="retry", type="bounded_corrective")], [RecipeEdge(source="f", source_port="candidates", target="grade", target_port="evidence"), RecipeEdge(source="q", source_port="query", target="retry", target_port="query"), RecipeEdge(source="grade", source_port="decision", target="retry", target_port="decision")]),
        hybrid_variant("v0_7_graph", "V0.7 Graph-Augmented", [RecipeNode(id="graph", type="graph_query")], [RecipeEdge(source="q", source_port="query", target="graph", target_port="query"), RecipeEdge(source="graph", source_port="evidence", target="c", target_port="evidence")]),
        hybrid_variant("v0_8_multimodal", "V0.8 PDF / Layout Route", [RecipeNode(id="pdf", type="pdf_page_retrieve")], [RecipeEdge(source="q", source_port="query", target="pdf", target_port="query"), RecipeEdge(source="pdf", source_port="evidence", target="c", target_port="evidence")]),
    ])
    return [compile_recipe(recipe) for recipe in recipes]
