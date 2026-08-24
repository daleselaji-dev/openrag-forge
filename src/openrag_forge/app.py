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

import httpx
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import settings
from .adapters.qdrant import QdrantAdapter
from .domain.models import Chunk, Document, Evidence, Recipe, RunResult, utc_now
from .generation.client import generate_grounded_answer
from .parsers.chunker import chunk_blocks
from .parsers.router import parse_bytes
from .policies.basic import detect_request_risks
from .pipeline.compiler import CompileError, compile_recipe, default_recipes, node_catalog
from .pipeline.trace import TraceRecorder
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
    parameters: dict[str, Any] = Field(default_factory=dict)
    source: Literal["endpoint", "manifest"] = "endpoint"


SCENARIOS = [
    {"scenario_id": "customer_support", "title": "客服投诉助手", "business_problem": "一线客服需要在回答前快速核对官方流程与相似案例。", "recipe_id": "v0_2_hybrid", "sample_question": "客户说信用卡上有一笔不认识的扣款，客服应该先核对哪些信息？", "data_requirements": ["官方 FAQ / SOP", "产品政策", "脱敏历史工单"], "trace_expectation": ["Dense/Sparse candidates", "RRF", "evidence", "citation", "policy gate"], "source_urls": ["https://www.consumerfinance.gov/data-research/consumer-complaints/"]},
    {"scenario_id": "internal_policy", "title": "企业内部政策问答", "business_problem": "员工需要查询版本化的 HR、IT 或合规 SOP，并且不能混用过期政策。", "recipe_id": "v0_4_rerank", "sample_question": "这份内部政策要求审批人核对哪些材料？", "data_requirements": ["版本化政策 PDF/DOCX", "审批 SOP", "生效日期 Metadata"], "trace_expectation": ["metadata filter", "hybrid candidates", "rerank", "citation"], "source_urls": []},
    {"scenario_id": "controlled_customer_agent", "title": "受控客服 Agent", "business_problem": "客服工单字段不完整时先追问并生成草稿，不能自动发消息或决定退款。", "recipe_id": "v1_controlled_agent", "sample_question": "我发现信用卡上有一笔陌生扣款，之前联系过银行但还没有明确结果。", "data_requirements": ["客服知识库", "工单字段定义", "人工审批规则"], "trace_expectation": ["missing fields", "search", "ticket draft", "human approval"], "source_urls": []},
]


def _safe_filename(filename: str | None) -> str:
    value = Path(filename or "upload.bin").name
    value = re.sub(r"[^A-Za-z0-9._()\-\u4e00-\u9fff ]", "_", value).strip(" .")
    return value[:180] or "upload.bin"


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9_-]+|[\u4e00-\u9fff]", lowered))
    words.update(re.findall(r"[a-z0-9]+", lowered))
    return words


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


def _extractive_answer(question: str, evidence: list[Evidence]) -> str:
    if not evidence:
        return "当前知识库没有足够证据支持回答。请补充文档或改写问题。"
    lines = ["根据当前知识库检索到的证据："]
    for item in evidence[:3]:
        excerpt = item.text[:420].strip()
        lines.append(f"[{item.citation}] {excerpt}")
    lines.append("以上内容是文档证据摘要，不代表账户调查结果、退款承诺或法律结论。")
    return "\n".join(lines)


def _request_is_high_risk(question: str) -> list[str]:
    return detect_request_risks(question)


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
    app.state.store = store
    app.state.qdrant = QdrantAdapter(settings)
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


