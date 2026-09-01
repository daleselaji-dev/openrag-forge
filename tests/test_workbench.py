"""工作台支撑能力回归测试：诚实标注、真实耗时、节点配置生效。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openrag_forge.app import app
from openrag_forge.config import settings


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def test_node_catalog_carries_honest_metadata():
    with TestClient(app) as client:
        nodes = client.get("/api/v1/plugins").json()["nodes"]
        for node_type, spec in nodes.items():
            assert spec["implemented"] in {"live", "fallback", "stub"}, node_type
            assert spec["title"], node_type
            assert isinstance(spec["config_schema"], list), node_type
        assert nodes["dense_retrieve"]["implemented"] == "live"
        assert nodes["llm_generate"]["implemented"] == "live"
        assert nodes["sparse_retrieve"]["implemented"] == "live"
        assert nodes["graph_query"]["implemented"] == "stub"
        assert nodes["reranker"]["implemented"] == "fallback"
        assert nodes["rrf_fusion"]["implemented"] == "live"
        assert nodes["context_builder"]["implemented"] == "live"
        dense_fields = {field["key"]: field for field in nodes["dense_retrieve"]["config_schema"]}
        assert dense_fields["top_k"]["effective"] is True


def test_run_trace_records_real_durations_and_execution_labels():
    with TestClient(app) as client:
        upload = client.post("/api/v1/knowledge-bases/default/documents", files={"file": ("faq.md", b"# Support\n\nThe support workflow requires verification of account details.", "text/markdown")})
        assert upload.status_code == 200
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v0_4_rerank", "question": "What does the support workflow require?", "mode": "run"})
        assert run.status_code == 200
        trace = run.json()["trace"]
        assert trace
        for event in trace:
            assert event["duration_ms"] >= 0
            assert "node_type" in event["details"], event["node_id"]
            assert "execution" in event["details"], event["node_id"]
            assert "impact" in event["details"], event["node_id"]
        assert any(event["duration_ms"] > 0 for event in trace)
        dense_event = next(event for event in trace if event["node_id"] == "d")
        assert dense_event["details"]["impact"]["backend"] in {"qdrant_dense", "lexical_fallback"}
        rerank_event = next(event for event in trace if event["node_id"] == "r")
        assert rerank_event["details"]["impact"]["backend"] in {"passthrough", "openai_compatible_rerank"}


def test_preview_trace_is_labeled_compile_only():
    with TestClient(app) as client:
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v0_2_hybrid", "question": "What is documented here?", "mode": "preview"})
        assert run.status_code == 200
        for event in run.json()["trace"]:
            assert event["details"]["execution"] == "preview_compile_only"
            assert event["details"]["preview"] is True


def test_dense_node_top_k_config_takes_effect():
    with TestClient(app) as client:
        for index in range(3):
            upload = client.post("/api/v1/knowledge-bases/default/documents", files={"file": (f"faq{index}.md", f"# Refund process {index}\n\nThe support workflow step {index} explains verification.".encode(), "text/markdown")})
            assert upload.status_code == 200
        dense = next(recipe for recipe in client.get("/api/v1/recipes").json()["items"] if recipe["recipe_id"] == "v0_1_dense")
        draft = {**dense, "recipe_id": "draft_topk", "name": "topk draft", "status": "draft", "hash": None}
        draft["nodes"] = [{**node, "config": {"top_k": 1}} if node["type"] == "dense_retrieve" else node for node in dense["nodes"]]
        assert client.post("/api/v1/recipes", json=draft).status_code == 200
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "draft_topk", "question": "What does the support workflow explain?", "mode": "run", "top_k": 5})
        assert run.status_code == 200
        payload = run.json()
        assert len(payload["evidence"]) == 1
        dense_event = next(event for event in payload["trace"] if event["node_id"] == "d")
        assert dense_event["details"]["top_k"] == 1


def test_llm_generation_params_recorded_in_trace():
    with TestClient(app) as client:
        upload = client.post("/api/v1/knowledge-bases/default/documents", files={"file": ("faq.md", b"The support answer explains verification.", "text/markdown")})
        assert upload.status_code == 200
        dense = next(recipe for recipe in client.get("/api/v1/recipes").json()["items"] if recipe["recipe_id"] == "v0_1_dense")
        draft = {**dense, "recipe_id": "draft_gen", "name": "gen draft", "status": "draft", "hash": None}
        draft["nodes"] = [{**node, "config": {"temperature": 0.7, "max_tokens": 256}} if node["type"] == "llm_generate" else node for node in dense["nodes"]]
        assert client.post("/api/v1/recipes", json=draft).status_code == 200
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "draft_gen", "question": "What does the support answer explain?", "mode": "run"})
        payload = run.json()
        llm_event = next(event for event in payload["trace"] if event["node_id"] == "g")
        assert llm_event["details"]["temperature"] == 0.7
        assert llm_event["details"]["max_tokens"] == 256
        assert llm_event["details"]["execution"] in {"live", "fallback_extractive"}


def test_ingest_chunker_config_takes_effect():
    with TestClient(app) as client:
        ingest = next(recipe for recipe in client.get("/api/v1/recipes").json()["items"] if recipe["recipe_id"] == "custom_ingest")
        ingest["nodes"] = [{**node, "config": {"max_chars": 250, "overlap": 0}} if node["type"] == "chunker" else node for node in ingest["nodes"]]
        assert client.put("/api/v1/recipes/custom_ingest", json=ingest).status_code == 200
        body = client.post("/api/v1/knowledge-bases/default/documents", files={"file": ("long.md", b"word " * 300, "text/markdown")}).json()
        chunk_event = next(event for event in body["trace"] if event["node_id"] == "chunk")
        assert chunk_event["details"]["max_chars"] == 250
        assert chunk_event["details"]["overlap"] == 0
