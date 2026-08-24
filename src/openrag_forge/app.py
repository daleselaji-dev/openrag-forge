from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .adapters.qdrant import QdrantAdapter
from .config import settings
from .domain.models import Document, Evidence, Recipe, RunResult, utc_now
from .generation.client import extractive_answer
from .parsers.chunker import chunk_blocks
from .parsers.enricher import enrich_chunks
from .parsers.router import parse_bytes
from .pipeline.compiler import CompileError, compile_recipe, default_recipes, node_catalog
from .pipeline.executor import QueryExecutor
from .pipeline.trace import TraceRecorder
from .policies.basic import detect_request_risks
from .store import Store


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
    # API key 只保存在服务端 SQLite，列表/详情接口一律脱敏为 has_api_key
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


# 兼容别名：抽取式降级回答的实现已移至 generation.client
_extractive_answer = extractive_answer


def _request_is_high_risk(question: str) -> list[str]:
    return detect_request_risks(question)


def _ingest_node_config(recipe: Recipe | None, node_type: str) -> tuple[str, dict[str, Any]]:
    """从 ingest Recipe 中取指定类型节点的 (node_id, 合并默认值后的 config)。"""
    defaults = node_catalog().get(node_type, {}).get("config_defaults", {})
    if recipe is not None:
        for node in recipe.nodes:
            if node.type == node_type:
                return node.id, {**defaults, **(node.config or {})}
    fallback_ids = {"parse_route": "route", "chunker": "chunk", "metadata_enricher": "meta", "embed_index": "index"}
    return fallback_ids.get(node_type, node_type), defaults


def _resolve_model(model_ref: str) -> dict[str, Any] | None:
    if not model_ref:
        return None
    return app.state.store.get_model(model_ref)


