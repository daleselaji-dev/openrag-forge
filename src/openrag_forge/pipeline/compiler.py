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


# ---------------------------------------------------------------------------
# 节点目录元数据（供工作台展示与教学）。
# implemented 字段是「诚实标注」的核心约定：
#   live     —— 节点声明的算法在当前代码里真实执行；
#   fallback —— 节点存在，但执行时退化为共享路径（如稀疏检索共用稠密结果）；
#   stub     —— 仅在 Trace 中记录经过，不改变任何数据（占位直通）。
# 工作台必须原样展示这些标注，禁止把 stub 呈现成已实现。
# ---------------------------------------------------------------------------

NODE_META: dict[str, dict[str, Any]] = {
    "parse_route": {"title": "解析路由", "implemented": "live", "execution_note": "根据文件签名与扩展名选择真实解析器，决策带置信度与 reason_codes。", "teach": {"what": "上传的原始字节先经过路由：识别 PDF/Office/HTML/表格/纯文本，选出对应解析器。这是 RAG 数据面质量的第一道闸门。", "tune": "route=auto 让签名检测做决定；解析结果不对时可强制指定路由后 reprocess。", "pitfalls": "把扫描版 PDF 当纯文本解析会得到空 Block；路由错误应看 reason_codes 而不是直接换模型。"}},
    "native_parser": {"title": "文本解析", "implemented": "live", "execution_note": "由 parse_route 路由到达时真实执行（Markdown/纯文本 → 结构化 Block）。", "teach": {"what": "把 Markdown / 纯文本按标题层级切成 Block，保留 heading_path。", "tune": "无参数；质量取决于原文结构是否清晰。", "pitfalls": "没有标题结构的长文本会变成大段 paragraph，后续 Chunk 边界会比较生硬。"}},
    "pdf_parser": {"title": "PDF 解析", "implemented": "live", "execution_note": "按页提取文本；不做版面分析（layout 路由为轻量近似）。", "teach": {"what": "按页读取 PDF 文本层，每页一个 Block 并记录页码。", "tune": "扫描件（无文本层）需要 OCR，本框架未内置。", "pitfalls": "双栏排版按行拼接可能乱序；表格会被拍平成文本。"}},
    "office_parser": {"title": "Office 解析", "implemented": "live", "execution_note": "DOCX/XLSX 结构化提取真实执行。", "teach": {"what": "从 Office XML 提取段落与表格行。", "tune": "无参数。", "pitfalls": "嵌入图片/图表内容不会被提取。"}},
    "tabular_parser": {"title": "表格解析", "implemented": "live", "execution_note": "CSV/XLSX 每行生成 row Block，真实执行。", "teach": {"what": "把表格行转成带表头上下文的文本 Block，便于按行检索。", "tune": "无参数。", "pitfalls": "超宽表每行文本很长，注意 Chunk 大小配合。"}},
    "chunker": {"title": "Chunker", "implemented": "live", "execution_note": "按 max_chars/overlap 滑窗切分，配置在上传时真实生效。", "teach": {"what": "把 Block 切成检索粒度的 Chunk。Chunk 是检索与引用的最小单位。", "tune": "max_chars 越小召回越精确但上下文越碎；overlap 用于缓解切断句子的伤害。改完在「数据」页重新上传或 reprocess 才会生效。", "pitfalls": "常见误区：无限加大 chunk 提升『上下文』——会稀释向量语义，召回反而变差。"}},
    "metadata_enricher": {"title": "Metadata", "implemented": "live", "execution_note": "保存 heading_path/页码等基础 metadata；没有 LLM 增强。", "teach": {"what": "把标题路径、页码、block 类型等写入 Chunk metadata，供过滤与引用定位。", "tune": "无参数；进阶做法是用 LLM 生成摘要/标签（本框架未实现）。", "pitfalls": "metadata 缺失时 metadata_filter 类节点无从过滤。"}},
    "embed_index": {"title": "Embedding / 索引", "implemented": "live", "execution_note": "调用 OpenAI 兼容 Embedding 端点并写入 Qdrant；服务不可用时降级 deferred（真相源不受影响）。", "teach": {"what": "把 Chunk 向量化写入 Qdrant。Qdrant 是可重建的派生索引，SQLite 才是真相源。", "tune": "model_ref 绑定注册过的 Embedding 模型；换模型后必须重建索引，否则新旧向量不可比。", "pitfalls": "索引 deferred ≠ 失败：文档已保存，模型服务就绪后用「重建索引」补齐。"}},
    "question": {"title": "问题", "implemented": "live", "execution_note": "查询入口，透传问题文本。", "teach": {"what": "查询链路的入口节点，携带用户问题。", "tune": "无参数。", "pitfalls": "问题里含高风险措辞会触发安全门直接拒答（这是设计行为）。"}},
    "intent_router": {"title": "意图路由", "implemented": "stub", "execution_note": "占位直通：未实现意图分类，仅在 Trace 中记录经过。", "teach": {"what": "设计目标：按问题意图选择不同检索分支（闲聊/事实/操作类）。当前为占位。", "tune": "当前配置不生效。", "pitfalls": "不要以为加了这个节点就有意图识别——看 Trace 里的 stub_passthrough 标记。"}},
    "metadata_filter": {"title": "Metadata 过滤", "implemented": "live", "execution_note": "按 Chunk metadata 字段做检索前过滤；过滤为空时可 fallback_once。", "teach": {"what": "按版本/生效日期等 metadata 缩小检索范围。", "tune": "fields / on_empty 来自节点 config。", "pitfalls": "metadata 缺失时过滤可能为空集。"}},
    "dense_retrieve": {"title": "稠密检索", "implemented": "live", "execution_note": "真实执行：优先 Qdrant 向量检索；Qdrant/Embedding 不可用时降级为词法重叠检索（Trace 标 fallback）。", "teach": {"what": "把问题向量化后在 Qdrant 里找最近邻 Chunk，是这条链路真正的召回主力。", "tune": "top_k 控制候选条数（本节点配置优先于请求参数）；score_threshold 过滤低分噪声。", "pitfalls": "threshold 设太高会把全部候选滤光，退化成『没有证据』；看 Trace 里 backend 字段确认走的是 qdrant_dense 还是 lexical_fallback。"}},
    "sparse_retrieve": {"title": "稀疏检索 / BM25", "implemented": "live", "execution_note": "本地 BM25 稀疏检索（backend=bm25_local），与稠密检索并行产出独立候选集。", "teach": {"what": "BM25 关键词召回，与稠密检索互补。", "tune": "top_k / k1 / b 在节点 config 中可调。", "pitfalls": "语料极小时 BM25 分数可能偏低，可与 dense 一起做 RRF。"}},
    "rrf_fusion": {"title": "RRF 融合", "implemented": "live", "execution_note": "对双路候选做真实 Reciprocal Rank Fusion 排名合并。", "teach": {"what": "合并多路召回的排序结果。", "tune": "k 参数控制融合平滑度。", "pitfalls": "只有一路候选时融合效果有限。"}},
    "reranker": {"title": "重排", "implemented": "fallback", "execution_note": "端点提供 /rerank 时真实调用 Cross-Encoder；否则如实直通并记录原因。", "teach": {"what": "用 Cross-Encoder 精排候选。", "tune": "model_ref/candidate_k/final_k 来自节点配置。", "pitfalls": "本地未部署 rerank 端点时会降级直通。"}},
    "context_builder": {"title": "上下文构建", "implemented": "live", "execution_note": "按 token 预算截断证据并拼装上下文。", "teach": {"what": "控制进入 Prompt 的证据体量。", "tune": "token_budget 真实生效。", "pitfalls": "预算过小会截断关键证据。"}},
    "parent_expansion": {"title": "父块扩展", "implemented": "live", "execution_note": "命中子 Chunk 后按 block_ids 扩展父级上下文。", "teach": {"what": "小 Chunk 命中后回捞更大上下文。", "tune": "window / max_chars_per_side 可调。", "pitfalls": "无 block 关系时扩展为空。"}},
    "llm_generate": {"title": "LLM 生成", "implemented": "live", "execution_note": "真实调用 OpenAI 兼容 Chat 端点；不可用时降级为证据摘要（extractive_fallback），缺引用时触发 citation_repair。", "teach": {"what": "用检索证据约束生成回答，要求每个事实引用 [S#]。", "tune": "model_ref 绑定注册的 Chat 模型，temperature/max_tokens 真实生效。", "pitfalls": "看 Trace 的 provider 字段：extractive_fallback 说明模型端点没接通，回答只是证据摘要。"}},
    "evidence_grade": {"title": "证据评分", "implemented": "stub", "execution_note": "占位直通：未实现证据充分性评分。", "teach": {"what": "设计目标：判断证据是否足以回答，不足则触发纠错检索。", "tune": "当前配置不生效。", "pitfalls": "无。"}},
    "policy_gate": {"title": "安全门", "implemented": "live", "execution_note": "真实执行，但仅是关键词正则风险检测 + 无副作用检查，不是完整内容安全系统。", "teach": {"what": "拦截高风险请求（退款承诺/违法认定等），并声明本次运行无外部副作用。", "tune": "风险词表在 policies/basic.py，按业务扩展。", "pitfalls": "正则门挡不住改写攻击；接真实客户前需要独立的内容安全服务。"}},
    "bounded_corrective": {"title": "有限纠错", "implemented": "live", "execution_note": "证据不足时改写查询并重试一次（硬上限 max_retries）。", "teach": {"what": "有界纠错检索，防止无界循环。", "tune": "max_retries 必须显式声明。", "pitfalls": "重试仍无证据时会如实记录。"}},
    "graph_query": {"title": "图谱查询", "implemented": "stub", "execution_note": "compile-complete / runtime-stub：Neo4j 图谱后端未实现，节点记录 skipped。", "teach": {"what": "设计目标：从知识图谱召回实体关系证据。需要 graph profile + Neo4j。", "tune": "当前配置不生效。", "pitfalls": "无 Neo4j 时不会产出伪造证据。"}},
    "pdf_page_retrieve": {"title": "PDF 页检索", "implemented": "live", "execution_note": "在 page 类型 Chunk 上执行 BM25 页级检索。", "teach": {"what": "按 PDF 页召回证据。", "tune": "top_k 可调。", "pitfalls": "无页级 Chunk 时返回 0 命中。"}},
    "cache": {"title": "缓存", "implemented": "live", "execution_note": "按 question+recipe_hash 的进程内 TTL 缓存；命中时下游节点记录 skipped。", "teach": {"what": "缓存整次运行结果。", "tune": "ttl_seconds 可调。", "pitfalls": "必须以 recipe_hash 为 key 的一部分。"}},
    "rate_limit": {"title": "限流", "implemented": "live", "execution_note": "Recipe 级进程内滑动窗口限流；超限安全短路。API 中间件另有全局限流。", "teach": {"what": "保护下游模型服务。", "tune": "requests_per_minute 可调。", "pitfalls": "多副本部署需在网关层限流。"}},
    "approval": {"title": "人工审批", "implemented": "live", "execution_note": "真实执行：运行在此停住并标记 approval_required，绝不自动放行。", "teach": {"what": "受控 Agent 的关键闸门：草稿必须人工审批，框架不代替人做决定。", "tune": "required 固定为 true 是有意设计。", "pitfalls": "无。"}},
    "build_ticket_draft": {"title": "工单草稿", "implemented": "live", "execution_note": "真实执行：字段抽取为启发式正则，生成结构化草稿并列出缺失字段。", "teach": {"what": "从用户消息抽取工单字段，缺什么列什么，产出待审批草稿，声明禁用动作清单。", "tune": "字段规则在 app.py，按业务工单模型扩展。", "pitfalls": "正则抽取只是演示级；生产应换成受约束的结构化抽取。"}},
}

