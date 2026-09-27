"""Provider and shadow-budget helpers for public Evidence-only Jev calls."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.core.config import Settings

from .contracts import (
    JevChoiceAnswer,
    JevChoiceQuestion,
    JevNoulAnswer,
    JevNoulQuestion,
    JevRequest,
    JevResponse,
    JevScoreAnswer,
    JevScoreQuestion,
    JevUsage,
    validate_jev_response,
)

JevStatus = Literal[
    "PASS",
    "FAKE_PASS",
    "JEV_SHADOW_DISABLED",
    "JEV_SHADOW_BLOCKED_CREDENTIAL",
    "JEV_SHADOW_BLOCKED_NETWORK",
    "JEV_SHADOW_BLOCKED_RATE_LIMIT",
    "JEV_SHADOW_BLOCKED_BUDGET",
    "JEV_SHADOW_BLOCKED_REQUEST_CAP",
    "JEV_SHADOW_INVALID_RESPONSE",
]

_KNOWN_JEV_STATUSES = {
    "PASS",
    "FAKE_PASS",
    "JEV_SHADOW_DISABLED",
    "JEV_SHADOW_BLOCKED_CREDENTIAL",
    "JEV_SHADOW_BLOCKED_NETWORK",
    "JEV_SHADOW_BLOCKED_RATE_LIMIT",
    "JEV_SHADOW_BLOCKED_BUDGET",
    "JEV_SHADOW_BLOCKED_REQUEST_CAP",
    "JEV_SHADOW_INVALID_RESPONSE",
}

_PRIVATE_FIELD_MARKERS = {
    "api_key",
    "password",
    "secret",
    "token",
    "credential",
    "inventory",
    "stock",
    "bom",
    "reservation",
    "chat_history",
    "user_id",
}


class JevProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def estimate_input_tokens(request: JevRequest) -> int:
    """Conservative local estimate used before a request can consume the budget."""

    serialized = json.dumps(
        request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )
    return max(1, (len(serialized) + 3) // 4)


def _assert_public_state(value: Any, path: str = "state") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if any(marker in normalized for marker in _PRIVATE_FIELD_MARKERS):
                raise ValueError(f"Jev shadow state contains a private field at {path}.{key}")
            _assert_public_state(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_public_state(child, f"{path}[{index}]")


class FakeJevProvider:
    """Deterministic adapter for offline tests; it never performs network I/O."""

    def evaluate(self, request: JevRequest) -> JevResponse:
        state_text = json.dumps(request.state, ensure_ascii=False).casefold()
        answers: dict[str, Any] = {}
        for question_id, question in request.questions.items():
            if isinstance(question, JevChoiceQuestion):
                option = next(iter(question.criteria))
                probabilities = {key: 0.0 for key in question.criteria}
                probabilities[option] = 1.0
                answers[question_id] = JevChoiceAnswer(
                    type="choice",
                    choice=option,
                    probabilities=probabilities,
                    confidence=1.0,
                )
            elif isinstance(question, JevScoreQuestion):
                legend = {str(index): str(value) for index, value in enumerate(question.criteria)}
                probabilities = {key: 0.0 for key in legend}
                score_key = str(len(question.criteria) - 1) if "support" in state_text else "0"
                probabilities[score_key] = 1.0
                answers[question_id] = JevScoreAnswer(
                    type="score",
                    score=float(score_key),
                    legend=legend,
                    probabilities=probabilities,
                    confidence=1.0,
                )
            else:
                assert isinstance(question, JevNoulQuestion)
                value = 0.9 if "insufficient" not in state_text else 0.1
                answers[question_id] = JevNoulAnswer(type="noul", noul=value)
        request_tokens = estimate_input_tokens(request)
        return JevResponse(
            model=request.model,
            answers=answers,
            usage=JevUsage(input_tokens=request_tokens, output_tokens=max(1, len(answers))),
        )


class HttpJevProvider:
    """Minimal official System One HTTP adapter with bounded retry/fail-closed errors."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "jev-latest",
        base_url: str = "https://api.typesafe.ai",
        timeout_seconds: float = 30.0,
        retry_delays: tuple[float, ...] = (1.0, 2.0),
        client: httpx.Client | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.url = f"{base_url.rstrip('/')}/v1/systemone"
        self.timeout_seconds = timeout_seconds
        self.retry_delays = retry_delays
        self._client = client
        self.last_attempts = 0

    def evaluate(self, request: JevRequest) -> JevResponse:
        if not self.api_key.strip():
            raise JevProviderError("JEV_SHADOW_BLOCKED_CREDENTIAL", "TYPESAFE_API_KEY is absent")
        payload = request.model_copy(update={"model": self.model}).model_dump(mode="json")
        client = self._client or httpx.Client(timeout=self.timeout_seconds)
        close_client = self._client is None
        try:
            self.last_attempts = 0
            for attempt in range(len(self.retry_delays) + 1):
                self.last_attempts += 1
                try:
                    response = client.post(
                        self.url,
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json=payload,
                    )
                except httpx.RequestError as exc:
                    raise JevProviderError(
                        "JEV_SHADOW_BLOCKED_NETWORK", "TypeSafe network request failed"
                    ) from exc
                if response.status_code in {429, 529}:
                    if attempt < len(self.retry_delays):
                        time.sleep(self.retry_delays[attempt])
                        continue
                    raise JevProviderError(
                        "JEV_SHADOW_BLOCKED_RATE_LIMIT",
                        f"TypeSafe returned {response.status_code}",
                        retryable=True,
                    )
                if response.status_code in {401, 403}:
                    raise JevProviderError(
                        "JEV_SHADOW_BLOCKED_CREDENTIAL", "TypeSafe rejected the configured key"
                    )
                if response.status_code >= 400:
                    raise JevProviderError(
                        "JEV_SHADOW_BLOCKED_NETWORK",
                        f"TypeSafe returned HTTP {response.status_code}",
                    )
                try:
                    return validate_jev_response(
                        request.model_copy(update={"model": self.model}), response.json()
                    )
                except (TypeError, ValueError) as exc:
                    raise JevProviderError(
                        "JEV_SHADOW_INVALID_RESPONSE", "TypeSafe response failed typed validation"
                    ) from exc
            raise JevProviderError(
                "JEV_SHADOW_BLOCKED_NETWORK", "TypeSafe request exhausted retries"
            )
        finally:
            if close_client:
                client.close()