def _health(store: Store) -> dict[str, Any]:
    documents = sum(len(store.list_documents(kb["knowledge_base_id"])) for kb in store.list_knowledge_bases())
    qdrant: dict[str, Any] = {"url": settings.qdrant_url, "status": "unreachable"}
    try:
        response = httpx.get(f"{settings.qdrant_url.rstrip('/')}/collections/{settings.qdrant_collection}", timeout=2)
        qdrant["status"] = "ready" if response.status_code == 200 else "not_initialized"
        if response.status_code == 200:
            qdrant["points"] = response.json().get("result", {}).get("points_count")
    except Exception as exc:
        qdrant["error"] = type(exc).__name__
    lm_studio: dict[str, Any] = {"chat_base_url": settings.chat_base_url, "status": "unreachable"}
    try:
        response = httpx.get(f"{settings.chat_base_url.rstrip('/')}/models", timeout=2)
        lm_studio["status"] = "ready" if response.status_code == 200 else "error"
        lm_studio["models"] = [item.get("id") for item in response.json().get("data", [])]
    except Exception as exc:
        lm_studio["error"] = type(exc).__name__
    return {
        "status": "ready",
        "profile": settings.profile,
        "truth_source": "sqlite+local_blob" if settings.profile == "lite" else "production_adapter",
        "qdrant": qdrant,
        "lm_studio": lm_studio,
        "models": {"chat": settings.chat_model, "embedding": settings.embedding_model, "reranker": settings.reranker_model or None},
        "documents": documents,
        "capabilities": {"parsers": ["native_text", "html_structure", "pdf_page_text", "pdf_layout", "office_structure", "tabular", "json_structure"], "graph": settings.profile in {"graph", "production"}, "agent": False},
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    # 进程内运行时状态：cache（结果缓存）与 rate（滑动窗口限流），供 v0_9_operations 等生产信封节点使用
    app.state.runtime = {"cache": {}, "rate": {}}
    yield


app = FastAPI(title="OpenRAG Forge", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/api/v1/health")
async def health():
    return _health(app.state.store)


@app.get("/api/v1/capabilities")
async def capabilities():
    return {"profile": settings.profile, "nodes": node_catalog(), "parsers": _health(app.state.store)["capabilities"]["parsers"], "model_protocol": "openai-compatible"}


@app.post("/api/v1/knowledge-bases")
async def create_knowledge_base(request: KnowledgeBaseRequest):
    kb_id = f"kb_{uuid4().hex[:12]}"
    app.state.store.save_knowledge_base(kb_id, request.name)
    return {"knowledge_base_id": kb_id, "name": request.name, "status": "ready"}


@app.get("/api/v1/knowledge-bases")
async def list_knowledge_bases():
    return {"items": app.state.store.list_knowledge_bases()}


@app.get("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
async def list_documents(knowledge_base_id: str):
    return {"items": [document.model_dump() for document in app.state.store.list_documents(knowledge_base_id)]}


def _run_ingest(document: Document, content: bytes, route: str | None, embedding_model_id: str | None, ingest_recipe_id: str, max_chars: int | None, overlap: int | None, trace: TraceRecorder) -> dict[str, Any]:
    """执行 ingest 链路：Route → Chunk → Enrich → Index。配置来自 ingest Recipe 节点，显式参数可覆盖。"""
    store: Store = app.state.store
    ingest_recipe = store.get_recipe(ingest_recipe_id) or store.get_recipe("custom_ingest")
    route_node_id, route_config = _ingest_node_config(ingest_recipe, "parse_route")
    chunk_node_id, chunk_config = _ingest_node_config(ingest_recipe, "chunker")
    meta_node_id, meta_config = _ingest_node_config(ingest_recipe, "metadata_enricher")
    index_node_id, index_config = _ingest_node_config(ingest_recipe, "embed_index")
    effective_route = route or (str(route_config.get("route", "auto")) if str(route_config.get("route", "auto")) != "auto" else None)
    effective_max_chars = max(200, min(4000, int(max_chars or chunk_config.get("max_chars", 1200))))
    effective_overlap = max(0, min(400, int(overlap or chunk_config.get("overlap", 120))))
    decision, blocks = parse_bytes(document.document_id, document.filename, document.media_type, content, effective_route)
    trace.record(route_node_id, "completed", f"选择解析路由：{decision.route}", {"confidence": decision["confidence"], "reason_codes": decision["reason_codes"], "impact": {"route": decision.route, "blocks": len(blocks), "override": bool(effective_route)}})
    chunks = chunk_blocks(blocks, max_chars=effective_max_chars, overlap=effective_overlap)
    trace.record(chunk_node_id, "completed", f"生成 {len(chunks)} 个 Chunk（max_chars={effective_max_chars}, overlap={effective_overlap}）", {"blocks": len(blocks), "chunks": len(chunks), "impact": {"config_used": {"max_chars": effective_max_chars, "overlap": effective_overlap}, "chunks": len(chunks)}})
    keywords_top_k = int(meta_config.get("keywords_top_k", 5))
    chunks = enrich_chunks(chunks, blocks, document.filename, keywords_top_k=keywords_top_k)
    updated = document.model_copy(update={"status": "parsed", "parser_route": decision.route, "parser_confidence": decision["confidence"], "reason_codes": decision["reason_codes"]})
    store.update_document(updated)
    store.save_blocks(blocks)
    store.save_chunks(chunks)
    trace.record(meta_node_id, "completed", f"Metadata 增强并保存文档版本 v{updated.version}", {"document_id": document.document_id, "version": updated.version, "impact": {"keywords_top_k": keywords_top_k, "enriched_fields": ["title", "language", "keywords"], "chunks": len(chunks)}})
    effective_embedding = embedding_model_id or str(index_config.get("model_ref", "configured-embedding"))
    try:
        indexer = app.state.qdrant
        if effective_embedding and effective_embedding != "configured-embedding":
            model = store.get_model(effective_embedding)
            if model is None or model.get("kind") != "embedding":
                raise HTTPException(status_code=422, detail="指定的 embedding_model_id 未注册或不是 Embedding 模型")
            model_settings = settings.model_copy(update={"embedding_base_url": model["base_url"], "embedding_model": model["model_name"], "embedding_api_key": model.get("api_key") or ""})
            indexer = QdrantAdapter(model_settings)
        index = indexer.index(chunks)
        index["embedding_model_id"] = effective_embedding
        trace.record(index_node_id, "completed", f"Embedding 与 Qdrant 索引完成：{len(chunks)} 条", {**index, "impact": {"indexed": index.get("indexed", 0), "embedding_model_id": effective_embedding}})
    except HTTPException:
        raise
    except Exception as index_error:
        index = {"status": "deferred", "reason": str(index_error), "next_action": "启动 Embedding 与 Qdrant 后重建索引"}
        trace.record(index_node_id, "failed", "索引暂缓，原始文档与 Chunk 已保留", {**index, "impact": {"status": "deferred", "next_action": index["next_action"]}})
    return {"document": updated, "route": decision, "blocks": len(blocks), "chunks": len(chunks), "index": index}


@app.post("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
async def upload_document(knowledge_base_id: str, file: UploadFile = File(...), route: str | None = None, embedding_model_id: str | None = None, ingest_recipe_id: str = "custom_ingest", max_chars: int | None = None, overlap: int | None = None):
    filename = _safe_filename(file.filename)
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb} MB 限制")
    if not content:
        raise HTTPException(status_code=400, detail="不能上传空文件")
    document_id = f"doc_{uuid4().hex[:16]}"
    ingest_recipe = app.state.store.get_recipe(ingest_recipe_id) or app.state.store.get_recipe("custom_ingest")
    ingest_trace = TraceRecorder(f"ingest_{uuid4().hex[:12]}", ingest_recipe.hash if ingest_recipe else "", app.state.store)
    media_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    document = Document(document_id=document_id, knowledge_base_id=knowledge_base_id, filename=filename, media_type=media_type, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    app.state.store.save_document(document, content)
    try:
        result = _run_ingest(document, content, route or None, embedding_model_id, ingest_recipe_id, max_chars, overlap, ingest_trace)
        return {"job_id": document_id, **result, "trace_id": ingest_trace.run_id, "trace": [event.model_dump() for event in ingest_trace.events]}
    except HTTPException:
        raise
    except Exception as exc:
        ingest_trace.record("route", "failed", "解析失败，原始文件已保留", {"error": str(exc)})
        document = document.model_copy(update={"status": "failed", "reason_codes": ["parser_failed", type(exc).__name__]})
        app.state.store.update_document(document)
        raise HTTPException(status_code=422, detail={"message": "解析失败，原始文件已保留", "document": document.model_dump(), "error": str(exc)}) from exc


@app.get("/api/v1/documents/{document_id}")
async def get_document(document_id: str):
    document = app.state.store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


@app.get("/api/v1/documents/{document_id}/blocks")
async def get_blocks(document_id: str):
    return {"items": [block.model_dump() for block in app.state.store.list_blocks(document_id)]}


@app.get("/api/v1/documents/{document_id}/chunks")
async def get_chunks(document_id: str):
    document = app.state.store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"items": [chunk.model_dump() for chunk in app.state.store.list_chunks(document.knowledge_base_id) if chunk.document_id == document_id]}


@app.post("/api/v1/documents/{document_id}/reprocess")
async def reprocess_document(document_id: str, route: str | None = None, embedding_model_id: str | None = None, ingest_recipe_id: str = "custom_ingest", max_chars: int | None = None, overlap: int | None = None):
    """用不同的路由 / Chunker 配置重新解析。源文件永不覆盖，版本号 +1。"""
    document = app.state.store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    path = settings.upload_dir / f"{document.document_id}_{document.filename}"
    ingest_recipe = app.state.store.get_recipe(ingest_recipe_id) or app.state.store.get_recipe("custom_ingest")
    trace = TraceRecorder(f"ingest_{uuid4().hex[:12]}", ingest_recipe.hash if ingest_recipe else "", app.state.store)
    try:
        bumped = document.model_copy(update={"version": document.version + 1})
        result = _run_ingest(bumped, path.read_bytes(), route or None, embedding_model_id, ingest_recipe_id, max_chars, overlap, trace)
        return {**result, "trace_id": trace.run_id, "trace": [event.model_dump() for event in trace.events]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"重新解析失败：{exc}") from exc


@app.post("/api/v1/knowledge-bases/{knowledge_base_id}/index/rebuild")
async def rebuild_index(knowledge_base_id: str):
    chunks = app.state.store.list_chunks(knowledge_base_id)
    try:
        result = app.state.qdrant.index(chunks)
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"message": "索引重建失败，真相源未受影响", "error": str(exc), "chunks": len(chunks)}) from exc
    return {"knowledge_base_id": knowledge_base_id, **result}


@app.get("/api/v1/plugins")
async def plugins():
    return {"nodes": node_catalog(), "parsers": ["native_text", "html_structure", "pdf_page_text", "pdf_layout", "office_structure", "tabular", "json_structure"]}


@app.get("/api/v1/scenarios")
async def scenarios():
    return {"items": app.state.store.list_scenarios(), "note": "Scenario 运行使用当前选中的知识库；示例中的业务资料必须先导入或连接官方 Pack。"}


@app.post("/api/v1/scenarios")
async def create_scenario(scenario: ScenarioDefinition):
    payload = scenario.model_copy(update={"source": "user"}).model_dump()
    app.state.store.save_scenario(scenario.scenario_id, payload)
    return {"status": "registered", "scenario": payload}


@app.get("/api/v1/models")
async def list_models():
    return {"items": [_public_model(model) for model in app.state.store.list_models()], "import_policy": "Register an OpenAI-compatible endpoint or a JSON manifest; model weight files stay in LM Studio/Ollama/vLLM and are never executed by the web app."}


@app.post("/api/v1/models")
async def register_model(model: ModelRegistration):
    app.state.store.save_model(model.model_id, model.model_dump())
    return {"status": "registered", "model": _public_model(model.model_dump())}


@app.post("/api/v1/models/{model_id}/probe")
async def probe_model(model_id: str):
    model = app.state.store.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="模型未注册")
    base_url = str(model["base_url"]).rstrip("/")
    headers = {}
    if model.get("api_key"):
        headers["Authorization"] = f"Bearer {model['api_key']}"
    try:
        if model["kind"] == "embedding":
            response = httpx.post(f"{base_url}/embeddings", headers=headers, json={"model": model["model_name"], "input": ["OpenRAG Forge probe"]}, timeout=30)
        else:
            response = httpx.get(f"{base_url}/models", headers=headers, timeout=10)
        response.raise_for_status()
        return {"status": "ready", "model_id": model_id, "kind": model["kind"], "details": {"http_status": response.status_code, "base_url": base_url}}
    except Exception as exc:
        return {"status": "unreachable", "model_id": model_id, "kind": model["kind"], "details": {"error": str(exc), "base_url": base_url, "next_action": "确认模型服务已启动、base_url 可达、API key 正确后重试"}}


