"""Safe DashScope connectivity smoke test without warehouse data or secret output."""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.llm.errors import classify_provider_error
from app.llm.qwen import QwenProvider


def main() -> int:
    base_url_host = urlparse(settings.dashscope_base_url).hostname or "invalid"
    if not settings.dashscope_api_key.strip():
        print(
            json.dumps(
                {
                    "success": False,
                    "provider": "qwen",
                    "model": settings.dashscope_model,
                    "base_url_host": base_url_host,
                    "key_configured": False,
                    "error_category": "missing_api_key",
                }
            )
        )
        return 2
    provider = QwenProvider(
        api_key=settings.dashscope_api_key,
        base_url=settings.dashscope_base_url,
        model=settings.dashscope_model,
        timeout_seconds=settings.agent_timeout_seconds,
        enable_thinking=settings.dashscope_enable_thinking,
    )
    try:
        result = provider.chat(
            [
                {"role": "system", "content": "只回答 OK。"},
                {"role": "user", "content": "连通性测试"},
            ],
            tools=None,
        )
    except Exception as exc:
        category = classify_provider_error(exc)
    else:
        telemetry = result.get("_telemetry") or {}
        print(
            json.dumps(
                {
                    "success": True,
                    "base_url_host": base_url_host,
                    "key_configured": True,
                    "enable_thinking": settings.dashscope_enable_thinking,
                    **telemetry,
                },
                ensure_ascii=False,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "success": False,
                "provider": "qwen",
                "model": settings.dashscope_model,
                "base_url_host": base_url_host,
                "key_configured": True,
                "enable_thinking": settings.dashscope_enable_thinking,
                "error_category": category,
            },
            ensure_ascii=False,
        )
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
