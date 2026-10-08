import re
from pathlib import Path

COMPOSE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"


def _service_block(compose: str, service: str) -> str:
    start = compose.index(f"  {service}:\n")
    next_block = re.search(r"(?m)^  [A-Za-z0-9_]+:\s*$", compose[start + 1 :])
    end = start + 1 + next_block.start() if next_block else len(compose)
    return compose[start:end]


def test_compose_keeps_databases_internal_and_backend_has_controlled_egress():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")
    db = _service_block(compose, "db")
    robotics = _service_block(compose, "robotics")
    backend = _service_block(compose, "backend")
    backup = _service_block(compose, "backup")

    assert "llm_egress" not in db
    assert "ports:" not in db
    assert "ports:" not in robotics
    assert "ROS_DOMAIN_ID: ${ROS_DOMAIN_ID:-147}" in robotics
    assert "profiles: [robotics]" in robotics
    assert "storage" in robotics and "robotics_egress" in robotics
    assert "llm_egress" not in backup
    assert "internal" in backend and "llm_egress" in backend
    assert "source: ${MATERIALBRAIN_STORAGE_ROOT:-./storage}/evidence" in backend
    assert "source: ${MATERIALBRAIN_STORAGE_ROOT:-./storage}/evidence" in backup
    assert "read_only: true" in backup
    assert "internal: true" in compose
    assert "name: materialbrain_public_v02" in compose
    test_overlay = COMPOSE_PATH.with_name("docker-compose.test.yml").read_text(encoding="utf-8")
    assert "127.0.0.1:${TEST_POSTGRES_PORT:-55432}" in test_overlay
    assert "test_host" in test_overlay