@app.get("/api/v1/recipes")
async def list_recipes():
    recipes = app.state.store.list_recipes()
    recipes.sort(key=lambda recipe: tuple(int(part) for part in recipe.version.split(".")[:2]))
    return {"items": [recipe.model_dump() for recipe in recipes]}


@app.post("/api/v1/recipes")
async def create_recipe(recipe: Recipe):
    try:
        compiled = compile_recipe(recipe)
    except CompileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    saved = compiled.model_copy(update={"status": "draft" if recipe.status == "draft" else compiled.status})
    app.state.store.save_recipe(saved)
    return saved


@app.put("/api/v1/recipes/{recipe_id}")
async def update_recipe(recipe_id: str, recipe: Recipe):
    if recipe.recipe_id != recipe_id:
        raise HTTPException(status_code=422, detail="路径 recipe_id 与请求体不一致")
    return await create_recipe(recipe)


@app.post("/api/v1/recipes/{recipe_id}/validate")
async def validate_recipe(recipe_id: str):
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
async def import_recipes(payload: dict[str, Any]):
    """导入 Recipe JSON：接受单个 Recipe、{"recipe": {...}} 或 {"recipes": [...]}。一律以 draft 状态编译入库。"""
    raw_items: list[dict[str, Any]]
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
async def export_recipe(recipe_id: str):
    recipe = app.state.store.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe 不存在")
    return JSONResponse(content=recipe.model_dump(mode="json"), headers={"Content-Disposition": f'attachment; filename="{recipe_id}.recipe.json"'})


