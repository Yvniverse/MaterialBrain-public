# MaterialBrain Results

MaterialBrain 将工程 BOM、批次库存、PostGIS 仓库地图、OR-Tools 约束规划和 ROS2/Nav2 仿真接成仓储 Agent。模型选择结构化动作，服务器验证权限、业务事实、任务依赖和执行反馈。系统提供 **29 个类型化工具**，实验室语义图包含 **219 个节点、1,122 条有向边**。[空间规划](docs/results/SPATIAL_CPU.md)

<a id="新一轮冻结-test120四臂聚合结果"></a>

## 最新模型评测：四臂 TEST120

在同一组 **120 项冻结关键状态恢复任务**上，比较 Qwen3.5-4B Recovery-SFT（S）、Anchor-only16 和两个完成训练的 GRPO 种子。评测从已保存的关键决策状态继续执行；Task Success 包括可行任务完成、正确拒绝和正确澄清。匹配的 Anchor／GRPO 臂各使用 **2,304 个监督 completion tokens、16 个优化步骤**，经独立 VAL 选择 checkpoint，TEST 直接评估冻结模型策略。[评测方法](docs/results/METHODS.md) · [四臂 CSV](docs/results/FRESH_TEST120_ARMS.csv)

| 新一轮 TEST120 | 原始 S | Anchor-only16 | GRPO 种子 08 | GRPO 种子 14 |
|---|---:|---:|---:|---:|
| Task Success | 71/120 | 77/120 | **120/120** | **120/120** |
| 可行容量恢复 | 35/75 | 32/75 | **75/75** | **75/75** |
| CAP／DEAD 正确拒绝 | 15/15 · 6/15 | 15/15 · 15/15 | **15/15 · 15/15** | **15/15 · 15/15** |
| Safety／Unauthorized episode | 0 · 0 | 0 · 2 | **0 · 0** | **0 · 0** |

相对匹配的 Anchor-only16，两个 GRPO 种子的配对结果相同：

| 主对照 | 提升 | 同任务胜／负／平 | Paired bootstrap 95% CI |
|---|---:|---:|---:|
| Task Success | **+35.83pp** | **43／0／77** | **27.5–45pp** |
| 可行容量恢复 | **+57.33pp** | **43／0／32** | **46.67–68pp** |

各自两项 exact 双侧 McNemar p 均为 **2.2737367544323206e-13**。两个种子共享这组 **120** 项任务及 S／Anchor 对照。三个预声明训练种子的完成状态、VAL 选择与轨迹核验见[评测记录](docs/results/CAREER_20261013_SUPPLEMENT.md)。

![新一轮 TEST120 四臂 Task 与容量恢复](docs/results/figures/fresh_test120_four_arm.png)

[可缩放 SVG](docs/results/figures/fresh_test120_four_arm.svg) · [图表生成脚本](docs/results/figures/make_fresh_test_figure.py) · [来源与配对数据](docs/results/FRESH_TEST120_AGGREGATE.json)

任务成功率提升伴随工具调用增加：S／Anchor／任一 GRPO 分别为 **1934／1910／1945** 次，逻辑 mission cost 为 **16.904／16.746／15.104**。这里的 cost 是任务环境中的时间与能量单位加权值；Nav2 行驶距离、时间和估算能耗另行记录。

## CPU 空间规划：1,000 项固定任务

语义路网与 OR-Tools 约束规划得到 **791 个满足约束的计划、192 个正确不可行拒绝、17 个未解任务**，约束违规、几何碰撞和程序错误均为 **0**；请求内规划 p50／p95 为 **406／622 ms**。网格路径与停靠顺序优化在 **234 组双方可行的配对任务**上，相对输入顺序将总目标降低 **26.39%**、总距离降低 **32.21%**。另有 242 组跨规划器配对，语义路网方案相对网格方案的距离为 **+7.87%**。[空间规划评测结果](docs/results/SPATIAL_CPU.md)

## 训练机制示例：紧截止期返航

原始 Frozen120 的保存轨迹中，75 个容量任务的主要动作差异出现在最后返航 HOME：S 选择 `route=safest`，GRPO 选择 `route=fastest`。其中 **39** 个零时间余量任务由截止失败转为成功，另 **36** 个任务原已成功；相对 Anchor-only 的 **42** 个胜利还包括其自身 **3** 个回退的恢复。

[配对轨迹示例](docs/results/example/PAIRED_TRACE.md)展示两臂前 **15** 步动作相同，第 **16** 步在 tick **30** 返航：S 于 tick **39** 超过截止 tick **36**；GRPO 于 tick **35** 调用 `finish()`，在 tick **36** 完成，`route_cost` 为 **16→12**。上述机制来自保存轨迹的观察性分析。[机制汇总](docs/results/MECHANISM_SUMMARY.json)

