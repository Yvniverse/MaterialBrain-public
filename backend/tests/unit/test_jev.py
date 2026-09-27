import httpx
import pytest

from app.core.config import Settings
from app.jev import (
    FakeJevProvider,
    HttpJevProvider,
    JevChoiceQuestion,
    JevNoulQuestion,
    JevProviderError,
    JevRequest,
    JevScoreQuestion,
    JevShadowPilot,
    validate_jev_response,
)


def _questions():
    return {
        "best_anchor": JevChoiceQuestion(
            type="choice",
            instructions="Choose the best public evidence anchor.",
            criteria={"a": None, "b": None},
        ),
        "adequacy": JevScoreQuestion(
            type="score",
            instructions="Score citation adequacy.",
            criteria=["insufficient", "partial", "adequate"],
        ),
        "supports": JevNoulQuestion(
            type="noul",
            instructions="Does the cited excerpt support the question?",
        ),
    }


def test_jev_contract_and_fake_provider_are_typed_and_deterministic():
    request = JevRequest(
        state={"question": "Does the excerpt support the question?", "document": "public PDF"},
        model="jev-latest",
        questions=_questions(),
    )
    response = FakeJevProvider().evaluate(request)
    validated = validate_jev_response(request, response.model_dump(mode="json"))
    assert set(validated.answers) == set(request.questions)
    assert validated.usage.input_tokens > 0
    assert validated.answers["best_anchor"].type == "choice"


def test_jev_contract_rejects_unknown_choice_probability_keys():
    request = JevRequest(
        state="public evidence",
        model="jev-latest",
        questions={
            "choice": JevChoiceQuestion(
                type="choice", instructions="Choose", criteria={"a": None, "b": None}
            )
        },
    )
    response = FakeJevProvider().evaluate(request).model_dump(mode="json")
    response["answers"]["choice"]["probabilities"]["private"] = 0.0
    with pytest.raises(ValueError, match="options mismatch"):
        validate_jev_response(request, response)


def test_shadow_pilot_blocks_private_fields_and_defaults_off():
    pilot = JevShadowPilot(Settings())
    with pytest.raises(ValueError, match="private field"):
        pilot.evaluate(
            state={"question": "public", "inventory": {"material": "must not leave"}},
            questions=_questions(),
        )

    outcome = pilot.evaluate(state={"question": "public"}, questions=_questions())
    assert outcome.status == "JEV_SHADOW_DISABLED"
    assert outcome.provider_calls == 0


def test_shadow_pilot_fake_path_consumes_bounded_local_usage():
    settings = Settings(jev_stage_input_token_cap=10_000)
    pilot = JevShadowPilot(settings)
    outcome = pilot.evaluate(
        state={"question": "public support"},
        questions=_questions(),
        force_fake=True,
    )
    assert outcome.status == "FAKE_PASS"
    assert outcome.provider_calls == 0
    assert pilot.input_tokens == outcome.input_tokens


def test_shadow_pilot_counts_blocked_provider_attempt_without_counting_tokens():
    class BlockedProvider:
        def evaluate(self, _request):
            raise JevProviderError("JEV_SHADOW_BLOCKED_NETWORK", "offline")

    settings = Settings(
        jev_enabled=True,
        jev_shadow_enabled=True,
        typesafe_api_key="test-secret",
    )
    pilot = JevShadowPilot(settings, provider=BlockedProvider())
    outcome = pilot.evaluate(state={"question": "public"}, questions=_questions())
    assert outcome.status == "JEV_SHADOW_BLOCKED_NETWORK"
    assert outcome.provider_calls == 1
    assert pilot.provider_calls == 1
    assert pilot.input_tokens == 0


def test_shadow_pilot_enforces_http_request_cap():
    class BlockedProvider:
        def evaluate(self, _request):
            raise JevProviderError("JEV_SHADOW_BLOCKED_NETWORK", "offline")

    settings = Settings(
        jev_enabled=True,
        jev_shadow_enabled=True,
        typesafe_api_key="test-secret",
        jev_stage_request_cap=1,
    )
    pilot = JevShadowPilot(settings, provider=BlockedProvider())
    first = pilot.evaluate(state={"question": "public"}, questions=_questions())
    second = pilot.evaluate(state={"question": "public"}, questions=_questions())
    assert first.status == "JEV_SHADOW_BLOCKED_NETWORK"
    assert first.request_attempts == 1
    assert second.status == "JEV_SHADOW_BLOCKED_REQUEST_CAP"
    assert pilot.request_attempts == 1


def test_http_provider_uses_official_endpoint_shape_without_leaking_key():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "supports": {"type": "noul", "noul": 0.8},
                },
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        )

    request = JevRequest(
        state="public evidence excerpt",
        model="jev-latest",
        questions={"supports": JevNoulQuestion(type="noul", instructions="Does it support?")},
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = HttpJevProvider(
        api_key="test-secret",
        client=client,
        retry_delays=(),
    ).evaluate(request)
    assert response.usage.input_tokens == 12
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert b'"questions"' in seen["body"]
    client.close()
