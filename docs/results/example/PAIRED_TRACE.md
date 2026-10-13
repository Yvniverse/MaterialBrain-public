# 一条已保存的冻结 TEST 配对轨迹：返航时窗

这是按原始 `scenarios-test.jsonl` **输入顺序选出的首个合格容量胜例**，用于解释已报告结果，不参与训练、模型选择或新增评测。两个模型从相同的冻结场景与前缀状态开始；完整的原始动作顺序及其保存时的 tick 见下表，[机器可读副本](PAIRED_TRACE.json)记录源文件与 episode 哈希。

场景 `group_id=b5e11810f0df144e719a78cbdf8c6bbf1c89cef6394c8df37264dd620ebee896`，模板 `batch_reload`，`scenario_sha256=029f1f4bfc70f7439f15248a35fa95d40ff890f9af683caf34ce1cae37d061ca`，`cut_state_sha256=558c2c3da5e8a4aa955be777a7a220efd2405680fef5bccd7e66194545d949fa`。截止 tick **36**；预设 slack **0**。第 1 行即首个符合规则的输入。

| 步 | S：动作（执行前 tick／剩余） | Anchor+GRPO16：动作（执行前 tick／剩余） |
|---:|---|---|
| 1 | `load(S02)` · 1／35 | `load(S02)` · 1／35 |
| 2 | `query_routes()` · 3／33 | `query_routes()` · 3／33 |
| 3 | `travel(S02, fastest)` · 4／32 | `travel(S02, fastest)` · 4／32 |
| 4 | `inspect_dock()` · 7／29 | `inspect_dock()` · 7／29 |
| 5 | `scan(BIN-806362)` · 8／28 | `scan(BIN-806362)` · 8／28 |
| 6 | `handoff([redacted id])` · 9／27 | `handoff([redacted id])` · 9／27 |
| 7 | `query_routes()` · 12／24 | `query_routes()` · 12／24 |
| 8 | `travel(HOME, fastest)` · 13／23 | `travel(HOME, fastest)` · 13／23 |
| 9 | `load(S01)` · 16／20 | `load(S01)` · 16／20 |
| 10 | `query_routes()` · 18／18 | `query_routes()` · 18／18 |
| 11 | `travel(S01, fastest)` · 19／17 | `travel(S01, fastest)` · 19／17 |
| 12 | `inspect_dock()` · 24／12 | `inspect_dock()` · 24／12 |
| 13 | `scan(REV2-672857)` · 25／11 | `scan(REV2-672857)` · 25／11 |
| 14 | `handoff([redacted id])` · 26／10 | `handoff([redacted id])` · 26／10 |
| 15 | `query_routes()` · 29／7 | `query_routes()` · 29／7 |
| 16 | `travel(HOME, safest)` · 30／6 | `travel(HOME, fastest)` · 30／6 |
| 17 | — | `finish()` · 35／1 |

前 **15 步**动作及参数相同。第 16 步两者都从 tick **30** 返航 HOME，剩余 **6 ticks**；S 的原始参数为 `route=safest`，组合臂为 `route=fastest`。S 在 tick **39** 以 `DEADLINE_EXHAUSTED` 结束，超过 tick 36；组合臂在 tick **35** 发出第 17 步 `finish()`，并于 tick **36** 以 `SUCCESS` 结束。记录的总 `route_cost` 为 **16→12**。

这是保存轨迹的观察性比较：共同前缀与最后返航参数差异支持对失败路径的解释，但未执行只改变一个参数的反事实实验。两个 `handoff` 动作中的 mission-scoped key 已在公开副本中遮盖；其动作顺序、其他参数、tick、结局与原始哈希保持可核对。该 TEST 案例已暴露，不可回填训练或用于调参。

## 原始证据 SHA256

| 证据 | SHA256 |
|---|---|
| `scenarios-test.jsonl` | `050300aa2a170fc0f0a093b8c8f5608e4ded9e87e789f98d463e251bcf171176` |
| `S-traces.jsonl` | `65031245fffd722dfd76a82ce47a6e282e68eeb9270fb7bcc7af5c5d69fff9dd` |
| `S+Anchor+GRPO-ck16-traces.jsonl` | `50bca196a3ec9f955719319d9e0ad02258f7829d3eed75085562504a6dd699f8` |
| `READ_ONLY_RESULT_AUDIT.json` | `e5afdaff10bad59abe3d2411686343df3304cc1c190bd3208ea5bc8ec96739f4` |
| `S` episode JSONL 原始行 | `5e181fef3bf7b873a57bdd41b8be1b690a4395c4aa343cf2947f10d766ccf2a1` |
| `S` semantic_trajectory_sha256 | `b88e95741ef59a96277fb93778eabc3cd5abebdf7c1aba08138883e953db9520` |
| `S` diagnostic_trace_sha256 | `296dc5014e79e007f35872b2f6940b89b8fc09df61d54214bc934e57c98d4f0c` |
| `S` harness_trace_sha256 | `1c790412b2e1ab7bc0068d609f1bcbcf05c6460b97ed2c0697b4faafe9c4994d` |
| `S+Anchor+GRPO-ck16` episode JSONL 原始行 | `91ddc4e2613a694498b744aafad6b32bd4e0e72d397b86995a6431de617b28a4` |
| `S+Anchor+GRPO-ck16` semantic_trajectory_sha256 | `0fe8ab378ee2463183f517055343f81b7149501762d907a39ce398a6d34e6a19` |
| `S+Anchor+GRPO-ck16` diagnostic_trace_sha256 | `4fb0c9a90b6d75fd1714061daa4c5956acf4e80384c44f4971127cd9a3911394` |
| `S+Anchor+GRPO-ck16` harness_trace_sha256 | `0fdf76b6605f46e62a6ee93754c017c51b2ead1541462d38b138d98d0077fe1a` |

本公开摘录省略了原始模型 token 序列、内部观测和运行路径；[JSON](PAIRED_TRACE.json)保存核验该例所需的场景、序列及结局字段。
