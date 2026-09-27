"""Static contract tests for the destructive deployment preflight."""
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "deploy_exact_candidate.ps1"


class DeploymentIdentityGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")

    def test_storage_and_compose_identity_checks_precede_database_start(self):
        source = self.source
        db_start = source.index("& docker compose @composeArgs up -d db")
        for marker in (
            "Persistent storage root missing",
            "Persistent storage directory missing",
            "Compose project identity mismatch",
            "Compose database identity mismatch",
            "Expected production PostgreSQL volume does not exist",
            "Backend attachment mount identity mismatch",
            "Backend evidence mount identity mismatch",
        ):
            self.assertLess(source.index(marker), db_start, marker)

    def test_running_database_identity_is_checked_before_readiness_and_migration(self):
        source = self.source
        db_start = source.index("& docker compose @composeArgs up -d db")
        ready_loop = source.index("for ($i=0; $i -lt 60; $i++)")
        self.assertLess(db_start, source.index("Running DB compose project mismatch"))
        self.assertLess(source.index("Running DB compose project mismatch"), ready_loop)
        self.assertLess(source.index("Running DB volume mismatch"), ready_loop)
        self.assertLess(ready_loop, source.index("alembic upgrade head"))

    def test_running_application_storage_identity_is_checked_after_recreate(self):
        source = self.source
        app_start = source.index(
            "& docker compose @composeArgs up -d --force-recreate backend frontend nginx"
        )
        runtime_probe = source.index("$backendInfo = $null")
        for marker in (
            "Running backend compose project mismatch",
            "Running backend attachment mount mismatch",
            "Running backend evidence mount mismatch",
        ):
            self.assertLess(app_start, source.index(marker))
            self.assertLess(source.index(marker), runtime_probe)


if __name__ == "__main__":
    unittest.main()
