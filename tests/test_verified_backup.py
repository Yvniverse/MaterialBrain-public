"""A failed dump must never produce a successful backup manifest."""
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "verified_backup", Path(__file__).resolve().parents[1] / "tools/verified_backup.py"
)
backup_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup_tool)


class BackupTests(unittest.TestCase):
    def test_binary_dump_and_failure_do_not_publish_false_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attachments, evidence = root / "attachments", root / "evidence"
            attachments.mkdir()
            evidence.mkdir()
            (attachments / "binary.bin").write_bytes(bytes(range(256)))
            config = json.dumps({"name": "materialbrain", "services": {"db": {"environment": {
                "POSTGRES_USER": "test", "POSTGRES_DB": "isolated_test"}}}})
            binary_dump = b"PGDMP\x00\xff\x80\r\n"

            def run(command, **kwargs):
                if "pg_dump" in command:
                    kwargs["stdout"].write(binary_dump)
                else:
                    self.assertEqual(kwargs["stdin"].read(), binary_dump)

            with patch.object(backup_tool.subprocess, "check_output", return_value=config), \
                    patch.object(backup_tool.subprocess, "run", side_effect=run):
                manifest_path = backup_tool.backup(root, root / "ok", attachments, evidence)
                manifest = json.loads(manifest_path.read_text())
                self.assertEqual((manifest_path.parent / manifest["database"]).read_bytes(),
                                 binary_dump)
                self.assertTrue(manifest["dump_toc_verified"])
                self.assertEqual(manifest["compose_project"], "materialbrain")
                self.assertEqual(manifest["backup_directory"], str((root / "ok").resolve()))
                self.assertEqual(manifest["attachments_source"], {
                    "source_path": str(attachments.resolve()),
                    "file_count": 1,
                    "total_bytes": 256,
                })
                self.assertEqual(manifest["evidence_source"]["file_count"], 0)
                self.assertEqual(manifest["evidence_source"]["total_bytes"], 0)
            with patch.object(backup_tool.subprocess, "check_output", return_value=config), \
                    patch.object(backup_tool.subprocess, "run",
                                 side_effect=subprocess.CalledProcessError(1, "pg_dump")):
                with self.assertRaises(subprocess.CalledProcessError):
                    backup_tool.backup(root, root / "failed", attachments, evidence)
                self.assertEqual(list((root / "failed").glob("backup_manifest*")), [])


if __name__ == "__main__":
    unittest.main()
