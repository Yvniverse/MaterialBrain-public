from app.core.config import Settings, settings
from app.core.exceptions import BusinessError
from app.llm.base import LLMProvider
from app.llm.model_pool import QwenModelPoolProvider
from app.llm.qwen import QwenProvider


def _csv(value: str) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))


def create_llm_provider(config: Settings = settings) -> LLMProvider:
    if config.llm_provider != "qwen":
        raise BusinessError(
            "LLM_PROVIDER_UNSUPPORTED",
            f"不支持的 LLM Provider：{config.llm_provider}",
            503,
        )
    models = [config.dashscope_model]
    if config.dashscope_model_pool_enabled:
        models.extend(_csv(config.dashscope_fallback_models))
        certified = set(_csv(config.dashscope_certified_models))
        uncertified = [model for model in models if model not in certified]
        if uncertified:
            raise BusinessError(
                "UNCERTIFIED_MODEL_POOL",
                "模型池包含未通过 Full/Shadow 认证的模型",
                503,
                details={"models": uncertified},
            )
    providers = [
        QwenProvider(
            api_key=config.dashscope_api_key,
            base_url=config.dashscope_base_url,
            model=model,
            timeout_seconds=config.agent_timeout_seconds,
            enable_thinking=config.dashscope_enable_thinking,
        )
        for model in models
    ]
    return QwenModelPoolProvider(providers) if len(providers) > 1 else providers[0]