@app.post("/api/v1/knowledge-bases/{knowledge_base_id}/documents")
async def upload_document(knowledge_base_id: str, file: UploadFile = File(...), route: str | None = None, embedding_model_id: str | None = None):
    filename = _safe_filename(file.filename)
    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"文件超过 {settings.max_upload_mb} MB 限制")
    if not content:
        raise HTTPException(status_code=400, detail="不能上传空文件")
    document_id = f"doc_{uuid4().hex[:16]}"
    media_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    document = Document(document_id=document_id, knowledge_base_id=knowledge_base_id, filename=filename, media_type=media_type, size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    app.state.store.save_document(document, content)
    try:
        decision, blocks = parse_bytes(document_id, filename, media_type, content, route)
        chunks = chunk_blocks(blocks)
        document = document.model_copy(update={"status": "parsed", "parser_route": decision.route, "parser_confidence": decision["confidence"], "reason_codes": decision["reason_codes"]})
        app.state.store.update_document(document)
        app.state.store.save_blocks(blocks)
        app.state.store.save_chunks(chunks)
        try:
            indexer = app.state.qdrant
            if embedding_model_id:
                model = app.state.store.get_model(embedding_model_id)
                if model is None or model.get("kind") != "embedding":
                    raise HTTPException(status_code=422, detail="指定的 embedding_model_id 未注册或不是 Embedding 模型")
                model_settings = settings.model_copy(update={"embedding_base_url": model["base_url"], "embedding_model": model["model_name"]})
                indexer = QdrantAdapter(model_settings)
            index = indexer.index(chunks)
            index["embedding_model_id"] = embedding_model_id or "configured-embedding"
        except Exception as index_error:
            index = {"status": "deferred", "reason": str(index_error), "next_action": "启动 Embedding 与 Qdrant 后重建索引"}
        return {"job_id": document_id, "document": document, "route": decision, "blocks": len(blocks), "chunks": len(chunks), "index": index}
    except Exception as exc:
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
async def reprocess_document(document_id: str, route: str | None = None):
    document = app.state.store.get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    path = settings.upload_dir / f"{document.document_id}_{document.filename}"
    try:
        decision, blocks = parse_bytes(document_id, document.filename, document.media_type, path.read_bytes(), route)
        chunks = chunk_blocks(blocks)
        updated = document.model_copy(update={"status": "parsed", "version": document.version + 1, "parser_route": decision.route, "parser_confidence": decision["confidence"], "reason_codes": decision["reason_codes"]})
        app.state.store.update_document(updated)
        app.state.store.save_blocks(blocks)
        app.state.store.save_chunks(chunks)
        try:
            index = app.state.qdrant.index(chunks)
        except Exception as index_error:
            index = {"status": "deferred", "reason": str(index_error), "next_action": "启动 Embedding 与 Qdrant 后重建索引"}
        return {"document": updated, "route": decision, "blocks": len(blocks), "chunks": len(chunks), "index": index}
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
    return {"items": SCENARIOS, "note": "Scenario 运行使用当前选中的知识库；示例中的业务资料必须先导入或连接官方 Pack。"}


@app.get("/api/v1/models")
async def list_models():
    return {"items": app.state.store.list_models(), "import_policy": "Register an OpenAI-compatible endpoint or a JSON manifest; model weight files stay in LM Studio/Ollama/vLLM and are never executed by the web app."}


@app.post("/api/v1/models")
async def register_model(model: ModelRegistration):
    app.state.store.save_model(model.model_id, model.model_dump())
    return {"status": "registered", "model": model}


@app.post("/api/v1/models/{model_id}/probe")
async def probe_model(model_id: str):
    model = app.state.store.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="模型未注册")
    base_url = str(model["base_url"]).rstrip("/")
    try:
        if model["kind"] == "embedding":
            response = httpx.post(f"{base_url}/embeddings", json={"model": model["model_name"], "input": ["OpenRAG Forge probe"]}, timeout=30)
        elif model["kind"] == "chat":
            response = httpx.get(f"{base_url}/models", timeout=10)
        else:
            response = httpx.get(f"{base_url}/models", timeout=10)
        response.raise_for_status()
        return {"status": "ready", "model_id": model_id, "kind": model["kind"], "details": {"http_status": response.status_code, "base_url": base_url}}
    except Exception as exc:
        return {"status": "unreachable", "model_id": model_id, "kind": model["kind"], "details": {"error": str(exc), "base_url": base_url}}


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
        for node_id in _topological(recipe):
            recorder.record(node_id, "completed", "Preview：已编译节点，未调用外部模型或写入索引", {"preview": True})
        result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", trace=recorder.events, safety={"mode": "preview", "side_effects": False})
    else:
        query_tokens = _tokens(request.question)
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
        qdrant_hits: list[dict[str, Any]] = []
        try:
            if chunks:
                qdrant_hits = app.state.qdrant.search(request.question, request.top_k)
        except Exception:
            qdrant_hits = []
        for node_id in _topological(recipe):
            node = _node(recipe, node_id)
            if node.type in {"dense_retrieve", "sparse_retrieve", "graph_query", "pdf_page_retrieve"}:
                if qdrant_hits:
                    evidence = [Evidence(citation=f"S{index + 1}", chunk_id=str(hit.get("payload", {}).get("chunk_id", hit.get("id"))), document_id=str(hit.get("payload", {}).get("document_id", "")), title=str(hit.get("payload", {}).get("title", hit.get("payload", {}).get("document_id", ""))), text=str(hit.get("payload", {}).get("text", "")), score=round(float(hit.get("score", 0.0)), 4), metadata=hit.get("payload", {})) for index, hit in enumerate(qdrant_hits[:request.top_k])]
                    backend = "qdrant_dense"
                else:
                    scored = []
                    for chunk in chunks:
                        overlap = len(query_tokens & _tokens(chunk.text))
                        if overlap:
                            scored.append((overlap / max(1, len(query_tokens)), chunk))
                    scored.sort(key=lambda item: item[0], reverse=True)
                    evidence = [Evidence(citation=f"S{index + 1}", chunk_id=chunk.chunk_id, document_id=chunk.document_id, title=chunk.metadata.get("title") or chunk.document_id, text=chunk.text, score=round(score, 4), metadata=chunk.metadata) for index, (score, chunk) in enumerate(scored[: request.top_k])]
                    backend = "lexical_fallback"
                recorder.record(node_id, "completed", f"召回 {len(evidence)} 条候选", {"candidate_count": len(evidence), "backend": backend})
            elif node.type in {"context_builder", "reranker", "rrf_fusion"}:
                recorder.record(node_id, "completed", f"处理 {len(evidence)} 条证据", {"evidence_count": len(evidence)})
            elif node.type == "llm_generate":
                answer, provider = generate_grounded_answer(request.question, evidence, settings)
                recorder.record(node_id, "completed", "生成受证据约束的回答", {"provider": provider, "citation_count": len(evidence)})
            elif node.type == "policy_gate":
                recorder.record(node_id, "completed", "安全策略通过；未发现外部副作用动作", {"human_review": not bool(evidence)})
            elif node.type == "approval":
                recorder.record(node_id, "completed", "停在人工审批门", {"approval_required": True})
            elif node.type == "build_ticket_draft":
                normalized = request.question.lower()
                supplied = {"merchant": bool(re.search(r"merchant|商户", normalized)), "date": bool(re.search(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|日期|date", normalized)), "previous_actions": bool(re.search(r"contacted|联系过|此前|previous", normalized))}
                missing = [field for field, present in supplied.items() if not present]
                artifact = {"artifact_type": "ticket_draft", "status": "pending_human_approval", "fields": {"message": request.question, "merchant": None, "date": None, "previous_actions": None}, "missing_fields": missing, "evidence_ids": [item.citation for item in evidence], "forbidden_actions": ["send_customer_message", "write_external_crm", "promise_refund", "decide_legal_liability"]}
                answer = f"已生成客服工单草稿，仍缺少字段：{', '.join(missing) if missing else '无'}。草稿必须经过人工审批。"
                recorder.record(node_id, "completed", "生成结构化工单草稿，未执行外部动作", {"missing_fields": missing, "approval_required": True})
            else:
                recorder.record(node_id, "completed", f"{node.type} 已执行", {"node_type": node.type})
        result = RunResult(run_id=run_id, recipe_id=recipe.recipe_id, recipe_hash=recipe.hash or "", status="completed", answer=answer, artifact=artifact, evidence=evidence, trace=recorder.events, safety={"side_effects": False, "human_review": not bool(evidence) or bool(artifact)})
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


@app.get("/")
async def index():
    index_path = Path(__file__).resolve().parents[2] / "web" / "dist" / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse({"name": "OpenRAG Forge", "status": "api_ready", "next": "npm run dev in web/"})
