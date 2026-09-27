from __future__ import annotations

from app.llm.errors import classify_provider_error, is_fallback_eligible_provider_error
from app.llm.qwen import QwenProvider


class AIModelPoolExhausted(RuntimeError):
    def __init__(self, categories: list[str]):
        super().__init__("All certified AI models are temporarily unavailable")
        self.categories = categories


class QwenModelPoolProvider:
    """Sequential certified-model fallback; write intents use primary_only()."""

    def __init__(self, providers: list[QwenProvider]):
        if not providers:
            raise ValueError("Model pool requires at least one provider")
        self.providers = providers

    def primary_only(self) -> QwenProvider:
        return self.providers[0]

    def chat(self, messages, tools=None, tool_choice="auto") -> dict:
        return self._chat(
            messages,
            tools=tools,
            tool_choice=tool_choice,
        )

    def chat_with_max_tokens(
        self,
        messages,
        *,
        max_tokens: int,
        tools=None,
        tool_choice="auto",
    ) -> dict:
        return self._chat(
            messages,
            tools=tools,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
        )

    def _chat(self, messages, *, tools=None, tool_choice="auto", max_tokens=None) -> dict:
        categories: list[str] = []
        for provider in self.providers:
            try:
                if max_tokens is None:
                    return provider.chat(messages, tools=tools, tool_choice=tool_choice)
                return provider.chat_with_max_tokens(
                    messages,
                    max_tokens=max_tokens,
                    tools=tools,
                    tool_choice=tool_choice,
                )
            except Exception as exc:
                category = classify_provider_error(exc)
                if not is_fallback_eligible_provider_error(exc):
                    raise
                categories.append(category)
        raise AIModelPoolExhausted(categories)
