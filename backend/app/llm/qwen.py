import time
from collections.abc import Callable

from openai import OpenAI

from app.llm.errors import is_retryable_provider_error


class QwenProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        enable_thinking: bool | None = None,
        max_tokens: int = 512,
        retry_delays: tuple[float, ...] = (1.0, 2.0, 4.0),
        sleeper: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.enable_thinking = enable_thinking
        self.max_tokens = max(1, int(max_tokens))
        self.retry_delays = retry_delays
        self.sleeper = sleeper
        self.monotonic = monotonic
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> dict:
        return self.chat_with_max_tokens(
            messages,
            max_tokens=self.max_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )

    def chat_with_max_tokens(
        self,
        messages: list[dict],
        *,
        max_tokens: int,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> dict:
        started = time.perf_counter()
        deadline = self.monotonic() + self.timeout_seconds
        attempts = 0
        last_error: BaseException | None = None
        request_max_tokens = max(1, int(max_tokens))
        while attempts <= len(self.retry_delays):
            remaining = deadline - self.monotonic()
            if remaining <= 0 and last_error is not None:
                raise last_error
            attempts += 1
            request: dict = {
                "model": self.model,
                "messages": messages,
                "tools": tools or [],
                "tool_choice": tool_choice if tools else None,
                "max_tokens": request_max_tokens,
                "timeout": max(0.1, remaining),
            }
            if self.enable_thinking is not None:
                request["extra_body"] = {"enable_thinking": self.enable_thinking}
            try:
                response = self.client.chat.completions.create(**request)
                break
            except Exception as exc:
                last_error = exc
                retry_index = attempts - 1
                if (
                    not is_retryable_provider_error(exc)
                    or retry_index >= len(self.retry_delays)
                ):
                    raise
                delay = self.retry_delays[retry_index]
                if delay >= deadline - self.monotonic():
                    raise
                self.sleeper(delay)
        else:  # pragma: no cover - loop exits by success or exception
            raise RuntimeError("Qwen retry loop ended unexpectedly")
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))
        message = response.choices[0].message
        tool_calls = []
        for call in message.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
            )
        result = {"role": "assistant", "content": message.content or ""}
        if tool_calls:
            result["tool_calls"] = tool_calls
        usage = response.usage
        result["_telemetry"] = {
            "provider": "qwen",
            "model": self.model,
            "finish_reason": str(response.choices[0].finish_reason or "unknown"),
            "max_tokens": request_max_tokens,
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            "latency_ms": latency_ms,
            "tool_call_count": len(tool_calls),
            "attempts": attempts,
            "retries": attempts - 1,
        }
        return result
