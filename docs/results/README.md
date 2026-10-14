# 结果资料与复现

<a id="materialbrain-results-supporting-data"></a>

这里收录 [Results](../../RESULTS.md) 的聚合数据、评测方法、训练图和配对轨迹示例。CSV／JSON 可直接下载，数据 SHA256 见[方法与来源索引](METHODS.md#来源与下载索引)。

| 评测 | 阅读与下载 |
|---|---|
| 最新四臂 TEST120 | [四臂 CSV](FRESH_TEST120_ARMS.csv) · [配对统计、来源与审计索引](FRESH_TEST120_AGGREGATE.json) · [PNG 图](figures/fresh_test120_four_arm.png)／[SVG](figures/fresh_test120_four_arm.svg) · [Matplotlib 脚本](figures/make_fresh_test_figure.py) |
| VAL64、训练种子与完整 Nav2 转移 | [结果增补](CAREER_20261013_SUPPLEMENT.md) · [机器可读汇总](CAREER_SUPPLEMENT_DATA.json) · [VAL 七臂 CSV](FRESH_VAL64_ARMS.csv) · [VAL 所选臂配对 CSV](FRESH_VAL64_SELECTED_PAIRS.csv) · [Nav2 三臂 CSV](NAV2_FRESH12_ARMS.csv) · [路线诊断 CSV](ROUTE12_DIAGNOSTIC.csv) |
| 原始 Frozen120 | [三臂 CSV](THREE_ARM_FROZEN_TABLE_B.csv) · [三臂 JSON](THREE_ARM_FROZEN_TABLE_B.json) · [主配对报告](FROZEN_COMPARISON_B.json) · [PNG 图](figures/frozen_three_arm.png)／[SVG](figures/frozen_three_arm.svg) |
| 空间规划基准 | [CPU 规划结果](SPATIAL_CPU.md)，含 V3／V4 定义、1,000 项任务及 234 组 V3 配对比较 |
| 方法与实验历史 | [METHODS.md](METHODS.md)，含任务定义、监督量、VAL 选择、统计方法、算力和历史结果 |

新一轮两个 GRPO 训练种子共享同组 **120** 项任务与 S／Anchor 对照，两组轨迹重放与独立统计审计均通过。原始 Frozen120、新一轮 TEST120 与完整 Nav2 任务各自报告分母。

训练诊断与机制示例：

- [正式 16 步 TRAIN 图](figures/formal16_train_diagnostics.png)（[SVG](figures/formal16_train_diagnostics.svg)）展示逐更新 G8 成功数、终端奖励方差、reward／Anchor／KL 梯度及有效奖励更新；每步使用不同的预固定 TRAIN 组。
- [新增种子 TRAIN 图](figures/seed20261014_train_updates.png)（[SVG](figures/seed20261014_train_updates.svg)）来自已保存的 128 个 TRAIN episode。
- [机制汇总 JSON](MECHANISM_SUMMARY.json)汇总原始 Frozen120 的容量恢复动作差异。
- [配对轨迹示例](example/PAIRED_TRACE.md)（[JSON](example/PAIRED_TRACE.json)、[提取脚本](example/extract_trace.py)）展示输入顺序中的首个合格容量胜例，保留动作顺序、tick、结局和 episode 哈希；mission-scoped handoff ID 已遮盖。
- [图像清单](figures/FIGURE_MANIFEST.json)、[精简作图输入](figures/training_curve_inputs.json)与[原始两图生成脚本](figures/make_figures.py)记录作图来源和 SHA256。

## 复现步骤

核对原始 Frozen120 三份聚合副本的哈希及主表算术：

```bash
python docs/results/verify_tables.py
```

安装 Matplotlib 和 NumPy 后，从公开输入重画原始三臂图与正式 16 步 TRAIN 图：

```bash
python docs/results/figures/make_figures.py
```

公开资料包含聚合数据、精选作图输入和一条配对轨迹。完整 360 个原始 episode、权重与独立重放报告单独保存，档案哈希见[来源索引](METHODS.md#来源与下载索引)；episode 级重放和模型推理分别需要相应轨迹档案与权重。`verify_tables.py` 检查原始 Frozen120，最新四臂与增补数据的来源、重放和审计记录由各自 JSON 索引提供。

简历摘要：[当前版本](../../resume.md) · [早期 Frozen120 快照](../../resume-frozen120-snapshot.md)。
