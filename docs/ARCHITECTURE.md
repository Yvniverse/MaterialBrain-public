# 架构说明

系统采用同源三层架构。Nginx 是唯一局域网入口：`/` 转发到 Vue 静态站点，`/api/` 转发到 FastAPI。后端使用 SQLAlchemy 会话连接 PostgreSQL，数据库端口只存在于 Docker internal 网络。附件和备份通过宿主机目录持久化。

后端分为 `api`（输入与权限）、`schemas`（Pydantic 边界）、`services`（库存/审计规则）、`models`（持久化）、`core`（配置、安全、数据库）与 `seed`。库存数量只有 `InventoryService` 能修改。标准写路径为：认证和权限 → 幂等查询 → `SELECT ... FOR UPDATE` → 业务约束 → 数量变更 → 业务记录 → 库存流水 → 幂等响应 → 提交；任一步失败回滚。

关键设计：数据库检查约束保证 `quantity >= 0`、`reserved_quantity >= 0`、`reserved_quantity <= quantity`；物料编辑 DTO 不包含库存字段且拒绝额外字段；流水无更新和删除 API；冲正创建反向流水；会话保存在数据库，适合多后端实例；前端只展示服务端返回的最终库存。

离线边界：运行镜像首次拉取和依赖安装需要互联网或内部镜像仓库；镜像构建完成后，日常登录、查询、操作、附件与备份均不调用任何外部服务。