# 结构化配置表单的字段描述：工作台据此渲染表单（JSON 仅作为高级模式）。
# effective=False 表示该字段当前不被执行器读取（诚实标注，前端置灰提示）。
CONFIG_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "parse_route": [{"key": "route", "label": "解析路由", "type": "select", "options": ["auto", "native_text", "html_structure", "pdf_page_text", "pdf_layout", "office_structure", "tabular", "json_structure"], "effective": True, "help": "auto 按文件签名自动选择"}],
    "chunker": [
        {"key": "max_chars", "label": "Chunk 大小（字符）", "type": "number", "min": 200, "max": 8000, "step": 50, "effective": True, "help": "上传/重解析时生效"},
        {"key": "overlap", "label": "重叠（字符）", "type": "number", "min": 0, "max": 800, "step": 10, "effective": True, "help": "相邻 Chunk 重叠区"},
    ],
    "embed_index": [
        {"key": "model_ref", "label": "Embedding 模型", "type": "model", "model_kind": "embedding", "effective": True, "help": "上传时选择的 Embedding 覆盖此项"},
        {"key": "collection", "label": "Qdrant Collection", "type": "text", "effective": False, "help": "当前固定使用配置的 collection"},
    ],
    "dense_retrieve": [
        {"key": "top_k", "label": "Top K", "type": "number", "min": 1, "max": 20, "step": 1, "effective": True, "help": "节点配置优先于请求参数"},
        {"key": "score_threshold", "label": "分数阈值", "type": "number", "min": 0, "max": 1, "step": 0.05, "effective": True, "help": "低于阈值的向量命中被丢弃"},
        {"key": "model_ref", "label": "Embedding 模型", "type": "model", "model_kind": "embedding", "effective": False, "help": "查询侧必须与索引侧同模型，暂不支持单独切换"},
    ],
    "sparse_retrieve": [{"key": "top_k", "label": "Top K", "type": "number", "min": 1, "max": 50, "step": 1, "effective": False, "help": "稀疏索引未实现，仅记录"}],
    "rrf_fusion": [{"key": "k", "label": "RRF k", "type": "number", "min": 1, "max": 200, "step": 1, "effective": False, "help": "占位节点，不生效"}],
    "reranker": [
        {"key": "model_ref", "label": "Reranker 模型", "type": "model", "model_kind": "reranker", "effective": False, "help": "占位节点，不生效"},
        {"key": "candidate_k", "label": "精排候选数", "type": "number", "min": 1, "max": 100, "step": 1, "effective": False},
        {"key": "final_k", "label": "输出条数", "type": "number", "min": 1, "max": 20, "step": 1, "effective": False},
    ],
    "context_builder": [
        {"key": "token_budget", "label": "Token 预算", "type": "number", "min": 500, "max": 16000, "step": 100, "effective": False, "help": "占位节点，不生效"},
        {"key": "mmr_lambda", "label": "MMR λ", "type": "number", "min": 0, "max": 1, "step": 0.05, "effective": False},
    ],
    "llm_generate": [
        {"key": "model_ref", "label": "Chat 模型", "type": "model", "model_kind": "chat", "effective": True, "help": "绑定注册的 OpenAI 兼容端点"},
        {"key": "temperature", "label": "温度", "type": "number", "min": 0, "max": 2, "step": 0.05, "effective": True},
        {"key": "max_tokens", "label": "最大 Token", "type": "number", "min": 64, "max": 4096, "step": 32, "effective": True},
    ],
    "metadata_filter": [{"key": "on_empty", "label": "过滤为空时", "type": "select", "options": ["fallback_once", "fail"], "effective": False, "help": "占位节点，不生效"}],
    "bounded_corrective": [{"key": "max_retries", "label": "最大重试", "type": "number", "min": 0, "max": 3, "step": 1, "effective": False, "help": "占位节点，不生效"}],
    "cache": [{"key": "ttl_seconds", "label": "TTL（秒）", "type": "number", "min": 0, "max": 86400, "step": 30, "effective": False, "help": "占位节点，不生效"}],
    "rate_limit": [{"key": "requests_per_minute", "label": "每分钟请求数", "type": "number", "min": 1, "max": 6000, "step": 1, "effective": False, "help": "真实限流请配 OPENRAG_RATE_LIMIT_PER_MINUTE"}],
    "approval": [{"key": "required", "label": "必须人工审批", "type": "boolean", "effective": True, "help": "固定为 true 是有意设计"}],
}


