from app.agent import service as agent_service
from app.core.config import settings


def test_http_agent_deterministic_policy_never_constructs_qwen(
    client, admin, material, monkeypatch
):
    calls = []

    def fail_if_constructed(*_args, **_kwargs):
        calls.append("qwen")
        raise AssertionError("deterministic HTTP UAT must not construct a Qwen provider")

    monkeypatch.setattr(agent_service, "create_llm_provider", fail_if_constructed)
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "agent_model_policy", "deterministic")
    monkeypatch.setattr(settings, "dashscope_api_key", "configured-but-must-not-be-used")

    response = client.post(
        "/api/v1/agent/query",
        json={"message": f"{material['code']} 在哪里？库存多少？"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["execution_mode"] == "deterministic"
    assert body["model_call_count"] == 0
    assert body["telemetry"] == []
    assert calls == []
