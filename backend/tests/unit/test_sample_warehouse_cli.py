import pytest

from app.core.config import Settings
from scripts.seed_sample_warehouse import validate_target


@pytest.mark.parametrize("environment", ["development", "test"])
def test_read_only_preview_does_not_require_seed_write_flag(environment):
    config = Settings(environment=environment, sample_data_seed_enabled=False)
    validate_target(config, "public_sample", "public_sample", apply=False)


@pytest.mark.parametrize(
    "actual,expected", [("other_database", "public_sample"), ("public_sample", "")]
)
def test_identity_mismatch_refuses_any_sample_access(actual, expected):
    with pytest.raises(ValueError, match="identity"):
        validate_target(Settings(environment="test"), actual, expected, apply=False)


@pytest.mark.parametrize("apply", [False, True])
def test_production_environment_is_refused(apply):
    config = Settings(environment="production", sample_data_seed_enabled=True)
    with pytest.raises(ValueError, match="ENVIRONMENT"):
        validate_target(config, "public_sample", "public_sample", apply=apply)


def test_apply_requires_explicit_seed_flag():
    with pytest.raises(ValueError, match="SAMPLE_DATA_SEED_ENABLED"):
        validate_target(Settings(environment="test"), "public_sample", "public_sample", apply=True)
    validate_target(
        Settings(environment="test", sample_data_seed_enabled=True),
        "public_sample", "public_sample", apply=True,
    )
