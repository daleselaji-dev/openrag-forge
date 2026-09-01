"""生产级工作台新增 API 与执行器行为的回归测试。

所有用例在完全离线（无 Qdrant / 无模型服务）的 Lite 环境下运行，
验证降级路径与 Trace impact 字段的诚实性。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from openrag_forge.app import app
from openrag_forge.config import settings


def test_plugins_catalog_has_docs_runtime_and_tunables():
    with TestClient(app) as client:
        body = client.get("/api/v1/plugins").json()
        nodes = body["nodes"]
        assert nodes["sparse_retrieve"]["implemented"] == "live"
        assert nodes["graph_query"]["implemented"] == "stub"
        assert nodes["reranker"]["implemented"] == "fallback"
        for node_type, spec in nodes.items():
            assert spec["description"], node_type
            assert "config_defaults" in spec
            assert spec["implemented"] in {"live", "fallback", "stub"}
        chunker_schema = {field["key"] for field in nodes["chunker"]["config_schema"]}
        assert {"max_chars", "overlap"} <= chunker_schema


def test_upload_blocks_chunks_and_enrichment(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        upload = client.post(
            "/api/v1/knowledge-bases/default/documents",
            files={"file": ("faq.md", b"# Refund policy\n\nCustomers may dispute an unknown charge with the issuing bank.", "text/markdown")},
        )
        assert upload.status_code == 200
        payload = upload.json()
        document_id = payload["document"]["document_id"]
        assert payload["route"]["route"] == "native_text"
        assert any(event["node_id"] == "meta" for event in payload["trace"])
        blocks = client.get(f"/api/v1/documents/{document_id}/blocks").json()["items"]
        assert blocks and blocks[0]["block_type"] in {"heading", "paragraph"}
        chunks = client.get(f"/api/v1/documents/{document_id}/chunks").json()["items"]
        assert chunks
        metadata = chunks[0]["metadata"]
        assert metadata["title"]
        assert metadata["language"] in {"en", "zh", "mixed", "unknown"}
        assert isinstance(metadata.get("keywords"), list)


def test_reprocess_with_chunker_config_bumps_version(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        text = ("Refund dispute guidance. " * 60).encode()
        upload = client.post("/api/v1/knowledge-bases/default/documents", files={"file": ("long.md", text, "text/markdown")})
        document_id = upload.json()["document"]["document_id"]
        baseline_chunks = upload.json()["chunks"]
        reprocessed = client.post(f"/api/v1/documents/{document_id}/reprocess?max_chars=300&overlap=30")
        assert reprocessed.status_code == 200
        body = reprocessed.json()
        assert body["document"]["version"] == 2
        assert body["chunks"] > baseline_chunks


def test_hybrid_run_offline_records_honest_backends(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    # Keep this regression test deterministic even when a developer happens
    # to have a real Qdrant running locally.
    monkeypatch.setattr(settings, "qdrant_url", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "chat_base_url", "http://127.0.0.1:1/v1")
    with TestClient(app) as client:
        client.post(
            "/api/v1/knowledge-bases/default/documents",
            files={"file": ("faq.md", b"Customers may dispute an unknown charge with the issuing bank first.", "text/markdown")},
        )
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v0_2_hybrid", "question": "How should a customer dispute an unknown charge?", "mode": "run"})
        assert run.status_code == 200
        payload = run.json()
        trace = {event["node_id"]: event for event in payload["trace"]}
        assert trace["d"]["details"]["impact"]["backend"] == "lexical_fallback"
        assert trace["s"]["details"]["impact"]["backend"] == "bm25_local"
        assert trace["f"]["details"]["impact"]["candidate_count"] >= 1
        assert "evidence_ids" in trace["c"]["details"]["impact"]
        assert trace["g"]["details"]["impact"]["provider"] in {"extractive_fallback", "no_evidence", "citation_repair_fallback"}
        assert payload["evidence"], "词法降级路径应产出证据"
        assert payload["safety"]["side_effects"] is False


def test_graph_node_is_honest_runtime_stub(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v0_7_graph", "question": "How should a customer dispute a charge?", "mode": "run"})
        assert run.status_code == 200
        graph_events = [event for event in run.json()["trace"] if event["node_id"] == "graph"]
        assert graph_events and graph_events[0]["status"] == "skipped"
        assert graph_events[0]["details"]["impact"]["runtime"] == "stub"


def test_cache_node_short_circuits_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        client.post(
            "/api/v1/knowledge-bases/default/documents",
            files={"file": ("faq.md", b"Support agents verify the merchant and the charge date.", "text/markdown")},
        )
        request = {"knowledge_base_id": "default", "recipe_id": "v0_9_operations", "question": "What does the support agent verify first?", "mode": "run"}
        first = client.post("/api/v1/runs", json=request).json()
        first_cache = next(event for event in first["trace"] if event["node_id"] == "cache")
        assert first_cache["details"]["impact"]["cache"] == "miss"
        second = client.post("/api/v1/runs", json=request).json()
        second_cache = next(event for event in second["trace"] if event["node_id"] == "cache")
        assert second_cache["details"]["impact"]["cache"] == "hit"
        skipped = [event for event in second["trace"] if event["status"] == "skipped"]
        assert skipped and all(event["details"]["impact"]["skipped_reason"] == "cache_hit" for event in skipped)
        assert second["safety"].get("cache_hit") is True


def test_bounded_corrective_records_bounded_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        client.post(
            "/api/v1/knowledge-bases/default/documents",
            files={"file": ("faq.md", b"Chargeback SOP: verify merchant descriptor and posting date.", "text/markdown")},
        )
        run = client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v0_6_corrective", "question": "完全无关的问题词汇组合", "mode": "run"})
        assert run.status_code == 200
        retry_events = [event for event in run.json()["trace"] if event["node_id"] == "retry"]
        assert retry_events
        impact = retry_events[0]["details"]["impact"]
        assert impact["max_retries"] <= 2
        if impact.get("triggered"):
            assert impact["retries_used"] <= impact["max_retries"]


def test_model_registration_masks_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        registered = client.post("/api/v1/models", json={
            "model_id": "cloud-chat", "display_name": "Cloud Chat", "kind": "chat",
            "base_url": "https://api.example.com/v1", "model_name": "gpt-test", "api_key": "sk-secret-value",
        })
        assert registered.status_code == 200
        assert "api_key" not in registered.json()["model"]
        assert registered.json()["model"]["has_api_key"] is True
        listed = client.get("/api/v1/models").json()["items"]
        entry = next(item for item in listed if item["model_id"] == "cloud-chat")
        assert "api_key" not in entry and entry["has_api_key"] is True
        probe = client.post("/api/v1/models/cloud-chat/probe").json()
        assert probe["status"] in {"ready", "unreachable"}
        assert "sk-secret-value" not in str(probe)


def test_model_probe_rejects_http_200_error_payload(tmp_path, monkeypatch):
    """Some local servers return HTTP 200 with an unsupported-route error body."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"error": "Unexpected endpoint or method. (POST /rerank)"}

    class FakeClient:
        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("openrag_forge.app.get_http_client", lambda: FakeClient())
    with TestClient(app) as client:
        registered = client.post("/api/v1/models", json={
            "model_id": "unsupported-reranker", "display_name": "Unsupported Reranker", "kind": "reranker",
            "base_url": "http://localhost:23145", "model_name": "openrag-reranker",
        })
        assert registered.status_code == 200
        probe = client.post("/api/v1/models/unsupported-reranker/probe")
        assert probe.status_code == 200
        assert probe.json()["status"] == "unreachable"
        assert "Unexpected endpoint" in probe.json()["details"]["error"]


