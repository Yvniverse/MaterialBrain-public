# Route B 评测方法与实验历史

<a id="route-b-方法归因与证据边界"></a>

本页说明[Results](../../RESULTS.md)中模型结果的实验设计、指标和资源使用。原始 Frozen120、新一轮 TEST120、CPU 规划与完整 Nav2 任务各有独立任务集和分母。新一轮模型评测与 Nav2 结果详见[增补](CAREER_20261013_SUPPLEMENT.md)，规划器结果见[CPU 空间规划](SPATIAL_CPU.md)。

## 三臂与选择顺序

共同起点是 Qwen3.5-4B Recovery-SFT 检查点 S。`Anchor-only16` 和 `Anchor+GRPO16` 都从 S 重启，使用相同的 TRAIN Anchor 顺序、**2,304 个监督 completion tokens、16 个优化步骤**。Anchor 包含 64 个新冻结、expert-verified 的 PUBLIC TRAIN 状态（CAP24、DEAD24、可行非拒绝16），监督完整结构化动作和 EOS，prompt 不计入监督量。

组合臂额外执行 **16 组 G8** 真实多步 SDK rollout，共 **128** 条训练轨迹；**12/16** 步有非零奖励梯度，其余 4 步为 Anchor／KL 保留及优化器作用。Gate 权重在正式重启前丢弃。两正式臂匹配监督量与优化步数，GRPO 的采样和奖励计算使用额外算力，训练计算量并不相等。

TRAIN、VAL、TEST 的 `group_id`、`scene_seed`、`cut_state_sha256` 两两交集为零。Train256 是冻结训练池，正式 16 步消费预先固定的前 16 组。B-VAL64 评估 S、Anchor-only checkpoint8/16、组合 checkpoint8/16，按预置的任务增益及拒绝／安全／授权不回退规则选择组合 checkpoint16；记录 promotion 后，进行一次三臂 TEST120。TEST 使用相同推理路径，无 optimizer update、Shield、专家动作替代或按臂配置的 fallback。

<a id="指标怎么定义"></a>

## 指标定义与原始 Frozen120

评测从注册模板家族中、新场景种子生成的**冻结关键决策状态**开始，执行后续恢复动作。`Task Success` 采用原始 verifier 的判定，包含可行任务完成、正确拒绝和正确澄清。组合臂的 **120/120** 由 **87 SUCCESS、30 CORRECT_REFUSAL、3 CLARIFICATION** 组成。

`Capacity Recovery` 的分母为测试前固定的 **75 个可行容量冲突任务**，三个注册模板各 25。不可行 CAP15 与 DEAD15 的正确拒绝分别计数。完整初始任务从 HOME、空载、无历史开始，使用单列的 Nav2 转移评测。

| 原始 Frozen120 | S | Anchor-only16 | Anchor+GRPO16 |
|---|---:|---:|---:|
| Task Success | 72/120 | 78/120 | 120/120 |
| 可行容量恢复 | 36/75 | 33/75 | 75/75 |
| CAP 正确拒绝 | 15/15 | 15/15 | 15/15 |
| DEAD 正确拒绝 | 6/15 | 15/15 | 15/15 |
| Safety episode | 0/120 | 0/120 | 0/120 |
| Unauthorized episode | 0/120 | 3/120 | 0/120 |
| 工具调用（含固定前缀） | 1933 | 1906 | 1945 |
| 后缀工具调用 | 1559 | 1532 | 1571 |
| 逻辑 mission cost | 16.962 | 16.746 | 15.104 |
| 逻辑 suffix cost | 15.048 | 14.832 | 13.190 |

主对照为**匹配监督量的 Anchor-only16 → Anchor+GRPO16**：Task **+35pp**，同任务 W/L/T=`42/0/78`，2,000 次 paired bootstrap 95% CI **[26.67,43.33]pp**；Capacity **+56pp**，`42/0/33`，CI **[45.33,66.67]pp**。两项 exact 双侧 McNemar p 均为 **4.547473508864641e-13**。重采样保留同一任务的两臂配对。

S→组合臂的 Task **+40pp** 包含 Anchor 监督。Anchor-only 把 DEAD 拒绝从 **6/15** 提到 **15/15**，同时产生 **3** 个容量回退和 **3** 个 unauthorized／invalid episode。组合臂比 Anchor-only 多 **39** 次工具调用，逻辑 mission cost 由 **16.746** 降至 **15.104**；成功率、工具量与逻辑成本分别报告。

原始 `all-feasible false-refusal` 标志三臂均为 **3/90**。保存动作的重放确认这三个均为 verifier 接受的 `AMBIGUOUS_REFERENCE` 正确澄清，原始字段计数保留；容量专项误拒为 **0/75**。组合臂 TRAIN 的 128 条轨迹包含 **3** 个 safety、**1** 个 unauthorized episode，类别可能重叠，与 TEST 事件数分别记录。

## 可观察的改善机制

保存的 TEST 轨迹中，S 与组合臂的 75 条容量动作链主要差异落在最后返航 HOME 的 `route` 参数：`safest → fastest`。**39** 个零 slack 任务从 `DEADLINE_EXHAUSTED` 转为 SUCCESS，**36** 个 slack4 任务原已成功；相对 Anchor-only 的 **42** 个胜利由这 39 个加上 **3** 个 Anchor 回退的恢复组成。

[机制摘要](MECHANISM_SUMMARY.json)汇总这些计数，[首个输入顺序合格的配对轨迹](example/PAIRED_TRACE.md)展示动作顺序、tick 和结局。该比较支持“紧截止期返航参数选择改善”的解释，属于事后观察性分析；固定其他动作、单独改变 route 的参数介入尚未执行。

