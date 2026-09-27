from scripts.audit_managed_storage import audit_status


def test_storage_audit_separates_integrity_failure_from_orphan_warning():
    assert audit_status([], ["evidence/vendor/orphan.pdf"]) == {
        "status": "WARN",
        "integrity_status": "PASS",
        "orphan_status": "WARN",
    }
    assert audit_status([{"code": "sha256_mismatch"}], []) == {
        "status": "FAIL",
        "integrity_status": "FAIL",
        "orphan_status": "PASS",
    }