def test_recipe_import_export_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        exported = client.get("/api/v1/recipes/v0_1_dense/export")
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
        recipe = exported.json()
        recipe["recipe_id"] = "my_custom_recipe"
        recipe["name"] = "My Custom Recipe"
        imported = client.post("/api/v1/recipes/import", json=recipe)
        assert imported.status_code == 200
        item = imported.json()["items"][0]
        assert item["recipe_id"] == "my_custom_recipe"
        assert item["status"] == "draft"
        assert item["hash"]
        # 与已发布 Recipe 同名时不覆盖，自动加 _imported 后缀
        conflicting = exported.json()
        conflict = client.post("/api/v1/recipes/import", json=conflicting)
        assert conflict.json()["items"][0]["recipe_id"] == "v0_1_dense_imported"
        bad = client.post("/api/v1/recipes/import", json={"recipe_id": "bad", "name": "bad", "nodes": [{"id": "x", "type": "not_a_node"}], "edges": []})
        assert bad.status_code == 422


def test_runs_list_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(app) as client:
        client.post("/api/v1/runs", json={"knowledge_base_id": "default", "recipe_id": "v0_1_dense", "question": "List endpoint smoke question?", "mode": "run"})
        listed = client.get("/api/v1/runs").json()["items"]
        assert listed
        first = listed[0]
        assert {"run_id", "recipe_id", "status", "evidence_count", "trace_count"} <= set(first)
