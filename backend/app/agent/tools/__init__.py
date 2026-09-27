__all__ = ["ReadOnlyToolRegistry", "ToolContext", "ToolRegistry"]


def __getattr__(name: str):
    if name in __all__:
        from app.agent.tools.registry import (
            ReadOnlyToolRegistry,
            ToolContext,
            ToolRegistry,
        )

        return {
            "ReadOnlyToolRegistry": ReadOnlyToolRegistry,
            "ToolContext": ToolContext,
            "ToolRegistry": ToolRegistry,
        }[name]
    raise AttributeError(name)