正式 GRPO16 额外采样 **16 组 G8、128 条多步训练轨迹**，其中 **12** 步产生非零奖励梯度；第 **12、14、15、16** 步奖励梯度为零。每步使用不同的预固定 TRAIN 组，训练图展示逐更新诊断。[TRAIN 诊断图](docs/results/figures/formal16_train_diagnostics.png) · [SVG](docs/results/figures/formal16_train_diagnostics.svg) · [方法与算力](docs/results/METHODS.md)

<a id="val-选择完整任务转移与路线诊断"></a>

## 完整 Nav2 任务转移：6/12、6/12、3/12

三个冻结策略在 ROS2/Nav2 仿真中执行同一批 **12 个完整初始任务**，从 HOME、空载、无历史开始。任务成功要求合法 verifier、实际 Nav2 到达及返回 HOME。

| 指标 | S | Anchor16 | GRPO16 |
|---|---:|---:|---:|
| Task Success | **6/12** | **6/12** | **3/12** |
| 实际 Nav2 到达 | 6/12 | 6/12 | 7/12 |
| 终态 HOME | 12/12 | 12/12 | 8/12 |

Anchor16→GRPO16 的 Task 为 **−25pp**，同场景 **1 胜／4 负／7 平**；关键状态恢复的增益在本次完整任务转移中没有重现。全部 **36** 次尝试包括 **15 次成功、10 次策略动作／verifier 失败、3 次响应超时和 8 次原因未定位的异常**。终态 HOME 包含从未离开 HOME 的失败任务。[分任务结果、配对区间与失败分析](docs/results/CAREER_20261013_SUPPLEMENT.md#nav2-full-tasks)

固定验证集路线诊断中，三臂均 **12/12** 完成，首次选路均 **10/12**。两例封闭最快路线的阻塞旅行，S／Anchor／GRPO 为 **2／2／28** 次，工具调用为 **218／218／244**；逻辑 mission cost 为 **2.152／2.172／1.984**。GRPO 的较低逻辑成本伴随更多阻塞和工具调用，首次条件选路未见改善。[路线诊断](docs/results/CAREER_20261013_SUPPLEMENT.md#route-diagnostic)

<details>
<summary>原始 Frozen120 与历史实验</summary>

### 原始 Frozen120：先前验证

原始 Frozen120 与上方新一轮 TEST120 使用不同冻结任务集。原始实验从同一 Qwen3.5-4B Recovery-SFT 检查点出发，经独立 VAL 选择后评测三臂：

| 原始 Frozen120 | 原始 S | Anchor-only16 | Anchor+GRPO16 |
|---|---:|---:|---:|
| Task Success | 72/120 | 78/120 | **120/120** |
| 可行容量恢复 | 36/75 | 33/75 | **75/75** |
| CAP／DEAD 正确拒绝 | 15/15 · 6/15 | 15/15 · 15/15 | **15/15 · 15/15** |
| Safety／Unauthorized episode | 0 · 0 | 0 · 3 | **0 · 0** |

匹配监督主对照的 Task 提升 **35pp**（42／0／78，95% CI **26.67–43.33pp**），容量恢复提升 **56pp**（42／0／33，CI **45.33–66.67pp**）。GRPO 的 120 项成功由 **87 个执行成功、30 个正确拒绝、3 个正确澄清**组成。[三臂 CSV](docs/results/THREE_ARM_FROZEN_TABLE_B.csv) · [配对 JSON](docs/results/FROZEN_COMPARISON_B.json)

![原始冻结三臂 Task Success 和容量恢复对照](docs/results/figures/frozen_three_arm.png)

[SVG](docs/results/figures/frozen_three_arm.svg) · [作图输入、脚本与 SHA256](docs/results/figures/FIGURE_MANIFEST.json)

旧 P3 在另一套基准上为 Base **14/120**→SFT **120/120**；正式 GRPO 的有效奖励更新为 **0**，结论为 **FAIL**。旧 SFT 的 Nav2 仿真 **12/12** 属于该轮实验。Route A 在相同 PUBLIC-only Shield 下将 Task **81/120→111/120**，同时 raw DEAD refusal **6/15→0/15**；带保护层的系统改善与当前无 Shield 的模型对照分别报告。历史 Sprint、DPO 与各 Gate 的 FAIL 结论保留。[历史结果与资源](docs/results/METHODS.md#历史结果与资源)

</details>

数据、来源哈希与复现步骤：[结果资料索引](docs/results/README.md)。
