from types import SimpleNamespace

import httpx2
import pytest
from openai import APIConnectionError, APIStatusError, RateLimitError

from app.llm.errors import classify_provider_error, is_fallback_eligible_provider_error
from app.llm.model_pool import AIModelPoolExhausted, QwenModelPoolProvider
from app.llm.qwen import QwenProvider


def test_qwen_provider_returns_sanitized_telemetry():
    provider = QwenProvider(
        api_key="test-only-key",
        base_url="https://provider.invalid/v1",
        model="qwen-test",
        timeout_seconds=1,
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="",
                    tool_calls=[
                        SimpleNamespace(
                            id="call-1",
                            function=SimpleNamespace(
                                name="search_materials",
                                arguments='{"query":"F405"}',
                            ),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=12,
            completion_tokens=7,
            total_tokens=19,
        ),
    )
    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))
    )

    result = provider.chat(
        [{"role": "user", "content": "F405"}],
        tools=[{"type": "function", "function": {"name": "search_materials"}}],
    )

    assert result["tool_calls"][0]["function"]["name"] == "search_materials"
    assert result["_telemetry"] == {
        "provider": "qwen",
        "model": "qwen-test",
        "finish_reason": "tool_calls",
        "input_tokens": 12,
        "output_tokens": 7,
        "total_tokens": 19,
        "latency_ms": result["_telemetry"]["latency_ms"],
        "tool_call_count": 1,
        "attempts": 1,
        "retries": 0,
        "max_tokens": 512,
    }
    assert result["_telemetry"]["latency_ms"] >= 0


@pytest.mark.parametrize("enable_thinking", [None, False, True])
def test_qwen_provider_only_sends_thinking_flag_when_explicit(enable_thinking):
    provider = QwenProvider(
        api_key="test-only-key",
        base_url="https://provider.invalid/v1",
        model="qwen-test",
        timeout_seconds=1,
        enable_thinking=enable_thinking,
    )
    captured = {}
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="OK", tool_calls=[]),
            )
        ],
        usage=None,
    )

    def create(**kwargs):
        captured.update(kwargs)
        return response

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    provider.chat([{"role": "user", "content": "ping"}])

    assert captured["max_tokens"] == 512
    if enable_thinking is None:
        assert "extra_body" not in captured
    else:
        assert captured["extra_body"] == {"enable_thinking": enable_thinking}


def test_qwen_provider_accepts_bounded_per_call_output_budget():
    provider = QwenProvider(
        api_key="test-only-key",
        base_url="https://provider.invalid/v1",
        model="qwen-test",
        timeout_seconds=1,
    )
    captured = {}
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="Answer", tool_calls=[]),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=21, completion_tokens=32, total_tokens=53),
    )

    def create(**kwargs):
        captured.update(kwargs)
        return response

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = provider.chat_with_max_tokens(
        [{"role": "user", "content": "engineering"}],
        tools=[],
        max_tokens=768,
    )

    assert captured["max_tokens"] == 768
    assert result["_telemetry"]["max_tokens"] == 768
    assert result["_telemetry"]["input_tokens"] == 21
    assert result["_telemetry"]["output_tokens"] == 32


def test_qwen_provider_retries_rate_limit_with_bounded_backoff():
    delays = []
    provider = QwenProvider(
        api_key="test-only-key",
        base_url="https://provider.invalid/v1",
        model="qwen-test",
        timeout_seconds=10,
        retry_delays=(1.0, 2.0, 4.0),
        sleeper=delays.append,
    )
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="OK", tool_calls=[]),
            )
        ],
        usage=None,
    )
    calls = 0

    def create(**_kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RateLimitError(
                "rate limited",
                response=httpx2.Response(
                    429,
                    request=httpx2.Request("POST", "https://provider.invalid/v1/chat"),
                ),
                body={"code": "TooManyRequests"},
            )
        return response

    provider.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    result = provider.chat([{"role": "user", "content": "ping"}])

    assert calls == 3
    assert delays == [1.0, 2.0]
    assert result["_telemetry"]["attempts"] == 3
    assert result["_telemetry"]["retries"] == 2


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"code": "InvalidApiKey"}, "provider_401"),
        (403, {"code": "PermissionDenied"}, "provider_403"),
        (403, {"code": "AllocationQuota.FreeTierOnly"}, "quota_exhausted"),
        (429, {"code": "TooManyRequests"}, "provider_429"),
        (500, {"code": "InternalError"}, "provider_5xx"),
    ],
)
def test_provider_status_error_classification(status, body, expected):
    error = APIStatusError(
        "provider failure",
        response=httpx2.Response(
            status,
            request=httpx2.Request("POST", "https://provider.invalid/v1/chat"),
        ),
        body=body,
    )

    assert classify_provider_error(error) == expected


def test_provider_dns_error_classification():
    error = APIConnectionError(
        request=httpx2.Request("POST", "https://provider.invalid/v1/chat"),
        message="DNS name resolution failed",
    )

    assert classify_provider_error(error) == "dns"


class _PoolProvider:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _status_error(status: int, code: str):
    return APIStatusError(
        "provider failure",
        response=httpx2.Response(
            status,
            request=httpx2.Request("POST", "https://provider.invalid/v1/chat"),
        ),
        body={"code": code},
    )


def test_model_pool_falls_back_only_for_eligible_provider_errors():
    primary = _PoolProvider(error=_status_error(429, "TooManyRequests"))
    fallback = _PoolProvider(result={"role": "assistant", "content": "OK"})
    result = QwenModelPoolProvider([primary, fallback]).chat([])
    assert result["content"] == "OK"
    assert primary.calls == fallback.calls == 1
    assert is_fallback_eligible_provider_error(primary.error)


def test_model_pool_does_not_fallback_on_auth_error():
    primary = _PoolProvider(error=_status_error(401, "InvalidApiKey"))
    fallback = _PoolProvider(result={"role": "assistant", "content": "unsafe"})
    with pytest.raises(APIStatusError):
        QwenModelPoolProvider([primary, fallback]).chat([])
    assert primary.calls == 1
    assert fallback.calls == 0


def test_model_pool_reports_controlled_exhaustion():
    providers = [
        _PoolProvider(error=_status_error(429, "TooManyRequests")),
        _PoolProvider(error=_status_error(503, "Unavailable")),
    ]
    with pytest.raises(AIModelPoolExhausted) as captured:
        QwenModelPoolProvider(providers).chat([])
    assert captured.value.categories == ["provider_429", "provider_5xx"]
