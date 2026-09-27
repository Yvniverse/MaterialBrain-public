# API 说明

统一前缀 `/api/v1`，交互文档 `/api/docs`，OpenAPI `/api/openapi.json`。认证使用 `pengka_session` HttpOnly Cookie；非 GET 请求同时发送 `X-CSRF-Token`。库存写请求必须包含 8–100 字符的 `idempotency_key`。

已实现端点覆盖：`auth/login|logout|me|change-password`；用户与角色；物料 CRUD；分类、库位、供应商；库存 10 类操作；流水、低库存、盘点；项目 CRUD 与 BOM；采购单；CSV/XLSX/XLS 导入预览和提交；物料/流水导出；附件上传下载删除；仪表盘和审计日志。

可视化大库位通过 `POST /locations/organizers` 创建，`organizer_style` 支持 `standard_56`、`split_configurable` 和 `drawer_rack_100`。其中 `drawer_rack_100` 固定生成 20 行 × 5 列的 100 个抽屉，坐标从 A01–E01 排列至 A20–E20；抽屉内容继续使用 `/locations/{id}/content` 更新或清空。

用户与角色管理端点：

- `GET /users`、`POST /users`、`PUT /users/{id}`：查询、创建和调整用户。
- `DELETE /users/{id}`：安全删除用户，立即撤销其会话并从用户列表隐藏；历史库存、项目和审计外键记录保留。
- `GET /roles`、`POST /roles`、`PUT /roles/{id}`：查询、创建和更新角色权限。

不能删除或停用当前登录账号。用户删除、停用、换角色或角色权限更新后，后端还会校验系统至少保留一名同时拥有用户管理和角色管理权限的启用用户。

错误统一为 `{code,message,details,request_id}`。常见代码：`INSUFFICIENT_AVAILABLE_STOCK`、`INSUFFICIENT_PROJECT_RESERVATION`、`ADJUSTMENT_BELOW_RESERVED`、`VALIDATION_ERROR`。OpenAPI 是字段和响应的最终可执行参考。
