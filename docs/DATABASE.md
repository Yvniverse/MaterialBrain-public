# 数据库说明

主要表：`users/roles/sessions` 负责身份；`materials/categories/locations/suppliers/inventory_lots` 负责主数据与库位库存；`stock_movements/idempotency_records/audit_logs` 负责一致性和追溯；`projects/bom_items/project_reservations` 负责项目；`stocktakes/purchase_orders/attachments` 负责业务记录。旧版 `loans` 表仅为兼容既有数据库迁移保留，应用不再提供借用相关接口或界面。

重要约束：物料编码、角色名、用户名、项目号、流水号唯一；项目与物料预留唯一；物料与库位 lot 唯一；幂等范围 `(user_id, endpoint, key)` 唯一。金额和数量使用 Decimal/Numeric，避免浮点误差。扩展属性与标签使用 JSON（PostgreSQL 可迁移为 JSONB 优化索引）。

迁移由 `backend/alembic/versions/0001_initial.py` 建立完整元数据。生产命令：`docker compose exec backend alembic current`、`docker compose exec backend alembic upgrade head`。不得手工编辑正式库存和历史流水；数据纠错使用盘点或冲正 API。
