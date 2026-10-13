# Route B 方法、归因与证据边界

本页支持[公开 Results](../../RESULTS.md)的已完成数字。模型实验的执行源码身份为 `7783b638062fbb0a1f1e2f93868e9485ac8e5abb` 加逐文件哈希绑定的研究 adapter；后续应用交付源码身份为 `485dc2e86c9b98653b0289a22ae44d299f873b32`。两者分别指向训练／评测执行与应用交付，不能互相代替。

## 三臂与选择顺序

原始共同起点是 Qwen3.5-4B Recovery-SFT 检查点 S。`Anchor-only16` 和 `Anchor+GRPO16` 都从 S 重启，使用相同的 TRAIN Anchor 顺序、2,304 个监督 completion tokens、16 个优化步骤。Anchor 是 64 个新鲜、expert-verified 的 PUBLIC TRAIN 状态（CAP24、DEAD24、可行非拒绝16）；只监督完整结构化动作和 EOS，不监督 prompt。组合臂额外执行正式 16 组 G8 真实多步 SDK rollout，共 128 条训练轨迹，其中 12/16 步有非零奖励梯度；其余 4 步不计作奖励学习。Gate 权重在正式重启前丢弃。

TRAIN、VAL、TEST 的 `group_id`、`scene_seed`、`cut_state_sha256` 两两交集为零。训练池标为 Train256，正式执行只消费预先固定的前 16 组。B-VAL64 评估 S、Anchor-only checkpoint8/16、组合 checkpoint8/16；按预置的任务增益和拒绝／安全／授权不回退规则选择组合 checkpoint16，并在记录 promotion 后只打开一次三臂 TEST120。TEST 推理没有 optimizer update，也没有 Shield、专家动作替代或 arm-specific fallback。完整原始轨迹和独立审计保存在单独证据档案；此公开 packet 只含聚合报告及其核验程序。

## 指标怎么定义

`Task Success` 是原始 verifier 的任务判定，包括可行任务完成、正确拒绝和正确澄清。组合臂的 `120/120` 由 **87 SUCCESS、30 CORRECT_REFUSAL、3 CLARIFICATION** 组成，不是 120 次机器人配送。`Capacity Recovery` 分母是测试前固定的 **75 个可行容量冲突任务**，三个注册模板各 25；不可行 CAP15 与 DEAD15 的正确拒绝分别计数，不混入 75。

| TEST120 | S | Anchor-only16 | Anchor+GRPO16 |
|---|---:|---:|---:|
| Task Success | 72/120 | 78/120 | 120/120 |
| 可行容量冲突恢复 | 36/75 | 33/75 | 75/75 |
| CAP 正确拒绝 | 15/15 | 15/15 | 15/15 |
| DEAD 正确拒绝 | 6/15 | 15/15 | 15/15 |
| Safety episode | 0/120 | 0/120 | 0/120 |
| Unauthorized episode | 0/120 | 3/120 | 0/120 |

主归因只取**匹配监督量的 Anchor-only16 → Anchor+GRPO16**：Task `+35pp`、同任务 W/L/T=`42/0/78`、2,000 次 paired bootstrap 95% CI `[26.67,43.33]pp`；Capacity `+56pp`、`42/0/33`、CI `[45.33,66.67]pp`。两项 exact 双侧 McNemar p=`4.547473508864641e-13`。配对重采样每次保留同一任务的两臂，不能各自独立抽样。S→组合臂的 Task +40pp 包含 Anchor 监督；Anchor-only 自身把 DEAD 拒绝从 6/15 提到 15/15，同时出现 3 个容量回退和 3 个 unauthorized／invalid episode，全部保留。

原始 `all-feasible false-refusal` 标志在三臂都为 3/90；只读动作审计确认三个均是 verifier 认可的 `AMBIGUOUS_REFERENCE` 正确澄清。原始字段计数保持不变；容量专项误拒为 0/75。组合臂 TRAIN 128 条轨迹曾出现 3 个 safety、1 个 unauthorized episode（类别可能重叠），它们不因 TEST 零事件而被删除。

## 可观察的改善机制

在保存的 TEST 原始轨迹中，S 与组合臂的 75 条容量动作链的关键差异落在最后返航 HOME 的 `route` 参数：`safest → fastest`；当 S 超时，组合臂还能完成 `finish`。39 个零 slack 任务从 `DEADLINE_EXHAUSTED` 翻成 SUCCESS，36 个 slack4 任务原本 S 已成功。相对 Anchor-only 的 42 个胜利由这 39 个加 3 个 Anchor 自身回退的恢复组成。[可检查的公共摘要](MECHANISM_SUMMARY.json)保存原只读审计 SHA；[首个输入顺序合格的配对轨迹](example/PAIRED_TRACE.md)公开保存的完整动作顺序、tick、结局及原始 episode SHA，只有 mission-scoped handoff key 被遮盖。

