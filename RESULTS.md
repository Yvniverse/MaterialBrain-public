# MaterialBrain Results

MaterialBrain 将工程 BOM、批次库存、PostGIS 仓库地图、OR-Tools 约束规划和 ROS2/Nav2 仿真接成可审计的仓储 Agent。模型选择结构化动作；服务器验证权限、业务事实、任务依赖和执行反馈。系统提供 **29 个类型化工具**，实验室语义图包含 **219 个节点、1,122 条有向边**。[空间规划数据](docs/results/SPATIAL_CPU.md)

## 核心模型结果

从同一个 Qwen3.5-4B Recovery-SFT 检查点出发，比较原始 S、只做保留监督的 Anchor-only16、以及相同监督加 GRPO16。两个正式训练臂都使用 **2,304 个监督 completion tokens、16 个优化步骤**。按独立 VAL 预选 checkpoint 后，进行一次无 Shield 的冻结 **120 项关键状态任务**评测。[方法与原始聚合数据](docs/results/README.md)

| 冻结 TEST120 | 原始 S | Anchor-only16 | Anchor+GRPO16 |
|---|---:|---:|---:|
| Task Success | 72/120 | 78/120 | **120/120** |
| 可行容量冲突恢复 | 36/75 | 33/75 | **75/75** |
| CAP／DEAD 正确拒绝 | 15/15 · 6/15 | 15/15 · 15/15 | **15/15 · 15/15** |
| Safety／Unauthorized episode | 0 · 0 | 0 · 3 | **0 · 0** |

主对照 **Anchor-only16 → Anchor+GRPO16** 的 Task Success 提升 **35 个百分点**（同任务配对 42 胜／0 负／78 平；95% CI 26.67–43.33pp），容量恢复提升 **56 个百分点**（42／0／33；95% CI 45.33–66.67pp）。120 个判定成功中，**87 个为执行成功、30 个为正确拒绝、3 个为正确澄清**。[三臂 CSV](docs/results/THREE_ARM_FROZEN_TABLE_B.csv) · [配对 JSON](docs/results/FROZEN_COMPARISON_B.json) · [离线核对脚本](docs/results/verify_tables.py)

![原始冻结三臂 Task Success 和容量恢复对照](docs/results/figures/frozen_three_arm.png)

[可缩放 SVG 图](docs/results/figures/frozen_three_arm.svg) · [作图输入、脚本与 SHA256](docs/results/figures/FIGURE_MANIFEST.json)

固定种子的 **1,000 项 CPU 规划**另外得到 **791 个独立审计可行解、0 个约束违规**，请求内规划 p50/p95 为 **406/622 ms**。这项规划结果与模型 TEST 分开计量。[规划完整口径](docs/results/SPATIAL_CPU.md)

<details>
<summary>实验设计、机制与证据范围</summary>

两个正式臂使用相同 Anchor 顺序、监督量与优化步数；组合臂额外采样 16 组 G8、共 128 条真实多步训练轨迹，其中 12 步产生非零奖励梯度。TEST 无 Shield、无权重更新，三臂共 360 个 episode。完整动作轨迹由独立审计重放；本公开 packet 提供字节一致的聚合副本与 SHA，原始全量轨迹和权重另行保存。[方法与哈希](docs/results/METHODS.md)

[原始 16 步 TRAIN 诊断图](docs/results/figures/formal16_train_diagnostics.png)展示每步不同固定训练组的成功数、终端奖励方差、三项梯度和累计有效奖励更新；第 12、14、15、16 步奖励梯度为零。每步训练组不同，图中成功数不应解释为同一验证集的学习曲线。[SVG](docs/results/figures/formal16_train_diagnostics.svg) · [精简作图输入](docs/results/figures/training_curve_inputs.json)

保存轨迹的只读对齐显示：75 个容量任务中，原始 S 与 RL 的关键差异集中于最后返航 HOME 的 `route=safest→fastest`。39 个零时间余量任务从截止失败转为成功；其余 36 个原已成功。相对 Anchor-only 的 42 个胜利还包含修复其自身 3 个回退。这是观察性机制证据，未进行单参数反事实介入。[首个合格配对任务的原始动作顺序](docs/results/example/PAIRED_TRACE.md)展示 S 在截止 tick 36 后失败、RL 按时完成；[公共机制摘要](docs/results/MECHANISM_SUMMARY.json)汇总全体。

本次 120 项是注册模板家族内新场景种子的**关键状态后缀任务**；它不代表 120 次机器人配送。新增两个训练种子及本轮三臂 Nav2 仍为 **PENDING**。

</details>

<details>
<summary>历史结果与负结果</summary>

旧 P3 在另一套已饱和基准上 Base14/120→SFT120/120，但旧正式 GRPO 有效奖励更新为 0，科学结论仍是 FAIL；旧 SFT 的 Nav2 仿真 12/12 属另一轮实验。Route A 在相同 PUBLIC-only Shield 下将 Task81/120 提到111/120，但模型 raw DEAD refusal 6/15→0/15，因此仅支持带保护层的系统改进。当前 Route B 的无 Shield、匹配监督对照单独回答模型增益。[历史与归因](docs/results/METHODS.md#历史结果与资源)

</details>

公开副本与验证说明：[docs/results/README.md](docs/results/README.md)。本页提供可离线核对的 CSV、JSON、源码身份和训练诊断图。
