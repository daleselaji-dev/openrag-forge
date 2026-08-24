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


# runtime 字段的诚实约定：
#   implemented  —— 运行层有真实行为（可能带降级路径，降级会写入 Trace 的 backend/fallback 字段）
#   degradable   —— 有真实后端调用路径，但后端不可用时按 Trace 声明降级/直通
#   stub         —— compile-complete / runtime-stub：编译期类型约束完整，运行层暂无真实后端
NODE_DOCS: dict[str, dict[str, Any]] = {
    "parse_route": {
        "title": "解析路由", "runtime": "implemented",
        "description": "按内容签名（%PDF、PK zip 头）+ 扩展名 + MIME 确定性选择解析器路由，输出 confidence 与 reason_codes；用户可显式覆盖。",
        "why": "路由必须可解释、可复测、零成本，因此用规则而不是 LLM。",
        "downstream": "路由决定 Block 的类型与粒度，直接影响 Chunk 切分与检索证据的出处。",
        "tunables": [
            {"name": "route", "type": "enum", "options": ["auto", "native_text", "html_structure", "pdf_page_text", "pdf_layout", "office_structure", "tabular", "json_structure"], "description": "auto 表示按内容自动路由；显式指定会记录 user_selected_route"},
        ],
    },
    "native_parser": {
        "title": "文本解析", "runtime": "implemented",
        "description": "按空行切分纯文本 / Markdown 为 paragraph Block。等价于 parse_route 固定选择 native_text 路由。",
        "why": "最保守的兜底解析器，任何格式失败后都能落到这里。",
        "downstream": "产出的 paragraph Block 是 Chunk 的最小单位。",
        "tunables": [],
    },
    "pdf_parser": {
        "title": "PDF 解析", "runtime": "implemented",
        "description": "pypdf 逐页抽取文本，产出 page 级 Block（带页码）。等价于 parse_route 固定选择 pdf_page_text。",
        "why": "页级 Block 保留页码出处，可支撑 pdf_page_retrieve 页级检索。",
        "downstream": "page Block 的页码进入 Chunk metadata，可用于引用定位。",
        "tunables": [],
    },
    "office_parser": {
        "title": "Office 解析", "runtime": "implemented",
        "description": "解压 DOCX/PPTX zip 包并抽取 XML 文本，产出 paragraph Block。等价于 parse_route 固定选择 office_structure。",
        "why": "不依赖重型 Office 运行时，Lite 安装即可解析。",
        "downstream": "文档结构（part 名）保留在 Block metadata。",
        "tunables": [],
    },
    "tabular_parser": {
        "title": "表格解析", "runtime": "implemented",
        "description": "CSV/XLSX 逐行产出 row Block，保留行号。等价于 parse_route 固定选择 tabular。",
        "why": "行级 Block 让表格数据可以按行被检索与引用。",
        "downstream": "row Block 的行号进入 Chunk metadata。",
        "tunables": [],
    },
    "chunker": {
        "title": "Chunker", "runtime": "implemented",
        "description": "把 Block 切成固定窗口 + 重叠的 Chunk，每个 Chunk 记录来源 block_ids。max_chars / overlap 实际生效于 ingest。",
        "why": "Chunk 是检索与引用的原子单位；窗口大小直接影响召回粒度与上下文预算。",
        "downstream": "chunk_id 是 Evidence 引用和 Qdrant payload 的主键。",
        "tunables": [
            {"name": "max_chars", "type": "int", "min": 200, "max": 4000, "description": "单个 Chunk 最大字符数；越小召回越精确但上下文越碎"},
            {"name": "overlap", "type": "int", "min": 0, "max": 400, "description": "相邻 Chunk 重叠字符数，缓解切断句子的问题"},
        ],
    },
    "metadata_enricher": {
        "title": "Metadata 增强", "runtime": "implemented",
        "description": "为每个 Chunk 补充 title（最近标题/文件名）、language（zh/en 比例检测）、keywords（词频 Top-K），写入 Chunk metadata。",
        "why": "Metadata 是 metadata_filter 与引用展示的数据来源。",
        "downstream": "增强字段随 Chunk 进入 Qdrant payload 与 Evidence metadata。",
        "tunables": [
            {"name": "keywords_top_k", "type": "int", "min": 0, "max": 20, "description": "每个 Chunk 提取的关键词数量；0 表示关闭"},
        ],
    },
    "embed_index": {
        "title": "Embedding / 派生索引", "runtime": "implemented",
        "description": "调用 OpenAI 兼容 Embedding 端点向量化 Chunk 并写入 Qdrant。失败时状态为 deferred（含 next_action），真相源不受影响。",
        "why": "Qdrant 永远是可重建的派生索引；索引失败不阻塞上传解析。",
        "downstream": "决定 dense_retrieve 的召回质量；embedding 模型可按知识库切换。",
        "tunables": [
            {"name": "model_ref", "type": "model", "kind": "embedding", "description": "绑定注册的 Embedding 模型"},
            {"name": "collection", "type": "string", "description": "Qdrant collection 名称"},
        ],
    },
    "question": {
        "title": "问题入口", "runtime": "implemented",
        "description": "接收用户问题，做规范化与分词，输出 query。请求级安全门在此之前执行。",
        "why": "把问题作为显式节点，让 Trace 从第一步就可审计。",
        "downstream": "query 供检索与生成节点消费。",
        "tunables": [],
    },
    "intent_router": {
        "title": "意图路由", "runtime": "implemented",
        "description": "启发式规则判断问题意图（procedural / factual / comparison / risk），记录进 Trace 供下游参考。",
        "why": "意图是选择检索策略与提示词的依据；当前为规则实现，可替换为分类模型。",
        "downstream": "意图写入 Trace impact，不改变查询本身。",
        "tunables": [],
    },
    "metadata_filter": {
        "title": "Metadata 过滤", "runtime": "implemented",
        "description": "按 fields 配置过滤候选 Chunk（如 block_type、language）。过滤后为空时按 on_empty 策略回退一次并在 Trace 记录。",
        "why": "版本化政策等场景必须先按元数据缩小候选池，避免混用过期内容。",
        "downstream": "缩小 dense/sparse 检索的候选池。",
        "tunables": [
            {"name": "fields", "type": "json", "description": "键值对过滤条件，匹配 Chunk metadata，如 {\"block_type\": \"page\"}"},
            {"name": "on_empty", "type": "enum", "options": ["fallback_once", "fail"], "description": "过滤后为空的行为：回退到未过滤候选或直接报告失败"},
        ],
    },
    "dense_retrieve": {
        "title": "Dense 检索", "runtime": "implemented",
        "description": "向量检索：Embedding 问题 → Qdrant 搜索 → 分数阈值过滤 → 真相源 chunk_id 过滤。Qdrant/Embedding 不可用时降级为本地词法检索（Trace 记录 backend=lexical_fallback）。",
        "why": "语义召回主路径；真相源过滤保证索引幽灵点永远不会成为证据。",
        "downstream": "输出 candidates 供融合 / 重排 / 上下文构建。",
        "tunables": [
            {"name": "top_k", "type": "int", "min": 1, "max": 50, "description": "召回候选数量"},
            {"name": "score_threshold", "type": "float", "min": 0, "max": 1, "description": "低于该余弦分数的命中被丢弃"},
            {"name": "model_ref", "type": "model", "kind": "embedding", "description": "查询侧 Embedding 模型（须与索引一致）"},
        ],
    },
    "sparse_retrieve": {
        "title": "Sparse / BM25 检索", "runtime": "implemented",
        "description": "内置 BM25 词法检索（backend=bm25_local），直接在 SQLite 真相源 Chunk 上打分，无需外部服务。Qdrant named-sparse 后端仍在 roadmap，若配置了该 backend 会在 Trace 中如实记录回退。",
        "why": "关键词/编号/术语类问题上词法检索优于向量；也是完全离线可用的召回路径。",
        "downstream": "输出 candidates，与 dense 候选做 RRF 融合。",
        "tunables": [
            {"name": "top_k", "type": "int", "min": 1, "max": 50, "description": "召回候选数量"},
            {"name": "k1", "type": "float", "min": 0.5, "max": 3, "description": "BM25 词频饱和参数"},
            {"name": "b", "type": "float", "min": 0, "max": 1, "description": "BM25 长度归一化参数"},
        ],
    },
    "rrf_fusion": {
        "title": "RRF 融合", "runtime": "implemented",
        "description": "对多路候选列表执行 Reciprocal Rank Fusion：score = Σ weight / (k + rank)。只有一路候选时如实直通并在 Trace 标注。",
        "why": "无需调分数刻度即可稳健融合 dense 与 sparse 两路召回。",
        "downstream": "输出融合排序后的 candidates。",
        "tunables": [
            {"name": "k", "type": "int", "min": 1, "max": 200, "description": "RRF 平滑常数，越大排名差异影响越小"},
            {"name": "weights", "type": "json", "description": "各路候选权重数组，如 [1.0, 0.7]"},
        ],
    },
    "reranker": {
        "title": "Cross-Encoder 重排", "runtime": "degradable",
        "description": "若绑定的 reranker 模型端点提供 /rerank 接口（Cohere/Jina/TEI 兼容）则真实调用重排；后端不可用时如实直通，Trace 记录 backend=passthrough 与原因，绝不静默伪装。",
        "why": "重排在候选多、问题模糊时收益最大；但没有后端就不该假装重排过。",
        "downstream": "输出精排后的 evidence（final_k 条）。",
        "tunables": [
            {"name": "model_ref", "type": "model", "kind": "reranker", "description": "绑定注册的 Reranker 模型"},
            {"name": "candidate_k", "type": "int", "min": 1, "max": 100, "description": "参与重排的候选数量"},
            {"name": "final_k", "type": "int", "min": 1, "max": 20, "description": "重排后保留的证据数量"},
        ],
    },
    "context_builder": {
        "title": "上下文构建", "runtime": "implemented",
        "description": "对候选/证据去重、按分数排序、套用 token_budget 字符预算截断，产出最终 Evidence 列表与拼接后的 context。丢弃与保留数量写入 Trace。",
        "why": "上下文预算是成本与幻觉控制的核心旋钮；证据在这里获得 [S#] 引用编号。",
        "downstream": "context 供 llm_generate；evidence 供 policy_gate 与 Capsule。",
        "tunables": [
            {"name": "token_budget", "type": "int", "min": 500, "max": 32000, "description": "上下文预算（近似 token 数），超出的证据被丢弃并记录"},
            {"name": "max_per_doc", "type": "int", "min": 0, "max": 10, "description": "单文档最多保留证据数；0 表示不限制"},
        ],
    },
    "parent_expansion": {
        "title": "父子块扩展", "runtime": "implemented",
        "description": "对每条证据按真相源中的相邻 Chunk（同文档 order±window）扩展上下文文本，扩展量写入 Trace。",
        "why": "小 Chunk 召回精确但上下文不足；父子扩展兼顾两者。",
        "downstream": "扩展后的 evidence 文本进入上下文与引用展示。",
        "tunables": [
            {"name": "window", "type": "int", "min": 1, "max": 3, "description": "向前后各扩展多少个相邻 Chunk"},
            {"name": "max_chars_per_side", "type": "int", "min": 100, "max": 2000, "description": "每侧扩展文本的字符上限"},
        ],
    },
    "llm_generate": {
        "title": "LLM 生成", "runtime": "implemented",
        "description": "调用 OpenAI 兼容 chat 端点生成受证据约束的回答。三级降级：模型 → 抽取式回答 → 明确说无证据；无 [S#] 引用时触发 citation_repair_fallback。实际使用的 provider 写入 Trace。",
        "why": "生成必须被证据边界约束；降级路径保证离线也可用。",
        "downstream": "answer 交给 policy_gate 审查。",
        "tunables": [
            {"name": "model_ref", "type": "model", "kind": "chat", "description": "绑定注册的 Chat 模型"},
            {"name": "temperature", "type": "float", "min": 0, "max": 2, "description": "采样温度；证据问答建议 ≤0.3"},
            {"name": "max_tokens", "type": "int", "min": 64, "max": 4000, "description": "回答最大 token 数"},
        ],
    },
    "evidence_grade": {
        "title": "证据评分", "runtime": "implemented",
        "description": "按证据数量与最高分判定 sufficient / insufficient，输出 decision 供 bounded_corrective 消费。",
        "why": "纠错检索必须由显式的证据判定触发，而不是模型自由裁量。",
        "downstream": "decision=insufficient 时触发有限纠错重试。",
        "tunables": [
            {"name": "min_evidence", "type": "int", "min": 1, "max": 10, "description": "低于该证据数量判定为 insufficient"},
            {"name": "min_top_score", "type": "float", "min": 0, "max": 1, "description": "最高分低于该值判定为 insufficient"},
        ],
    },
    "policy_gate": {
        "title": "安全策略门", "runtime": "implemented",
        "description": "回答级审查：校验引用有效性（[S#] 必须对应真实证据）、无证据时强制 human_review。请求级高风险拦截在检索之前执行，被跳过的节点记录为 skipped。",
        "why": "拒绝与放行都是需要审计的决策，全部落入 Trace 与 Capsule。",
        "downstream": "输出最终安全决策，写入 RunResult.safety。",
        "tunables": [
            {"name": "require_citation", "type": "bool", "description": "有证据的回答必须包含 [S#] 引用"},
        ],
    },
    "bounded_corrective": {
        "title": "有限纠错检索", "runtime": "implemented",
        "description": "decision=insufficient 时用查询变体（去停用词 + 领域词扩展）重跑一次检索，最多 max_retries 次（编译器强制显式上限，禁止无界循环）。重试前后候选数量写入 Trace。",
        "why": "纠错是有界的显式节点，而不是 Agent 自由循环。",
        "downstream": "重试成功时替换下游使用的候选集。",
        "tunables": [
            {"name": "max_retries", "type": "int", "min": 1, "max": 2, "description": "最大重试次数（硬上限 2）"},
            {"name": "query_variant", "type": "enum", "options": ["domain_term_expansion", "keyword_only"], "description": "重试时的查询改写策略"},
        ],
    },
    "graph_query": {
        "title": "图谱检索", "runtime": "stub",
        "description": "compile-complete / runtime-stub：编译期端口类型完整，运行层暂无 Neo4j 后端，执行时记录 skipped 并注明原因，不产出伪造证据。",
        "why": "图谱检索需要 graph profile + Neo4j；诚实标注优于伪装执行。",
        "downstream": "接入后可输出实体关系证据补充上下文。",
        "tunables": [],
    },
    "pdf_page_retrieve": {
        "title": "PDF 页级检索", "runtime": "implemented",
        "description": "在 block_type=page 的 Chunk 上执行 BM25 页级检索，输出带页码的证据。没有 PDF 页级 Chunk 时如实记录 0 命中。",
        "why": "版面/扫描类 PDF 先按页召回再细读，页码本身就是引用出处。",
        "downstream": "页级证据与其它证据一起进入 context_builder。",
        "tunables": [
            {"name": "top_k", "type": "int", "min": 1, "max": 20, "description": "召回页数"},
        ],
    },
    "cache": {
        "title": "结果缓存", "runtime": "implemented",
        "description": "按 question + recipe_hash 键的进程内 TTL 缓存。命中时下游检索/生成节点记录 skipped（reason=cache_hit）并复用缓存结果。",
        "why": "生产信封组件：重复问题不重复付出模型成本。",
        "downstream": "命中时替代整条检索生成链路。",
        "tunables": [
            {"name": "ttl_seconds", "type": "int", "min": 10, "max": 86400, "description": "缓存有效期（秒）"},
        ],
    },
    "rate_limit": {
        "title": "限流", "runtime": "implemented",
        "description": "按 Recipe 的进程内滑动窗口限流。超限时记录 failed 并安全短路（HTTP 仍为 200，answer 说明限流），剩余配额写入 Trace。",
        "why": "生产信封组件：保护下游模型服务。",
        "downstream": "超限时跳过整条链路。",
        "tunables": [
            {"name": "requests_per_minute", "type": "int", "min": 1, "max": 6000, "description": "每分钟允许的请求数"},
        ],
    },
    "approval": {
        "title": "人工审批门", "runtime": "implemented",
        "description": "Agent 产物强制停靠点：artifact 停在 pending_human_approval，框架层没有任何外部副作用执行路径。",
        "why": "受控 Agent 的不变量：错误动作代价不对称的场景必须有人签字。",
        "downstream": "终点节点，产物进入 Capsule 等待人工处理。",
        "tunables": [
            {"name": "required", "type": "bool", "description": "是否强制人工审批（受控场景应恒为 true）"},
        ],
    },
    "build_ticket_draft": {
        "title": "工单草稿", "runtime": "implemented",
        "description": "从问题与证据生成结构化工单草稿：显式列出 missing_fields 与 forbidden_actions（发消息 / 写 CRM / 承诺退款 / 认定责任），永不执行外部动作。",
        "why": "Agent 只产出草稿，动作留给人。",
        "downstream": "artifact 流向 approval 节点停靠。",
        "tunables": [],
    },
}


