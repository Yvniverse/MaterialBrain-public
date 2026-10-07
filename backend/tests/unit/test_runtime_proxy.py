from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_nginx_uses_runtime_docker_dns_for_backend_without_changing_api_uri():
    config = (ROOT / "nginx" / "nginx.conf").read_text(encoding="utf-8")
    assert "resolver 127.0.0.11 ipv6=off valid=5s;" in config
    assert "upstream backend" not in config
    assert config.count("set $materialbrain_backend backend:8000;") == 2
    assert config.count("proxy_pass http://$materialbrain_backend;") == 2
    assert "location = /api/v1/auth/login" in config
    assert "location /api/" in config
    assert "include /etc/nginx/proxy_params;" in config
