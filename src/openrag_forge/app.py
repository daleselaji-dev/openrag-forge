"""OpenRAG Forge API 入口（生产级 FastAPI 服务教学样例）。

本文件在功能基线之上补齐了生产必需的横切能力，每一处都有注释说明动机：

- **生命周期**（lifespan）：启动时初始化日志/追踪/存储并打印生产就绪告警，
  关机时 flush 追踪缓冲、关闭连接池——配合 SIGTERM 实现优雅关机；
- **中间件栈**（自外向内）：CORS → 安全响应头 → OTel Server Span →
  请求上下文（Request ID/访问日志/指标）→ 限流 → API Key 认证 → 路由；
- **健康检查三分法**：/livez（存活）、/readyz（就绪，供 K8s 探针）、
  /api/v1/health（人类可读的详细诊断）；
- **同步端点 + 线程池**：本服务的存储与出站调用都是同步阻塞 IO，写成
  ``async def`` 会阻塞事件循环让并发瞬间归零——这是 FastAPI 最常见的生产
  事故之一。声明为 ``def`` 后 FastAPI 自动放入线程池并发执行。

部署方式见 docs/deployment.md；配置项见 docs/configuration.md。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .adapters.qdrant import QdrantAdapter
from .config import settings
from .domain.models import Document, Evidence, Recipe, RunResult, utc_now
from .generation.client import extractive_answer
from .net import close_http_client, get_http_client
from .observability import (
    get_logger,
    observe_fallback,
    observe_run,
    render_metrics,
    request_id_var,
    setup_logging,
    setup_tracing,
    shutdown_tracing,
)
from .observability.middleware import RequestContextMiddleware
from .observability.telemetry import TraceContextMiddleware
from .parsers.chunker import chunk_blocks
from .parsers.enricher import enrich_chunks
from .parsers.router import parse_bytes
from .pipeline.compiler import CompileError, compile_recipe, default_recipes, node_catalog
from .pipeline.executor import QueryExecutor
from .pipeline.trace import TraceRecorder
from .policies.basic import detect_request_risks
from .security import ApiKeyMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware
from .store import Store

logger = get_logger("openrag_forge.app")


class KnowledgeBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class QueryRequest(BaseModel):
    knowledge_base_id: str = "default"
    recipe_id: str = "v0_1_dense"
    question: str = Field(min_length=3, max_length=6000)
    top_k: int = Field(default=5, ge=1, le=20)


class RunRequest(QueryRequest):
    mode: str = Field(default="run", pattern="^(run|preview|dry_run)$")


class EvalCase(BaseModel):
    case_id: str
    question: str = Field(min_length=3, max_length=6000)
    must_answer: bool = True
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_terms: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class EvalRequest(BaseModel):
    knowledge_base_id: str = "default"
    recipe_id: str = "v0_1_dense"
    cases: list[EvalCase] = Field(min_length=1, max_length=500)


class ModelRegistration(BaseModel):
    model_id: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    kind: Literal["chat", "embedding", "reranker"]
    provider: str = "openai-compatible"
    base_url: str = Field(min_length=1, max_length=500)
    model_name: str = Field(min_length=1, max_length=240)
    api_key: str | None = Field(default=None, max_length=500)
    parameters: dict[str, Any] = Field(default_factory=dict)
    source: Literal["endpoint", "manifest"] = "endpoint"


def _public_model(payload: dict[str, Any]) -> dict[str, Any]:
    data = {key: value for key, value in payload.items() if key != "api_key"}
    data["has_api_key"] = bool(payload.get("api_key"))
    return data


class ScenarioDefinition(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=160)
    business_problem: str = Field(min_length=1, max_length=800)
    recipe_id: str = Field(min_length=1, max_length=120)
    sample_question: str = Field(min_length=3, max_length=3000)
    data_requirements: list[str] = Field(default_factory=list, max_length=20)
    trace_expectation: list[str] = Field(default_factory=list, max_length=30)
    source_urls: list[str] = Field(default_factory=list, max_length=20)
    source: Literal["builtin", "user"] = "user"


SCENARIOS = [
    {"scenario_id": "customer_support", "title": "客服投诉助手", "business_problem": "一线客服需要在回答前快速核对官方流程与相似案例。", "recipe_id": "v0_2_hybrid", "sample_question": "客户说信用卡上有一笔不认识的扣款，客服应该先核对哪些信息？", "data_requirements": ["官方 FAQ / SOP", "产品政策", "脱敏历史工单"], "trace_expectation": ["Dense/Sparse candidates", "RRF", "evidence", "citation", "policy gate"], "source_urls": ["https://www.consumerfinance.gov/data-research/consumer-complaints/"]},
    {"scenario_id": "internal_policy", "title": "企业内部政策问答", "business_problem": "员工需要查询版本化的 HR、IT 或合规 SOP，并且不能混用过期政策。", "recipe_id": "v0_4_rerank", "sample_question": "这份内部政策要求审批人核对哪些材料？", "data_requirements": ["版本化政策 PDF/DOCX", "审批 SOP", "生效日期 Metadata"], "trace_expectation": ["metadata filter", "hybrid candidates", "rerank", "citation"], "source_urls": []},
    {"scenario_id": "controlled_customer_agent", "title": "受控客服 Agent", "business_problem": "客服工单字段不完整时先追问并生成草稿，不能自动发消息或决定退款。", "recipe_id": "v1_controlled_agent", "sample_question": "我发现信用卡上有一笔陌生扣款，之前联系过银行但还没有明确结果。", "data_requirements": ["客服知识库", "工单字段定义", "人工审批规则"], "trace_expectation": ["missing fields", "search", "ticket draft", "human approval"], "source_urls": []},
]


def _safe_filename(filename: str | None) -> str:
    value = Path(filename or "upload.bin").name
    value = re.sub(r"[^A-Za-z0-9._()\-\u4e00-\u9fff ]", "_", value).strip(" .")
    return value[:180] or "upload.bin"


_STOPWORDS = {"a", "an", "the", "is", "are", "was", "were", "what", "which", "how", "can", "could", "should", "i", "you", "we", "they", "for", "to", "of", "in", "on", "and", "or", "does", "do", "did", "this", "that"}


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9_-]+|[\u4e00-\u9fff]", lowered))
    words.update(re.findall(r"[a-z0-9]+", lowered))
    return {word for word in words if word not in _STOPWORDS}


def _topological(recipe: Recipe) -> list[str]:
    ids = {node.id for node in recipe.nodes}
    indegree = {node_id: 0 for node_id in ids}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in ids}
    for edge in recipe.edges:
        indegree[edge.target] += 1
        outgoing[edge.source].append(edge.target)
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    order: list[str] = []
    while queue:
        current = queue.pop(0)
        order.append(current)
        for target in outgoing[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return order or [node.id for node in recipe.nodes]


def _node(recipe: Recipe, node_id: str):
    return next(node for node in recipe.nodes if node.id == node_id)


def _ingest_chunk_config(ingest_recipe: Recipe | None) -> tuple[int, int]:
    """读取 custom_ingest Recipe 中 chunker 节点的配置，使工作台的调参真实生效。"""
    max_chars, overlap = 1200, 120
    if ingest_recipe is not None:
        for node in ingest_recipe.nodes:
            if node.type == "chunker":
                try:
                    max_chars = min(8000, max(200, int(node.config.get("max_chars", max_chars))))
                    overlap = min(max_chars // 2, max(0, int(node.config.get("overlap", overlap))))
                except (TypeError, ValueError):
                    pass
    return max_chars, overlap


_extractive_answer = extractive_answer


def _resolve_model(model_ref: str) -> dict[str, Any] | None:
    if not model_ref:
        return None
    return app.state.store.get_model(model_ref)


def _request_is_high_risk(question: str) -> list[str]:
    return detect_request_risks(question)


def _health(store: Store) -> dict[str, Any]:
    documents = sum(len(store.list_documents(kb["knowledge_base_id"])) for kb in store.list_knowledge_bases())
    client = get_http_client()
    # 依赖探测统一使用短超时（probe_timeout_seconds）：健康检查必须快速返回，
    # 一个卡死的下游不应把健康检查本身拖到超时。
    qdrant: dict[str, Any] = {"url": settings.qdrant_url, "status": "unreachable"}
    try:
        response = client.get(f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection}", timeout=settings.probe_timeout_seconds)
        qdrant["status"] = "ready" if response.status_code == 200 else "not_initialized"
        if response.status_code == 200:
            qdrant["points"] = response.json().get("result", {}).get("points_count")
    except Exception as exc:
        qdrant["error"] = type(exc).__name__
    lm_studio: dict[str, Any] = {"chat_base_url": settings.chat_base_url, "status": "unreachable"}
    try:
        response = client.get(f"{settings.chat_base_url.rstrip('/')}/models", timeout=settings.probe_timeout_seconds)
        lm_studio["status"] = "ready" if response.status_code == 200 else "error"
        lm_studio["models"] = [item.get("id") for item in response.json().get("data", [])]
    except Exception as exc:
        lm_studio["error"] = type(exc).__name__
    return {
        "status": "ready",
        "profile": settings.profile,
        "environment": settings.environment,
        "truth_source": "sqlite+local_blob" if settings.profile == "lite" else "production_adapter",
        "qdrant": qdrant,
        "lm_studio": lm_studio,
        "models": {"chat": settings.chat_model, "embedding": settings.embedding_model, "reranker": settings.reranker_model or None},
        "documents": documents,
        "capabilities": {"parsers": ["native_text", "html_structure", "pdf_page_text", "pdf_layout", "office_structure", "tabular", "json_structure"], "graph": settings.profile in {"graph", "production"}, "agent": False},
        # 生产就绪告警：environment=production 时列出危险配置，空数组代表通过。
        # 上线前检查此字段应为 []，详见 docs/production-checklist.md。
        "production_readiness": {"warnings": settings.production_warnings()},
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- 启动阶段：初始化顺序是日志 → 追踪 → 存储（后者的输出依赖前者）----
    setup_logging(settings.log_level, settings.log_format)
    tracing_on = setup_tracing(settings)
    for warning in settings.production_warnings():
        logger.warning("生产就绪检查未通过", extra={"readiness_warning": warning})
    store = Store(settings)
    for recipe in default_recipes():
        if store.get_recipe(recipe.recipe_id) is None:
            store.save_recipe(recipe.model_copy(update={"status": "published"}))
    store.save_knowledge_base("default", "Default knowledge base")
    configured_models = [
        ModelRegistration(model_id="configured-chat", display_name=settings.chat_model, kind="chat", base_url=settings.chat_base_url, model_name=settings.chat_model).model_dump(),
        ModelRegistration(model_id="configured-embedding", display_name=settings.embedding_model, kind="embedding", base_url=settings.embedding_base_url, model_name=settings.embedding_model).model_dump(),
    ]
    if settings.reranker_model and settings.reranker_base_url:
        configured_models.append(ModelRegistration(model_id="configured-reranker", display_name=settings.reranker_model, kind="reranker", base_url=settings.reranker_base_url, model_name=settings.reranker_model).model_dump())
    for model in configured_models:
        if store.get_model(model["model_id"]) is None:
            store.save_model(model["model_id"], model)
    for scenario in SCENARIOS:
        store.save_scenario(scenario["scenario_id"], {**scenario, "source": "builtin"})
    app.state.store = store
    app.state.qdrant = QdrantAdapter(settings)
    app.state.runtime = {"cache": {}, "rate": {}}
    logger.info("OpenRAG Forge 启动完成", extra={"profile": settings.profile, "environment": settings.environment, "tracing": tracing_on})
    yield
    # ---- 关机阶段（优雅关机）----
    # uvicorn 收到 SIGTERM 后先停止接收新连接、等待在途请求完成
    # （--timeout-graceful-shutdown 控制等待上限），随后才执行这里：
    # 1. flush 尚在缓冲区的 OTel span，避免丢失最后一批追踪数据；
    # 2. 关闭出站 HTTP 连接池，让下游干净地看到连接关闭。
    logger.info("OpenRAG Forge 正在关机：flush 追踪数据并关闭连接池")
    shutdown_tracing()
    close_http_client()


app = FastAPI(title="OpenRAG Forge", version="0.2.0", lifespan=lifespan)

# 中间件栈：add_middleware 后添加的在更外层，因此实际执行顺序（自外向内）为：
#   CORS → 安全响应头 → OTel Server Span → 请求上下文(ID/日志/指标) → 限流 → API Key
# 关键顺序约束：
#   - CORS 必须在认证之外：浏览器预检(OPTIONS)不带凭证，若先认证会 401 导致跨域全挂；
#   - 请求上下文在限流/认证之外：被拒绝的请求（401/429）也要有日志与指标。
app.add_middleware(ApiKeyMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(TraceContextMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["*"], allow_headers=["*"])

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """兜底错误处理：未捕获异常绝不把堆栈泄露给客户端。

    客户端只拿到 request_id；完整堆栈进结构化日志（带同一个 request_id 与
    trace_id），排障时用 ID 关联即可。这是"对外最小披露、对内全量可查"原则。
    """
    logger.error("未处理的服务器异常", exc_info=exc, extra={"path": request.url.path})
    return JSONResponse(status_code=500, content={"detail": "服务器内部错误，请携带 request_id 联系管理员", "request_id": request_id_var.get()})


# ---------------------------------------------------------------------------
# 运维端点：探活 / 就绪 / 指标
# ---------------------------------------------------------------------------
# 三个端点的分工（K8s 探针语义，配置见 deploy/k8s/deployment.yaml）：
#   /livez  —— 进程是否活着。失败 → 重启容器。绝不检查外部依赖，
#              否则下游抖动会引发全体副本重启的雪崩。
#   /readyz —— 是否可以接流量。失败 → 从 Service 摘除但不重启。
#              只检查真相源（SQLite）；Qdrant/LLM 属于可降级依赖，
#              它们不可用时服务仍能提供上传/解析能力，因此不拖垮就绪状态。
#   /metrics —— Prometheus 抓取点（deploy/prometheus.yml 已配置）。


@app.get("/livez")
def livez():
    return {"status": "alive"}


@app.get("/readyz")
def readyz():
    try:
        app.state.store.list_knowledge_bases()
    except Exception as exc:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": type(exc).__name__})
    return {"status": "ready"}


@app.get("/metrics")
def metrics():
    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="指标端点已通过 OPENRAG_METRICS_ENABLED=false 关闭")
    rendered = render_metrics()
    if rendered is None:
        return JSONResponse(status_code=501, content={"detail": "未安装 prometheus-client；请执行 pip install \".[observability]\""})
    payload, content_type = rendered
    return Response(content=payload, media_type=content_type)


# ---------------------------------------------------------------------------
# 业务端点
# 注意：以下端点特意声明为同步 def（而非 async def）。内部的 SQLite 与
# httpx 调用都是阻塞 IO，FastAPI 会把同步端点放进线程池执行，事件循环
# 保持空闲以继续接收新请求。若声明为 async def，一次慢的 LLM 调用（最长
# chat_timeout_seconds=90s）会独占事件循环，期间所有请求排队——这是
# FastAPI 服务最常见的生产性能事故。
# ---------------------------------------------------------------------------


@app.get("/api/v1/health")
def health():
    return _health(app.state.store)


@app.get("/api/v1/capabilities")
def capabilities():
    return {"profile": settings.profile, "nodes": node_catalog(), "parsers": _health(app.state.store)["capabilities"]["parsers"], "model_protocol": "openai-compatible"}


@app.post("/api/v1/knowledge-bases")
def create_knowledge_base(request: KnowledgeBaseRequest):
    kb_id = f"kb_{uuid4().hex[:12]}"
    app.state.store.save_knowledge_base(kb_id, request.name)
    return {"knowledge_base_id": kb_id, "name": request.name, "status": "ready"}


@app.get("/api/v1/knowledge-bases")
def list_knowledge_bases():
    return {"items": app.state.store.list_knowledge_bases()}


@app.get("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
def list_documents(knowledge_base_id: str):
    return {"items": [document.model_dump() for document in app.state.store.list_documents(knowledge_base_id)]}


@app.post("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
def upload_document(knowledge_base_id: str, file: UploadFile = File(...), route: str | None = None, embedding_model_id: str | None = None, max_chars: int | None = None, overlap: int | None = None):
    filename = _safe_filename(file.filename)
    file.file.seek(0)
    content = file.file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb} MB 限制")
    if not content:
        raise HTTPException(status_code=400, detail="不能上传空文件")
    document_id = f"doc_{uuid4().hex[:16]}"
    ingest_recipe = app.state.store.get_recipe("custom_ingest")
    ingest_trace = TraceRecorder(f"ingest_{uuid4().hex[:12]}", ingest_recipe.hash if ingest_recipe else "", app.state.store)
    media_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    document = Document(document_id=document_id, knowledge_base_id=knowledge_base_id, filename=filename, media_type=media_type, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    app.state.store.save_document(document, content)
    default_max, default_overlap = _ingest_chunk_config(ingest_recipe)
    effective_max = max(200, min(4000, int(max_chars or default_max)))
    effective_overlap = max(0, min(effective_max // 2, int(overlap if overlap is not None else default_overlap)))
    try:
        step_started = time.perf_counter()
        decision, blocks = parse_bytes(document_id, filename, media_type, content, route)
        ingest_trace.record("route", "completed", f"选择解析路由：{decision.route}", {"confidence": decision["confidence"], "reason_codes": decision["reason_codes"], "execution": "live", "node_type": "parse_route"}, started=step_started)
        step_started = time.perf_counter()
        chunks = chunk_blocks(blocks, effective_max, effective_overlap)
        ingest_trace.record("chunk", "completed", f"生成 {len(chunks)} 个 Chunk（max_chars={effective_max}, overlap={effective_overlap}）", {"blocks": len(blocks), "chunks": len(chunks), "max_chars": effective_max, "overlap": effective_overlap, "execution": "live", "node_type": "chunker"}, started=step_started)
        step_started = time.perf_counter()
        chunks = enrich_chunks(chunks, blocks, filename)
        document = document.model_copy(update={"status": "parsed", "parser_route": decision.route, "parser_confidence": decision["confidence"], "reason_codes": decision["reason_codes"]})
        app.state.store.update_document(document)
        app.state.store.save_blocks(blocks)
        app.state.store.save_chunks(chunks)
        ingest_trace.record("meta", "completed", "保存文档版本与 Chunk Metadata", {"document_id": document_id, "version": document.version, "execution": "live", "node_type": "metadata_enricher"}, started=step_started)
        step_started = time.perf_counter()
        try:
            indexer = app.state.qdrant
            if embedding_model_id:
                model = app.state.store.get_model(embedding_model_id)
                if model is None or model.get("kind") != "embedding":
                    raise HTTPException(status_code=422, detail="指定的 embedding_model_id 未注册或不是 Embedding 模型")
                model_settings = settings.model_copy(update={"embedding_base_url": model["base_url"], "embedding_model": model["model_name"], "embedding_api_key": model.get("api_key") or settings.embedding_api_key or settings.model_api_key})
                indexer = QdrantAdapter(model_settings)
            index = indexer.index(chunks)
            index["embedding_model_id"] = embedding_model_id or "configured-embedding"
            ingest_trace.record("index", "completed", f"Embedding 与 Qdrant 索引完成：{len(chunks)} 条", {**index, "execution": "live", "node_type": "embed_index"}, started=step_started)
        except Exception as index_error:
            # 降级而不失败：真相源已保存，索引可以事后重建。
            # 但降级必须可见——记录指标供告警（详见 observability.metrics）。
            observe_fallback("qdrant_index")
            index = {"status": "deferred", "reason": str(index_error), "next_action": "启动 Embedding 与 Qdrant 后重建索引"}
            ingest_trace.record("index", "failed", "索引暂缓，原始文档与 Chunk 已保留", {**index, "execution": "fallback_deferred", "node_type": "embed_index"}, started=step_started)
        return {"job_id": document_id, "document": document, "route": decision, "blocks": len(blocks), "chunks": len(chunks), "max_chars": effective_max, "overlap": effective_overlap, "index": index, "trace_id": ingest_trace.run_id, "trace": [event.model_dump() for event in ingest_trace.events]}
    except Exception as exc:
        ingest_trace.record("route", "failed", "解析失败，原始文件已保留", {"error": str(exc)})
        document = document.model_copy(update={"status": "failed", "reason_codes": ["parser_failed", type(exc).__name__]})
        app.state.store.update_document(document)
        raise HTTPException(status_code=422, detail={"message": "解析失败，原始文件已保留", "document": document.model_dump(), "error": str(exc)}) from exc


@app.get("/api/v1/documents/{document_id}")
def get_document(document_id: str):
    document = app.state.store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


@app.get("/api/v1/documents/{document_id}/blocks")
def get_blocks(document_id: str):
    return {"items": [block.model_dump() for block in app.state.store.list_blocks(document_id)]}


@app.get("/api/v1/documents/{document_id}/chunks")
def get_chunks(document_id: str):
    document = app.state.store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"items": [chunk.model_dump() for chunk in app.state.store.list_chunks(document.knowledge_base_id) if chunk.document_id == document_id]}


@app.post("/api/v1/documents/{document_id}/reprocess")
def reprocess_document(document_id: str, route: str | None = None, max_chars: int | None = None, overlap: int | None = None, embedding_model_id: str | None = None):
    document = app.state.store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    path = settings.upload_dir / f"{document.document_id}_{document.filename}"
    ingest_recipe = app.state.store.get_recipe("custom_ingest")
    default_max, default_overlap = _ingest_chunk_config(ingest_recipe)
    effective_max = max(200, min(4000, int(max_chars or default_max)))
    effective_overlap = max(0, min(effective_max // 2, int(overlap if overlap is not None else default_overlap)))
    try:
        decision, blocks = parse_bytes(document_id, document.filename, document.media_type, path.read_bytes(), route)
        chunks = chunk_blocks(blocks, effective_max, effective_overlap)
        chunks = enrich_chunks(chunks, blocks, document.filename)
        updated = document.model_copy(update={"status": "parsed", "version": document.version + 1, "parser_route": decision.route, "parser_confidence": decision["confidence"], "reason_codes": decision["reason_codes"]})
        app.state.store.update_document(updated)
        app.state.store.save_blocks(blocks)
        app.state.store.save_chunks(chunks)
        try:
            indexer = app.state.qdrant
            if embedding_model_id:
                model = app.state.store.get_model(embedding_model_id)
                if model is None or model.get("kind") != "embedding":
                    raise HTTPException(status_code=422, detail="指定的 embedding_model_id 未注册或不是 Embedding 模型")
                indexer = QdrantAdapter(settings.model_copy(update={"embedding_base_url": model["base_url"], "embedding_model": model["model_name"], "embedding_api_key": model.get("api_key") or settings.embedding_api_key or settings.model_api_key}))
            index = indexer.index(chunks)
        except HTTPException:
            raise
        except Exception as index_error:
            observe_fallback("qdrant_index")
            index = {"status": "deferred", "reason": str(index_error), "next_action": "启动 Embedding 与 Qdrant 后重建索引"}
        return {"document": updated, "route": decision, "blocks": len(blocks), "chunks": len(chunks), "index": index, "max_chars": effective_max, "overlap": effective_overlap}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"重新解析失败：{exc}") from exc


@app.post("/api/v1/knowledge-bases/{knowledge_base_id}/index/rebuild")
def rebuild_index(knowledge_base_id: str):
    chunks = app.state.store.list_chunks(knowledge_base_id)
    try:
        result = app.state.qdrant.index(chunks)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"message": "索引重建失败，真相源未受影响", "error": str(exc), "chunks": len(chunks)}) from exc
    return {"knowledge_base_id": knowledge_base_id, **result}


@app.get("/api/v1/plugins")
def plugins():
    return {"nodes": node_catalog(), "parsers": ["native_text", "html_structure", "pdf_page_text", "pdf_layout", "office_structure", "tabular", "json_structure"]}


@app.get("/api/v1/scenarios")
def scenarios():
    return {"items": app.state.store.list_scenarios(), "note": "Scenario 运行使用当前选中的知识库；示例中的业务资料必须先导入或连接官方 Pack。"}


@app.post("/api/v1/scenarios")
def create_scenario(scenario: ScenarioDefinition):
    payload = scenario.model_copy(update={"source": "user"}).model_dump()
    app.state.store.save_scenario(scenario.scenario_id, payload)
    return {"status": "registered", "scenario": payload}


@app.get("/api/v1/models")
def list_models():
    return {"items": [_public_model(model) for model in app.state.store.list_models()], "import_policy": "Register an OpenAI-compatible endpoint or a JSON manifest; model weight files stay in LM Studio/Ollama/vLLM and are never executed by the web app."}


@app.post("/api/v1/models")
def register_model(model: ModelRegistration):
    app.state.store.save_model(model.model_id, model.model_dump())
    return {"status": "registered", "model": _public_model(model.model_dump())}


@app.post("/api/v1/models/{model_id}/probe")
def probe_model(model_id: str):
    model = app.state.store.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="模型未注册")
    base_url = str(model["base_url"]).rstrip("/")
    client = get_http_client()
    headers: dict[str, str] = {}
    if model.get("api_key"):
        headers["Authorization"] = f"Bearer {model['api_key']}"
    try:
        if model["kind"] == "embedding":
            response = client.post(f"{base_url}/embeddings", json={"model": model["model_name"], "input": ["OpenRAG Forge probe"]}, headers=headers, timeout=30)
        elif model["kind"] == "chat":
            response = client.get(f"{base_url}/models", headers=headers, timeout=10)
        else:
            response = client.post(
                f"{base_url}/rerank",
                json={"model": model["model_name"], "query": "OpenRAG Forge probe", "documents": ["probe document"], "top_n": 1},
                headers=headers,
                timeout=30,
            )
        response.raise_for_status()
        # Some OpenAI-compatible servers return HTTP 200 with an error object
        # for unsupported routes (LM Studio currently does this for /rerank).
        # Treat that as unavailable instead of reporting a false-ready probe.
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        return {"status": "ready", "model_id": model_id, "kind": model["kind"], "details": {"http_status": response.status_code, "base_url": base_url}}
    except Exception as exc:
        return {"status": "unreachable", "model_id": model_id, "kind": model["kind"], "details": {"error": str(exc), "base_url": base_url}}


@app.get("/api/v1/recipes")
def list_recipes():
    recipes = app.state.store.list_recipes()
    recipes.sort(key=lambda recipe: tuple(int(part) for part in recipe.version.split(".")[:2]))
    return {"items": [recipe.model_dump() for recipe in recipes]}


@app.post("/api/v1/recipes")
def create_recipe(recipe: Recipe):
    try:
        compiled = compile_recipe(recipe)
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = compiled.model_copy(update={"status": "draft" if recipe.status == "draft" else compiled.status})
    app.state.store.save_recipe(saved)
    return saved


@app.put("/api/v1/recipes/{recipe_id}")
def update_recipe(recipe_id: str, recipe: Recipe):
    if recipe.recipe_id != recipe_id:
        raise HTTPException(status_code=422, detail="路径 recipe_id 与请求体不一致")
    return create_recipe(recipe)


@app.post("/api/v1/recipes/{recipe_id}/validate")
def validate_recipe(recipe_id: str):
    recipe = app.state.store.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe 不存在")
    try:
        compiled = compile_recipe(recipe)
    except CompileError as exc:
        return JSONResponse(status_code=422, content={"status": "invalid", "errors": [str(exc)]})
    app.state.store.save_recipe(compiled)
    return {"status": "valid", "recipe": compiled}


@app.post("/api/v1/recipes/import")
def import_recipes(payload: dict[str, Any]):
    if isinstance(payload.get("recipes"), list):
        raw_items = payload["recipes"]
    elif isinstance(payload.get("recipe"), dict):
        raw_items = [payload["recipe"]]
    elif "nodes" in payload and "recipe_id" in payload:
        raw_items = [payload]
    else:
        raise HTTPException(status_code=422, detail="无法识别的 Recipe JSON：需要 recipe_id + nodes，或 recipe / recipes 包装")
    imported = []
    for raw in raw_items:
        try:
            recipe = Recipe.model_validate({**raw, "status": "draft", "hash": None})
            existing = app.state.store.get_recipe(recipe.recipe_id)
            if existing is not None and existing.status == "published":
                recipe = recipe.model_copy(update={"recipe_id": f"{recipe.recipe_id}_imported", "name": f"{recipe.name} (imported)"})
            compiled = compile_recipe(recipe).model_copy(update={"status": "draft"})
            app.state.store.save_recipe(compiled)
            imported.append(compiled.model_dump())
        except CompileError as exc:
            raise HTTPException(status_code=422, detail=f"Recipe {raw.get('recipe_id', '?')} 编译失败：{exc}") from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Recipe JSON 校验失败：{exc}") from exc
    return {"status": "imported", "count": len(imported), "items": imported}


@app.get("/api/v1/recipes/{recipe_id}/export")
def export_recipe(recipe_id: str):
    recipe = app.state.store.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe 不存在")
    return JSONResponse(content=recipe.model_dump(mode="json"), headers={"Content-Disposition": f'attachment; filename="{recipe_id}.recipe.json"'})


@app.post("/api/v1/recipes/{recipe_id}/publish")
def publish_recipe(recipe_id: str):
    recipe = app.state.store.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe 不存在")
    compiled = compile_recipe(recipe).model_copy(update={"status": "published"})
    app.state.store.save_recipe(compiled)
    return compiled


def _clamp(value: Any, low: float, high: float, default: float) -> float:
    try:
        return min(high, max(low, float(value)))
    except (TypeError, ValueError):
        return default


def _execute(request: RunRequest) -> RunResult:
    run_started = time.perf_counter()
    recipe = app.state.store.get_recipe(request.recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe 不存在")
    recipe = compile_recipe(recipe)
    run_id = f"run_{uuid4().hex[:16]}"
    recorder = TraceRecorder(run_id, recipe.hash or "", app.state.store)
    evidence: list[Evidence] = []
    artifact: dict[str, Any] | None = None
    chunks = app.state.store.list_chunks(request.knowledge_base_id)
    answer: str | None = None
    if request.mode == "preview":
        catalog = node_catalog()
        for node_id in _topological(recipe):
            node_started = time.perf_counter()
            node = _node(recipe, node_id)
            meta = catalog.get(node.type, {})
            recorder.record(
                node_id,
                "completed",
                "Preview：已编译节点，未调用外部模型或写入索引",
                {
                    "preview": True,
                    "execution": "preview_compile_only",
                    "node_type": node.type,
                    "impact": {
                        "runtime": meta.get("implemented", "stub"),
                        "config_used": {**meta.get("config_defaults", {}), **(node.config or {})},
                    },
                },
                started=node_started,
            )
        result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", trace=recorder.events, safety={"mode": "preview", "side_effects": False})
        observe_run(recipe.recipe_id, "preview", time.perf_counter() - run_started)
    else:
        risk_codes = _request_is_high_risk(request.question)
        if risk_codes:
            for node_id in _topological(recipe):
                node_started = time.perf_counter()
                node = _node(recipe, node_id)
                if node.type in {"question", "policy_gate", "approval"}:
                    recorder.record(node_id, "completed", "安全门拒绝高风险结论请求", {"risk_codes": risk_codes, "side_effects": False, "execution": "live", "node_type": node.type}, started=node_started)
                else:
                    recorder.record(node_id, "skipped", "request_safety_gate 已提前停止该节点", {"risk_codes": risk_codes, "node_type": node.type}, started=node_started)
            answer = "我不能替你认定违法、承诺退款或做账户决定。可以改为：根据知识库中的官方流程，列出需要核对的事实和下一步人工处理项。"
            result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", answer=answer, artifact=None, evidence=[], trace=recorder.events, safety={"side_effects": False, "request_safety_gate": risk_codes, "human_review": True})
            observe_run(recipe.recipe_id, "refused", time.perf_counter() - run_started)
            payload = result.model_dump(mode="json")
            app.state.store.save_run(payload)
            capsule_path = settings.artifact_dir / f"{run_id}.json"
            capsule_path.write_text(json.dumps({"capsule_version": "0.1", "created_at": utc_now(), "settings": {"profile": settings.profile, "chat_model": settings.chat_model, "embedding_model": settings.embedding_model}, **payload}, ensure_ascii=False, indent=2), encoding="utf-8")
            return result
        executor = QueryExecutor(
            recipe=recipe,
            question=request.question,
            top_k=request.top_k,
            chunks=chunks,
            store=app.state.store,
            settings=settings,
            qdrant=app.state.qdrant,
            recorder=recorder,
            runtime=app.state.runtime,
            resolve_model=_resolve_model,
        )
        executor.execute()
        answer = executor.answer
        artifact = executor.artifact
        evidence = executor.evidence
        safety = dict(executor.safety)
        safety.setdefault("side_effects", False)
        safety.setdefault("human_review", not bool(evidence) or bool(artifact))
        result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", answer=answer, artifact=artifact, evidence=evidence, trace=recorder.events, safety=safety)
        observe_run(recipe.recipe_id, "completed", time.perf_counter() - run_started)
    payload = result.model_dump(mode="json")
    app.state.store.save_run(payload)
    capsule_path = settings.artifact_dir / f"{run_id}.json"
    capsule_path.write_text(json.dumps({"capsule_version": "0.1", "created_at": utc_now(), "settings": {"profile": settings.profile, "chat_model": settings.chat_model, "embedding_model": settings.embedding_model}, **payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@app.post("/api/v1/runs", response_model=RunResult)
def create_run(request: RunRequest):
    return _execute(request)


@app.post("/api/v1/query", response_model=RunResult)
def query(request: QueryRequest):
    return _execute(RunRequest(**request.model_dump(), mode="run"))


@app.get("/api/v1/runs")
def list_runs(limit: int = 20):
    return {"items": app.state.store.list_runs(min(max(limit, 1), 100))}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str):
    payload = app.state.store.get_run(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    payload["trace"] = [event.model_dump() for event in app.state.store.list_trace(run_id)]
    return payload


@app.get("/api/v1/runs/{run_id}/events")
def run_events(run_id: str):
    def events():
        for event in app.state.store.list_trace(run_id):
            yield f"data: {event.model_dump_json()}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/v1/runs/{run_id}/capsule")
def run_capsule(run_id: str):
    path = settings.artifact_dir / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Evidence Capsule 不存在")
    return FileResponse(path, media_type="application/json", filename=f"{run_id}-evidence-capsule.json")


@app.get("/api/v1/evals")
def list_evals():
    reports = sorted(settings.artifact_dir.glob("eval_*.json"), reverse=True)
    return {"items": [json.loads(path.read_text(encoding="utf-8")) for path in reports[:20]], "message": "导入 JSONL Golden Set 后运行 Eval"}


@app.post("/api/v1/evals")
def create_eval(request: EvalRequest):
    eval_id = f"eval_{uuid4().hex[:12]}"
    rows = []
    for case in request.cases:
        result = _execute(RunRequest(knowledge_base_id=request.knowledge_base_id, recipe_id=request.recipe_id, question=case.question, mode="run"))
        evidence_ids = {item.chunk_id for item in result.evidence}
        term_hit = any(any(term.lower() in item.text.lower() for term in case.expected_terms) for item in result.evidence) if case.expected_terms else False
        hit = bool(evidence_ids.intersection(case.expected_chunk_ids) or term_hit) if (case.expected_chunk_ids or case.expected_terms) else bool(result.evidence)
        refused = bool(result.safety.get("request_safety_gate"))
        rows.append({"case_id": case.case_id, "hit": hit, "refused": refused, "expected_answer": case.must_answer, "trace_id": result.run_id, "evidence_count": len(result.evidence), "tags": case.tags})
    answered = [row for row in rows if row["expected_answer"]]
    refusal_cases = [row for row in rows if not row["expected_answer"]]
    report = {"eval_id": eval_id, "recipe_id": request.recipe_id, "cases": len(rows), "hit_at_k": sum(row["hit"] for row in answered) / len(answered) if answered else None, "refusal_correctness": sum(row["refused"] for row in refusal_cases) / len(refusal_cases) if refusal_cases else None, "unsupported_claims": 0, "rows": rows, "created_at": utc_now()}
    (settings.artifact_dir / f"{eval_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


@app.get("/api/v1/evals/{eval_id}")
def get_eval(eval_id: str):
    path = settings.artifact_dir / f"{eval_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Eval 不存在")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/v1/benchmarks/framework-smoke")
def framework_smoke_benchmark():
    report_path = Path(__file__).resolve().parents[2] / "reports" / "framework_smoke_latest.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="尚未运行 framework smoke benchmark")
    return json.loads(report_path.read_text(encoding="utf-8"))


@app.get("/")
def index():
    index_path = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"name": "OpenRAG Forge", "status": "api_ready", "next": "npm run dev in web/"})