这是一项事后观察性轨迹比较，未进行固定其他动作、单独改变 route 的参数介入；结论是“紧截止期返航参数选择改善”。既不能将整个分批装载技能说成新学会，也不能从 symbolic suffix 直接推断 Nav2 或实体仓库转移。

## 历史结果与资源

早期 P3 在旧 WarehouseBench-V1 上的 Base 为 14/120、SFT 为 120/120；该 SFT 用 12,513 个实际训练 decisions、2 epochs／1,566 steps。旧正式 GRPO 的 accepted reward-gradient updates 为 0，科学结论仍是 FAIL。旧 SFT Nav2 仿真 held-out 12/12、失败 GRPO checkpoint 11/12，是不同历史实验。

Route A 使用相同 PUBLIC-only Shield，S81/120→Gate-RL8 111/120、容量36/75→66/75，但模型原始 DEAD refusal 6/15→0/15、Shield 介入9→15。因此它只支持同保护层的**系统策略改善**，与 Route B 的无 Shield 模型结论分开。

整个 A/B 阶段含数值门槛、Gate、工程失败、正式训练和评测，实际分配时间 31,146 秒，即 **8.65167 A800h**。其中正式 GRPO16 是 2.76972 A800h，Anchor-only16 是 0.19056 A800h，三臂 Frozen 推理是 0.97139 A800h。A800h 按分配 GPU 数×壁钟计；环境模型的 mission cost 不是实测电耗或商业节省。

## 来源与可验证程度

| 公共副本 | 原始 SHA256 | 可核对内容 |
|---|---|---|
| [三臂 CSV](THREE_ARM_FROZEN_TABLE_B.csv) | `a898a703a6b30f9f9c6acf6453afc78dbc5e3da0e4f2897f2aa225397e8f8660` | 分子／分母、安全、工具与 cost |
| [三臂 JSON](THREE_ARM_FROZEN_TABLE_B.json) | `ac63f178b11b05a9d1e228e730f9f6a6ee4624ed445bdce237deaccda5b328d1` | 三臂及多重对照统计 |
| [主配对报告](FROZEN_COMPARISON_B.json) | `ce7451d01574b796a084ee57b81c37771a343d96306f7c52cfde075b1750fa16` | Anchor-only→组合臂配对结果及检查字段 |
| [机制公共摘要](MECHANISM_SUMMARY.json) | 摘要内记录原只读审计 SHA | 75／39／36／3 的观察性归因 |

运行 `python docs/results/verify_tables.py` 可在本 packet 中核对上述三个原始副本的 SHA 和主表算术。完整 A/B 证据 ZIP 的 SHA256 是 `794019260f11391fa2cf682b9b09b2e02015aabde86cca72b661991906bdbeed`；独立 B TEST 审计报告 SHA256 为 `2f9d5b8d96629b296fca4fd956b233e46281970690a6434015686970aefcbf75`。公开聚合 packet 增补一组路径卫生处理过的 S／RL 配对动作摘录；原始完整轨迹、权重和远端大文件仍未包含，因而仅凭此 packet 不能从头重新执行模型评测。

[三臂对照图](figures/frozen_three_arm.png)从上述 CSV 和配对 JSON 直接绘制，柱轴从零开始、标出实际分子／分母，区间只用于匹配监督量的 Anchor-only→GRPO 对照。[正式 16 步 TRAIN 诊断图](figures/formal16_train_diagnostics.png)来自原始训练摘要的选列公开副本；[图像与输入 SHA256 清单](figures/FIGURE_MANIFEST.json)记录摘要哈希 `6581acdb94052a24b2c4a1bb95f00704e730048887eef6ae03702bde66a11d2e`、精简输入、脚本及 PNG／SVG。训练图的每一步是**不同的预先固定 G8 TRAIN 组**，不是同一验证集上的连续测量；第 12、14、15、16 步奖励梯度为零，Anchor 和 KL 梯度仍有测量值。图像是已存原始结果的可复制作图，不包含新增 seed 或 Nav2 三臂结论。

本次 TEST 是注册模板家族内新场景种子的**关键状态后缀恢复**。训练 seed 只有原始一组；新增两组训练 seed 和新三臂 Nav2 对比在本版本均为 **PENDING**。新数据必须按完整固定分母报告，不能从旧已暴露 TEST 个例构造训练样本。[CPU 空间规划](SPATIAL_CPU.md)另有独立结果。
