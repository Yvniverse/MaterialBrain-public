from typing import Protocol


class LLMProvider(Protocol):
    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> dict:
        """Return one normalized assistant message."""
        ...