[正式 16 步 TRAIN 诊断图](figures/formal16_train_diagnostics.png)展示逐步 G8 成功数、终端奖励方差、reward／Anchor／KL 梯度和累计有效奖励更新。每步使用不同的预固定 TRAIN 组；第 **12、14、15、16** 步奖励梯度为零，Anchor 与 KL 梯度仍有测量值。图表是逐更新训练诊断，VAL 结果另表报告。

## 历史结果与资源

早期 P3 在旧 WarehouseBench-V1 上的 Base 为 **14/120**、SFT 为 **120/120**，SFT 使用 **12,513** 个实际训练 decisions、**2 epochs／1,566 steps**。旧正式 GRPO 的 accepted reward-gradient updates 为 **0**，结论为 **FAIL**。旧 SFT 的 Nav2 仿真 held-out 为 **12/12**，失败 GRPO checkpoint 为 **11/12**。这些是独立历史实验；Sprint、DPO 与各 Gate 的 FAIL 结论也保留。

Route A 使用相同 PUBLIC-only Shield，S **81/120→Gate-RL8 111/120**、容量 **36/75→66/75**；同时模型 raw DEAD refusal **6/15→0/15**、Shield 介入 **9→15**。它展示带保护层的系统策略改善，原始模型拒绝能力回退是该结果的一部分。Route B 则用无 Shield 的匹配监督对照评估新增 GRPO 效果。

| A/B 阶段 GPU 分配 | A800h |
|---|---:|
| 正式 GRPO16 | 2.76972 |
| Anchor-only16 | 0.19056 |
| 三臂 Frozen 推理 | 0.97139 |
| 整个 A/B 阶段（含数值门槛、Gate、工程失败、正式训练与评测） | **8.65167** |

整个阶段实际分配 **31,146 秒**。A800h 按分配 GPU 数×壁钟计。逻辑 mission cost 是任务环境的时间与能量单位加权值；规划器的资源预测、Nav2 遥测和硬件电耗是不同测量项。

## 新一轮评测与复现步骤

新一轮 TEST120 使用另一组冻结场景，原始 Frozen120 与新数据分别报告。新一轮两个已完成训练种子在同一组 120 项任务上评测，共享 S／Anchor 对照；独立样本分母为 **120**。三个事前声明训练种子中 **2** 个完成、**1** 个基础设施中断，状态与 VAL 选择见[增补](CAREER_20261013_SUPPLEMENT.md)。训练、选择和新 TEST 的划分沿用互不交叉原则，已暴露的旧 TEST 个例不进入新训练样本。

公开资料包含聚合 CSV／JSON、作图输入、脚本及一条配对轨迹摘录；完整轨迹档案与权重单独保存。以下命令可核对原始 Frozen120 三份聚合副本的 SHA256 和主表算术：

```bash
python docs/results/verify_tables.py
```

安装 Matplotlib 与 NumPy 后，运行以下命令可从公开输入重画原始三臂图与 TRAIN 诊断图：

```bash
python docs/results/figures/make_figures.py
```

完整 episode 重放需使用原始轨迹档案；重新执行模型推理还需对应权重。新一轮 TEST120 的轨迹重放与独立统计审计均通过，来源索引见[聚合 JSON](FRESH_TEST120_AGGREGATE.json)。

<a id="来源与可验证程度"></a>

## 来源与下载索引

| 源码用途 | 身份 |
|---|---|
| 模型训练／评测执行源码 | `7783b638062fbb0a1f1e2f93868e9485ac8e5abb`，加逐文件哈希绑定的研究 adapter |
| 后续应用交付源码 | `485dc2e86c9b98653b0289a22ae44d299f873b32` |

| 公共数据与档案 | SHA256／来源索引 |
|---|---|
| [原始三臂 CSV](THREE_ARM_FROZEN_TABLE_B.csv) | `a898a703a6b30f9f9c6acf6453afc78dbc5e3da0e4f2897f2aa225397e8f8660` |
| [原始三臂 JSON](THREE_ARM_FROZEN_TABLE_B.json) | `ac63f178b11b05a9d1e228e730f9f6a6ee4624ed445bdce237deaccda5b328d1` |
| [原始主配对报告](FROZEN_COMPARISON_B.json) | `ce7451d01574b796a084ee57b81c37771a343d96306f7c52cfde075b1750fa16` |
| [机制公共摘要](MECHANISM_SUMMARY.json) | 文件内记录原只读轨迹分析 SHA |
| 完整 A/B 证据 ZIP（另存） | `794019260f11391fa2cf682b9b09b2e02015aabde86cca72b661991906bdbeed` |
| 原始 B TEST 独立审计报告（另存） | `2f9d5b8d96629b296fca4fd956b233e46281970690a6434015686970aefcbf75` |
| [图像与输入清单](figures/FIGURE_MANIFEST.json) | 包含 TRAIN 摘要 SHA `6581acdb94052a24b2c4a1bb95f00704e730048887eef6ae03702bde66a11d2e`、精简输入、脚本及 PNG／SVG 哈希 |
| [新一轮四臂数据](FRESH_TEST120_ARMS.csv)与[聚合索引](FRESH_TEST120_AGGREGATE.json) | 源文件、训练种子及独立统计审计身份 |
| [增补数据索引](CAREER_SUPPLEMENT_DATA.json) | VAL、Nav2、路线诊断、图及冻结 runner 身份 |

[原始三臂图](figures/frozen_three_arm.png)使用 CSV 与配对 JSON 作图，柱轴从零开始并标注实际分子／分母；区间对应匹配监督量的 Anchor-only→GRPO 对照。公开配对摘录遮盖了 mission-scoped handoff key，动作顺序、tick、结局与原始 episode 哈希保留。全部公开数据见[结果资料索引](README.md)。