def node_catalog() -> dict[str, dict[str, Any]]:
    config_hints = {
        "parse_route": {"route": "auto"},
        "chunker": {"max_chars": 1200, "overlap": 120},
        "metadata_enricher": {"keywords_top_k": 5},
        "embed_index": {"model_ref": "configured-embedding", "collection": "openrag_forge"},
        "dense_retrieve": {"model_ref": "configured-embedding", "top_k": 5, "score_threshold": 0.0},
        "sparse_retrieve": {"backend": "bm25_local", "top_k": 20, "k1": 1.5, "b": 0.75},
        "rrf_fusion": {"k": 60, "weights": [1.0, 1.0]},
        "reranker": {"model_ref": "configured-reranker", "candidate_k": 50, "final_k": 6},
        "context_builder": {"token_budget": 4000, "max_per_doc": 0},
        "parent_expansion": {"window": 1, "max_chars_per_side": 600},
        "llm_generate": {"model_ref": "configured-chat", "temperature": 0.1, "max_tokens": 600},
        "evidence_grade": {"min_evidence": 1, "min_top_score": 0.05},
        "policy_gate": {"require_citation": True},
        "bounded_corrective": {"max_retries": 1, "query_variant": "domain_term_expansion"},
        "metadata_filter": {"fields": {}, "on_empty": "fallback_once"},
        "pdf_page_retrieve": {"top_k": 3},
        "rate_limit": {"requests_per_minute": 60},
        "cache": {"ttl_seconds": 300, "key": "question+recipe_hash"},
        "approval": {"required": True},
    }
    catalog: dict[str, dict[str, Any]] = {}
    for node_type, spec in NODE_CATALOG.items():
        docs = NODE_DOCS.get(node_type, {})
        catalog[node_type] = {
            **spec,
            "title": docs.get("title", node_type),
            "runtime": docs.get("runtime", "stub"),
            "description": docs.get("description", f"{node_type} component"),
            "why": docs.get("why", ""),
            "downstream": docs.get("downstream", ""),
            "tunables": docs.get("tunables", []),
            "config_defaults": config_hints.get(node_type, {}),
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
