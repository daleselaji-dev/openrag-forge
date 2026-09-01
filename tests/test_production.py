"""生产能力回归测试：健康探针、请求上下文、安全中间件、指标端点。

教学点：横切能力（认证/限流/头部/探针）与业务逻辑一样需要测试保护——
它们往往在"顺手重构中间件"时被无声破坏，而故障要到生产才暴露。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from openrag_forge.app import app
from openrag_forge.config import settings
from openrag_forge.security import RateLimitMiddleware


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """每个测试使用独立数据目录，避免污染仓库内的 ./data。"""
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def test_liveness_and_readiness_probes():
    with TestClient(app) as client:
        assert client.get("/livez").json() == {"status": "alive"}
        assert client.get("/readyz").json() == {"status": "ready"}


def test_readiness_fails_when_truth_source_is_broken(monkeypatch):
    with TestClient(app) as client:
        def broken():
            raise RuntimeError("sqlite gone")

        monkeypatch.setattr(app.state.store, "list_knowledge_bases", broken)
        response = client.get("/readyz")
        assert response.status_code == 503
        assert response.json()["status"] == "not_ready"


def test_request_id_generated_and_echoed():
    with TestClient(app) as client:
        generated = client.get("/livez")
        assert generated.headers["x-request-id"].startswith("req_")
        # 客户端/网关传入的关联 ID 必须原样回写，实现跨层日志串联
        echoed = client.get("/livez", headers={"X-Request-ID": "gw-abc-123"})
        assert echoed.headers["x-request-id"] == "gw-abc-123"


def test_security_headers_present():
    with TestClient(app) as client:
        response = client.get("/livez")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"


def test_metrics_endpoint_exposes_prometheus_text():
    with TestClient(app) as client:
        client.get("/api/v1/health")
        response = client.get("/metrics")
        if response.status_code == 501:
            pytest.skip("prometheus-client 未安装（未装 observability extra）")
        assert response.status_code == 200
        assert b"openrag_http_requests_total" in response.content


def test_api_key_enforced_when_configured(monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setattr(settings, "api_key", "secret-key")
        assert client.get("/api/v1/recipes").status_code == 401
        assert client.get("/api/v1/recipes", headers={"X-API-Key": "secret-key"}).status_code == 200
        assert client.get("/api/v1/recipes", headers={"Authorization": "Bearer secret-key"}).status_code == 200
        # 探活端点必须豁免认证，否则编排器探针会把健康副本判死
        assert client.get("/livez").status_code == 200
        assert client.get("/readyz").status_code == 200


def test_rate_limit_returns_429_with_retry_after(monkeypatch):
    RateLimitMiddleware.reset()
    with TestClient(app) as client:
        monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
        assert client.get("/api/v1/recipes").status_code == 200
        assert client.get("/api/v1/recipes").status_code == 200
        limited = client.get("/api/v1/recipes")
        assert limited.status_code == 429
        assert "retry-after" in limited.headers
        # 探活端点豁免限流
        assert client.get("/livez").status_code == 200
    RateLimitMiddleware.reset()


def test_health_reports_production_readiness_warnings(monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setattr(settings, "environment", "production")
        warnings = client.get("/api/v1/health").json()["production_readiness"]["warnings"]
        # 默认开发配置（CORS 全开、无 API key）在生产环境必须触发告警
        assert any("CORS" in warning for warning in warnings)
        assert any("API_KEY" in warning for warning in warnings)
        monkeypatch.setattr(settings, "environment", "dev")
        assert client.get("/api/v1/health").json()["production_readiness"]["warnings"] == []


def test_trace_events_carry_otel_trace_id_field():
    with TestClient(app) as client:
        run = client.post(
            "/api/v1/runs",
            json={"knowledge_base_id": "default", "recipe_id": "v0_1_dense", "question": "What is documented here?", "mode": "preview"},
        )
        assert run.status_code == 200
        for event in run.json()["trace"]:
            # 字段始终存在；tracing 未启用时为 None，启用后即为 Jaeger 可搜索的 ID
            assert "otel_trace_id" in event
