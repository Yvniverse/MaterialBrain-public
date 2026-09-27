from app.core.permissions import normalize_permissions
from app.seed.defaults import ROLE_DEFAULTS


def test_picking_permissions_are_first_class_and_stably_ordered():
    assert normalize_permissions(["picking:operate", "picking:view"]) == [
        "picking:view",
        "picking:operate",
    ]


def test_builtin_roles_separate_view_from_operate():
    assert "picking:view" in ROLE_DEFAULTS["硬件工程师"]
    assert "picking:operate" not in ROLE_DEFAULTS["硬件工程师"]
    assert {"picking:view", "picking:operate"}.issubset(ROLE_DEFAULTS["仓库管理员"])
    assert {"picking:view", "picking:operate"}.issubset(ROLE_DEFAULTS["项目负责人"])
    assert "picking:view" not in ROLE_DEFAULTS["采购人员"]
