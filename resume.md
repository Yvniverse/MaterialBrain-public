# MaterialBrain｜仓储工程 Agent 与 Agentic RL（2026.06—至今）

**技术栈：** Python、FastAPI、PostgreSQL/PostGIS、LangGraph、Qwen3.5-4B、PyTorch/LoRA、OR-Tools、ROS2/Nav2、Vue/Three.js

- 设计并实现 **29 个类型化 Agent 工具**与持久任务图，贯通工程 BOM、批次库存、仓库地图、拣货规划及 ROS2/Nav2 仿真；以服务端任务契约、权限和审批流程约束模型动作。
- 建立 **219 节点、1,122 条有向边**的 PostGIS 仓库语义图，结合 OR-Tools 处理时窗、载重、电池及充电约束；**1,000 项 CPU 规划**得到 **791 个独立审计可行解、0 个约束违规**，p50/p95 **406/622 ms**。早期 SFT 版本另在独立 Nav2 仿真评测中完成 **12/12**。
- 构建 Qwen3.5-4B 的 SFT、原生 GRPO 与 completion-only Anchor 训练链路：早期基准由 **14/120→120/120**；后续**无 Shield 冻结关键状态任务**中，相比同为 **2,304 监督 completion tokens、16 优化步骤**的 Anchor-only 对照，Task Success **78/120→120/120（+35pp）**，可行容量恢复 **33/75→75/75（+56pp）**。

[结果与方法](RESULTS.md) · [可核对的数据表](docs/results/README.md)
