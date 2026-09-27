from __future__ import annotations

from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError


def _safe_error_text(error: BaseException) -> str:
    values: list[Any] = [getattr(error, "body", None)]
    response = getattr(error, "response", None)
    if response is not None:
        values.append(getattr(response, "text", None))
    values.append(str(error))
    return " ".join(str(value) for value in values if value).casefold()


def classify_provider_error(error: BaseException) -> str:
    if isinstance(error, APITimeoutError):
        return "timeout"
    if isinstance(error, RateLimitError):
        return "provider_429"
    if isinstance(error, APIConnectionError):
        text = _safe_error_text(error)
        if any(marker in text for marker in ("name resolution", "getaddrinfo", "dns")):
            return "dns"
        return "connection"
    if isinstance(error, APIStatusError):
        status = int(error.status_code or 0)
        text = _safe_error_text(error)
        if status == 403 and any(
            marker in text
            for marker in ("allocationquota.freetieronly", "free tier", "quota exhausted")
        ):
            return "quota_exhausted"
        if status in {401, 403, 429}:
            return f"provider_{status}"
        return "provider_5xx" if status >= 500 else "provider_status"
    return "unexpected"


def is_fallback_eligible_provider_error(error: BaseException) -> bool:
    return classify_provider_error(error) in {
        "quota_exhausted",
        "provider_429",
        "timeout",
        "connection",
        "dns",
        "provider_5xx",
    }


def is_retryable_provider_error(error: BaseException) -> bool:
    if isinstance(error, (APITimeoutError, APIConnectionError, RateLimitError)):
        return True
    return isinstance(error, APIStatusError) and int(error.status_code or 0) >= 500