@app.post("/api/v1/recipes/{recipe_id}/publish")
async def publish_recipe(recipe_id: str):
    recipe = app.state.store.get_recipe(recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe 不存在")
    compiled = compile_recipe(recipe).model_copy(update={"status": "published"})
    app.state.store.save_recipe(compiled)
    return compiled


async def _execute(request: RunRequest) -> RunResult:
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
            node = _node(recipe, node_id)
            runtime_status = catalog.get(node.type, {}).get("runtime", "stub")
            recorder.record(node_id, "completed", "Preview：已编译节点，未调用外部模型或写入索引", {"preview": True, "impact": {"node_type": node.type, "runtime": runtime_status, "config_used": {**catalog.get(node.type, {}).get("config_defaults", {}), **(node.config or {})}}})
        result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", trace=recorder.events, safety={"mode": "preview", "side_effects": False})
    else:
        risk_codes = _request_is_high_risk(request.question)
        if risk_codes:
            for node_id in _topological(recipe):
                node = _node(recipe, node_id)
                if node.type in {"question", "policy_gate", "approval"}:
                    recorder.record(node_id, "completed", "安全门拒绝高风险结论请求", {"risk_codes": risk_codes, "side_effects": False})
                else:
                    recorder.record(node_id, "skipped", "request_safety_gate 已提前停止该节点", {"risk_codes": risk_codes})
            answer = "我不能替你认定违法、承诺退款或做账户决定。可以改为：根据知识库中的官方流程，列出需要核对的事实和下一步人工处理项。"
            result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", answer=answer, artifact=None, evidence=[], trace=recorder.events, safety={"side_effects": False, "request_safety_gate": risk_codes, "human_review": True})
            payload = result.model_dump(mode="json")
            app.state.store.save_run(payload)
            capsule_path = settings.artifact_dir / f"{run_id}.json"
            capsule_path.write_text(json.dumps({"capsule_version": "0.1", "created_at": utc_now(), "settings": {"profile": settings.profile, "chat_model": settings.chat_model, "embedding_model": settings.embedding_model}, **payload}, ensure_ascii=False, indent=2), encoding="utf-8")
            return result
        executor = QueryExecutor(
            recipe=recipe, question=request.question, top_k=request.top_k, chunks=chunks,
            store=app.state.store, settings=settings, qdrant=app.state.qdrant,
            recorder=recorder, runtime=app.state.runtime, resolve_model=_resolve_model,
        )
        executor.execute()
        answer = executor.answer
        artifact = executor.artifact
        evidence = executor.evidence
        safety = dict(executor.safety)
        safety.setdefault("side_effects", False)
        safety.setdefault("human_review", not bool(evidence) or bool(artifact))
        result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", answer=answer, artifact=artifact, evidence=evidence, trace=recorder.events, safety=safety)
    payload = result.model_dump(mode="json")
    app.state.store.save_run(payload)
    capsule_path = settings.artifact_dir / f"{run_id}.json"
    capsule_path.write_text(json.dumps({"capsule_version": "0.1", "created_at": utc_now(), "settings": {"profile": settings.profile, "chat_model": settings.chat_model, "embedding_model": settings.embedding_model}, **payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


@app.post("/api/v1/runs", response_model=RunResult)
async def create_run(request: RunRequest):
    return await _execute(request)


@app.post("/api/v1/query", response_model=RunResult)
async def query(request: QueryRequest):
    return await _execute(RunRequest(**request.model_dump(), mode="run"))


@app.get("/api/v1/runs")
async def list_runs(limit: int = 20):
    return {"items": app.state.store.list_runs(min(max(limit, 1), 100))}


@app.get("/api/v1/runs/{run_id}")
async def get_run(run_id: str):
    payload = app.state.store.get_run(run_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="运行不存在")
    payload["trace"] = [event.model_dump() for event in app.state.store.list_trace(run_id)]
    return payload


@app.get("/api/v1/runs/{run_id}/events")
async def run_events(run_id: str):
    async def events():
        for event in app.state.store.list_trace(run_id):
            yield f"data: {event.model_dump_json()}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream")


@app.get("/api/v1/runs/{run_id}/capsule")
async def run_capsule(run_id: str):
    path = settings.artifact_dir / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Evidence Capsule 不存在")
    return FileResponse(path, media_type="application/json", filename=f"{run_id}-evidence-capsule.json")


@app.get("/api/v1/evals")
async def list_evals():
    reports = sorted(settings.artifact_dir.glob("eval_*.json"), reverse=True)
    return {"items": [json.loads(path.read_text(encoding="utf-8")) for path in reports[:20]], "message": "导入 JSONL Golden Set 后运行 Eval"}


@app.post("/api/v1/evals")
async def create_eval(request: EvalRequest):
    eval_id = f"eval_{uuid4().hex[:12]}"
    rows = []
    for case in request.cases:
        result = await _execute(RunRequest(knowledge_base_id=request.knowledge_base_id, recipe_id=request.recipe_id, question=case.question, mode="run"))
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
async def get_eval(eval_id: str):
    path = settings.artifact_dir / f"{eval_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Eval 不存在")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/v1/benchmarks/framework-smoke")
async def framework_smoke_benchmark():
    report_path = Path(__file__).resolve().parents[2] / "reports" / "framework_smoke_latest.json"
    if not report_path.exists():
        raise HTTPException(status_code=404, detail="尚未运行 framework smoke benchmark")
    return json.loads(report_path.read_text(encoding="utf-8"))


@app.get("/")
async def index():
    index_path = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"name": "OpenRAG Forge", "status": "api_ready", "next": "npm run dev in web/"})
