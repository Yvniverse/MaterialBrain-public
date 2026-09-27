from app.core.exceptions import BusinessError


def require_inventory_operator(permissions: list[str] | None) -> None:
    granted = set(permissions or [])
    if "*" not in granted and "inventory:operate" not in granted:
        raise BusinessError(
            "INVENTORY_OPERATE_REQUIRED",
            "批准库存 Proposal 需要 inventory:operate 权限",
            403,
        )
