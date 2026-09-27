PERMISSIONS = (
    "dashboard:view",
    "material:view",
    "material:manage",
    "component:validate",
    "inventory:view",
    "inventory:operate",
    "category:manage",
    "location:manage",
    "supplier:manage",
    "project:view",
    "project:manage",
    "picking:view",
    "picking:operate",
    "purchase:manage",
    "import:manage",
    "export:view",
    "attachment:manage",
    "user:manage",
    "role:manage",
    "audit:view",
)

PERMISSION_SET = frozenset(PERMISSIONS)
ACCESS_ADMIN_PERMISSIONS = frozenset({"user:manage", "role:manage"})


def normalize_permissions(permissions: list[str]) -> list[str]:
    """Validate, deduplicate, and keep permissions in a stable display order."""
    unique_permissions = set(permissions)
    unknown = unique_permissions - PERMISSION_SET - {"*"}
    if unknown:
        unknown_text = "、".join(sorted(unknown))
        raise ValueError(f"包含未知权限：{unknown_text}")
    if "*" in unique_permissions:
        return ["*"]
    return [permission for permission in PERMISSIONS if permission in unique_permissions]


def can_manage_access(permissions: list[str] | None) -> bool:
    granted = set(permissions or [])
    return "*" in granted or ACCESS_ADMIN_PERMISSIONS.issubset(granted)