def node_catalog() -> dict[str, dict[str, Any]]:
    config_hints = {
        "parse_route": {"route": "auto"},
        "chunker": {"max_chars": 1200, "overlap": 120},
        "embed_index": {"model_ref": "configured-embedding", "collection": "openrag_forge"},
        "dense_retrieve": {"model_ref": "configured-embedding", "top_k": 5, "score_threshold": 0.0},
        "sparse_retrieve": {"backend": "qdrant_named_sparse", "top_k": 20},
        "rrf_fusion": {"k": 60, "weights": [1.0, 1.0]},
        "reranker": {"model_ref": "configured-reranker", "candidate_k": 50, "final_k": 6},
        "context_builder": {"token_budget": 4000, "official_minimum": 1, "mmr_lambda": 0.7},
        "llm_generate": {"model_ref": "configured-chat", "temperature": 0.1, "max_tokens": 600},
        "bounded_corrective": {"max_retries": 1, "query_variant": "domain_term_expansion"},
        "metadata_filter": {"fields": {}, "on_empty": "fallback_once"},
        "rate_limit": {"requests_per_minute": 60},
        "cache": {"ttl_seconds": 300, "key": "question+recipe_hash"},
        "approval": {"required": True},
    }
    catalog: dict[str, dict[str, Any]] = {}
    for node_type, spec in NODE_CATALOG.items():
        meta = NODE_META.get(node_type, {})
        catalog[node_type] = {
            **spec,
            "title": meta.get("title", node_type),
            "implemented": meta.get("implemented", "stub"),
            "execution_note": meta.get("execution_note", ""),
            "description": meta.get("execution_note") or f"{node_type} component",
            "teach": meta.get("teach", {}),
            "config_defaults": config_hints.get(node_type, {}),
            "config_schema": CONFIG_SCHEMAS.get(node_type, []),
        }
    return catalog


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
        Recipe(recipe_id="custom_ingest", name="Custom Document Parsing", version="0.0.1", nodes=[RecipeNode(id="route", type="parse_route"), RecipeNode(id="chunk", type="chunker"), RecipeNode(id="meta", type="metadata_enricher"), RecipeNode(id="index", type="embed_index")], edges=[RecipeEdge(source="route", source_port="blocks", target="chunk", target_port="blocks"), RecipeEdge(source="chunk", source_port="chunks", target="meta", target_port="chunks"), RecipeEdge(source="meta", source_port="chunks", target="index", target_port="chunks")]),
        Recipe(recipe_id="v0_1_dense", name="V0.1 Dense baseline", version="0.1.0", nodes=[RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="c", type="context_builder"), RecipeNode(id="g", type="llm_generate"), RecipeNode(id="p", type="policy_gate")], edges=[RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="c", target_port="candidates"), RecipeEdge(source="q", source_port="query", target="g", target_port="query"), RecipeEdge(source="c", source_port="context", target="g", target_port="context"), RecipeEdge(source="g", source_port="answer", target="p", target_port="answer"), RecipeEdge(source="c", source_port="evidence", target="p", target_port="evidence")]),
        Recipe(recipe_id="v0_2_hybrid", name="V0.2 Dense + Sparse + RRF", version="0.2.0", nodes=[RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="s", type="sparse_retrieve"), RecipeNode(id="f", type="rrf_fusion"), RecipeNode(id="c", type="context_builder"), RecipeNode(id="g", type="llm_generate"), RecipeNode(id="p", type="policy_gate")], edges=[RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="q", source_port="query", target="s", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="f", target_port="candidates"), RecipeEdge(source="s", source_port="candidates", target="f", target_port="candidates"), RecipeEdge(source="f", source_port="candidates", target="c", target_port="candidates"), RecipeEdge(source="q", source_port="query", target="g", target_port="query"), RecipeEdge(source="c", source_port="context", target="g", target_port="context"), RecipeEdge(source="g", source_port="answer", target="p", target_port="answer"), RecipeEdge(source="c", source_port="evidence", target="p", target_port="evidence")]),
        Recipe(recipe_id="v0_9_operations", name="V0.9 Production envelope", version="0.9.0", nodes=[RecipeNode(id="limit", type="rate_limit"), RecipeNode(id="cache", type="cache"), RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="c", type="context_builder"), RecipeNode(id="g", type="llm_generate"), RecipeNode(id="p", type="policy_gate")], edges=[RecipeEdge(source="limit", source_port="query", target="cache", target_port="query"), RecipeEdge(source="cache", source_port="query", target="q", target_port="query"), RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="c", target_port="candidates"), RecipeEdge(source="q", source_port="query", target="g", target_port="query"), RecipeEdge(source="c", source_port="context", target="g", target_port="context"), RecipeEdge(source="g", source_port="answer", target="p", target_port="answer"), RecipeEdge(source="c", source_port="evidence", target="p", target_port="evidence")]),
        Recipe(recipe_id="v1_controlled_agent", name="V1 Controlled Agent", version="1.0.0", nodes=[RecipeNode(id="q", type="question"), RecipeNode(id="d", type="dense_retrieve"), RecipeNode(id="draft", type="build_ticket_draft"), RecipeNode(id="approval", type="approval")], edges=[RecipeEdge(source="q", source_port="query", target="d", target_port="query"), RecipeEdge(source="q", source_port="query", target="draft", target_port="query"), RecipeEdge(source="d", source_port="candidates", target="draft", target_port="candidates"), RecipeEdge(source="draft", source_port="artifact", target="approval", target_port="artifact")]),
    ]
    hybrid = next(recipe for recipe in recipes if recipe.recipe_id == "v0_2_hybrid")
    def hybrid_variant(recipe_id: str, name: str, extra_nodes: list[RecipeNode], extra_edges: list[RecipeEdge], remove_edges: frozenset[tuple[str, str, str, str]] = frozenset()) -> Recipe:
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
