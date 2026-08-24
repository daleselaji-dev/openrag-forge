from __future__ import annotations

from fastapi.testclient import TestClient

from openrag_forge.app import app
from openrag_forge.config import settings
from openrag_forge.domain.models import Recipe, RecipeEdge, RecipeNode
from openrag_forge.parsers.router import ParserRouter, parse_bytes
from openrag_forge.pipeline.compiler import CompileError, compile_recipe


def test_parser_router_detects_pdf_signature():
    decision = ParserRouter().decide("notes.txt", "text/plain", b"%PDF-1.7 fake")
    assert decision.route == "pdf_page_text"


def test_markdown_parser_returns_blocks():
    decision, blocks = parse_bytes("doc_1", "notes.md", "text/markdown", b"# Heading\n\nA paragraph with evidence.")
    assert decision.route == "native_text"
    assert blocks and "Heading" in blocks[0].text


def test_recipe_compiler_rejects_cycle():
    recipe = Recipe(recipe_id="bad", name="cycle", nodes=[RecipeNode(id="a", type="question"), RecipeNode(id="b", type="cache")], edges=[RecipeEdge(source="a", source_port="query", target="b", target_port="query"), RecipeEdge(source="b", source_port="query", target="a", target_port="query")])
    try:
        compile_recipe(recipe)
    except CompileError as exc:
        assert "环" in str(exc)
    else:
        raise AssertionError("cycle was accepted")


def test_api_upload_preview_and_capsule(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 200
        upload = client.post("/api/v1/knowledge-bases/default/documents", files={"file": ("faq.md", b"A support answer.", "text/markdown")})
        assert upload.status_code == 200
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v1_controlled_agent", "question": "What is the support answer?", "mode": "preview"})
        assert run.status_code == 200
        payload = run.json()
        assert payload["safety"]["side_effects"] is False
        assert len(payload["trace"]) == 4
        assert client.get(f"/api/v1/runs/{payload['run_id']}/capsule").status_code == 200
        risky = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v1_controlled_agent", "question": "Can you guarantee I will get a refund?", "mode": "run"})
        assert risky.status_code == 200
        assert risky.json()["safety"]["request_safety_gate"] == ["refund_promise"]
        evaluation = client.post("/api/v1/evals", json={"knowledge_base_id": "default", "recipe_id": "v0_1_dense", "cases": [{"case_id": "answer", "question": "What is the support answer?", "must_answer": True, "expected_terms": ["support"]}, {"case_id": "risk", "question": "Can you guarantee a refund?", "must_answer": False}]})
        assert evaluation.status_code == 200
        assert evaluation.json()["refusal_correctness"] == 1.0