@dataclass(frozen=True)
class JevShadowOutcome:
    status: JevStatus
    response: JevResponse | None
    input_tokens: int
    output_tokens: int
    provider_calls: int
    error_code: str | None = None
    request_attempts: int = 0


class JevShadowPilot:
    """Run batched typed questions without ever promoting Jev into truth."""

    def __init__(self, settings: Settings, *, provider: Any | None = None):
        self.settings = settings
        self.provider = provider
        self.input_tokens = 0
        self.output_tokens = 0
        self.provider_calls = 0
        self.request_attempts = 0

    def evaluate(
        self,
        *,
        state: str | dict[str, Any] | list[Any],
        questions: dict[str, Any],
        force_fake: bool = False,
    ) -> JevShadowOutcome:
        _assert_public_state(state)
        request = JevRequest(state=state, model=self.settings.jev_model, questions=questions)
        estimate = estimate_input_tokens(request)
        if self.input_tokens + estimate > self.settings.jev_stage_input_token_cap:
            return JevShadowOutcome(
                status="JEV_SHADOW_BLOCKED_BUDGET",
                response=None,
                input_tokens=0,
                output_tokens=0,
                provider_calls=0,
                error_code="JEV_SHADOW_BLOCKED_BUDGET",
            )
        if force_fake:
            response = (self.provider or FakeJevProvider()).evaluate(request)
            self.input_tokens += response.usage.input_tokens
            self.output_tokens += response.usage.output_tokens
            return JevShadowOutcome(
                status="FAKE_PASS",
                response=response,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                provider_calls=0,
            )
        if not self.settings.jev_enabled or not self.settings.jev_shadow_enabled:
            return JevShadowOutcome(
                status="JEV_SHADOW_DISABLED",
                response=None,
                input_tokens=0,
                output_tokens=0,
                provider_calls=0,
            )
        if not self.settings.typesafe_api_key.strip():
            return JevShadowOutcome(
                status="JEV_SHADOW_BLOCKED_CREDENTIAL",
                response=None,
                input_tokens=0,
                output_tokens=0,
                provider_calls=0,
                error_code="JEV_SHADOW_BLOCKED_CREDENTIAL",
            )
        if self.request_attempts >= self.settings.jev_stage_request_cap:
            return JevShadowOutcome(
                status="JEV_SHADOW_BLOCKED_REQUEST_CAP",
                response=None,
                input_tokens=0,
                output_tokens=0,
                provider_calls=0,
                error_code="JEV_SHADOW_BLOCKED_REQUEST_CAP",
            )
        provider = self.provider or HttpJevProvider(
            api_key=self.settings.typesafe_api_key,
            model=self.settings.jev_model,
            base_url=self.settings.typesafe_api_base_url,
            timeout_seconds=self.settings.agent_timeout_seconds,
            # The phase budget is an HTTP request budget. Do not turn one
            # benchmark case into several requests through provider retries.
            retry_delays=(),
        )
        self.provider_calls += 1
        try:
            response = provider.evaluate(request)
        except JevProviderError as exc:
            attempts = int(getattr(provider, "last_attempts", 1) or 1)
            self.request_attempts += attempts
            return JevShadowOutcome(
                status=(
                    exc.code
                    if exc.code in _KNOWN_JEV_STATUSES
                    else "JEV_SHADOW_BLOCKED_NETWORK"
                ),
                response=None,
                input_tokens=0,
                output_tokens=0,
                provider_calls=1,
                error_code=exc.code,
                request_attempts=attempts,
            )
        attempts = int(getattr(provider, "last_attempts", 1) or 1)
        self.request_attempts += attempts
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        if self.input_tokens > self.settings.jev_stage_input_token_cap:
            return JevShadowOutcome(
                status="JEV_SHADOW_BLOCKED_BUDGET",
                response=response,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                provider_calls=1,
                error_code="JEV_SHADOW_BLOCKED_BUDGET",
                request_attempts=attempts,
            )
        return JevShadowOutcome(
            status="PASS",
            response=response,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            provider_calls=1,
            request_attempts=attempts,
        )
