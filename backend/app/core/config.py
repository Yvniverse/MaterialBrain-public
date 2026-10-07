from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MaterialBrain电子物料仓库管理系统"
    environment: str = "development"
    database_url: str = "sqlite:///./materialbrain.db"
    session_hours: int = 8
    cookie_secure: bool = False
    attachment_dir: Path = Path("../storage/attachments")
    engineering_evidence_storage_dir: Path = Path("../storage")
    max_upload_mb: int = 20
    login_max_attempts: int = 5
    login_lock_minutes: int = 15
    business_timezone: str = "Asia/Shanghai"
    materialbrain_build_sha: str = "unknown"
    materialbrain_release_tag: str = ""
    materialbrain_build_time_utc: str = ""
    agent_enabled: bool = False
    # ``normal`` preserves production Agent behavior. The other policies are
    # server-side test guards and fail closed before a Qwen client is called.
    agent_model_policy: Literal["normal", "deterministic", "jev_only"] = "normal"
    llm_provider: str = "qwen"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen3.7-plus-2026-05-26"
    dashscope_enable_thinking: bool | None = False
    dashscope_model_pool_enabled: bool = True
    dashscope_fallback_models: str = "qwen3.7-max-2026-06-08"
    dashscope_certified_models: str = (
        "qwen3.7-plus-2026-05-26,qwen3.7-max-2026-06-08"
    )
    # TypeSafe Jev is a shadow-only optional reviewer.  Keep both gates off by
    # default; the deterministic EvidenceAnchor path remains authoritative.
    typesafe_api_key: str = ""
    typesafe_api_base_url: str = "https://api.typesafe.ai"
    jev_enabled: bool = False
    jev_shadow_enabled: bool = False
    jev_model: str = "jev-latest"
    # Phase 3.1.2's live Jev budget is bounded server-side even when a caller
    # forgets to provide an explicit environment override.
    jev_stage_input_token_cap: int = Field(default=200_000, ge=1, le=10_000_000)
    jev_stage_request_cap: int = Field(default=30, ge=1, le=30)
    component_intelligence_enabled: bool = False
    traceable_engineering_evidence_required: bool = True
    sample_data_seed_enabled: bool = False
    sample_product_seed_enabled: bool = False
    sample_relation_seed_enabled: bool = False
    sample_evidence_seed_enabled: bool = False
    sample_cable_seed_enabled: bool = False
    sample_database_rebuild_enabled: bool = False
    agent_deterministic_material_resolution_enabled: bool = False
    agent_engineering_research_enabled: bool = True
    agent_max_tool_rounds: int = Field(default=5, ge=1, le=10)
    agent_timeout_seconds: float = Field(default=45, ge=5, le=120)
    agent_total_deadline_seconds: float = Field(default=90, ge=10, le=300)
    agent_conversation_ttl_minutes: int = Field(default=1440, ge=5, le=10080)
    agent_conversation_cleanup_grace_days: int = Field(default=7, ge=1, le=90)
    spatial_robot_bridge_url: str = "http://robotics:8766"
    spatial_nav2_evidence_dir: str = "../storage/robotics/qualification"
    spatial_sample_map_enabled: bool = False
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
