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


def test_backend_recreate_regression_keeps_nginx_running_and_probes_login():
    script = (ROOT / "tools" / "verify_backend_recreate_proxy.ps1").read_text(
        encoding="utf-8"
    )
    assert "'stop', 'backend'" in script
    assert "'rm', '-f', 'backend'" in script
    assert "'--network', $proxyNetwork[0], '--ip', $backendIpBefore" in script
    assert "'up', '-d', '--no-deps', 'backend" in script
    assert "backendIpBefore -eq $backendIpAfter" in script
    assert "nginxBefore -ne $nginxAfter" in script
    assert "/api/v1/auth/login" in script
    assert "502, 503, 504" in script
    assert "restart', 'nginx" not in script
