# MaterialBrain Results supporting data

This directory is a self-contained public support packet for [the concise Results page](../../RESULTS.md). The three frozen A/B data files below are byte-identical copies of the measured aggregate artifacts; their SHA256 values are recorded in [METHODS.md](METHODS.md). Links are relative so the packet can be placed in a public repository or shared as a ZIP without depending on access to the private source repository.

- [THREE_ARM_FROZEN_TABLE_B.csv](THREE_ARM_FROZEN_TABLE_B.csv): published three-arm numerators, denominators, safety and cost.
- [THREE_ARM_FROZEN_TABLE_B.json](THREE_ARM_FROZEN_TABLE_B.json): machine-readable three-arm comparison.
- [FROZEN_COMPARISON_B.json](FROZEN_COMPARISON_B.json): matched Anchor-only versus Anchor+GRPO paired statistics and acceptance fields.
- [MECHANISM_SUMMARY.json](MECHANISM_SUMMARY.json): public, path-free summary of the later read-only action-sequence audit.
- [Saved paired example](example/PAIRED_TRACE.md) ([JSON](example/PAIRED_TRACE.json), [read-only extraction script](example/extract_trace.py)): first input-order eligible S deadline failure versus GRPO success, preserving original action order, ticks and episode hashes with mission-scoped handoff IDs redacted.
- [METHODS.md](METHODS.md): frozen protocol, metric definitions, attribution, historical results and limits.
- [SPATIAL_CPU.md](SPATIAL_CPU.md): independently measured planning benchmark and its distinct V3 comparison.
- [verify_tables.py](verify_tables.py): offline check of the copied file hashes and headline arithmetic (`python docs/results/verify_tables.py`).
- [Frozen three-arm figure](figures/frozen_three_arm.png) ([SVG](figures/frozen_three_arm.svg)): zero-baseline Task and Capacity bars with observed counts and matched paired intervals.
- [Formal-16 TRAIN diagnostics](figures/formal16_train_diagnostics.png) ([SVG](figures/formal16_train_diagnostics.svg)): per-update G8 success, terminal reward spread, reward/Anchor/KL gradient and accepted reward updates. Each update uses a different pre-fixed TRAIN group.
- [Figure manifest](figures/FIGURE_MANIFEST.json), [selected plot inputs](figures/training_curve_inputs.json), and [Matplotlib reproduction script](figures/make_figures.py). Run `python docs/results/figures/make_figures.py` with Matplotlib and NumPy installed; the supplied selected input reproduces both plots without the private full trace archive.

The copied JSON and CSV verify the reported **aggregate** figures; the paired example is one public-safe excerpt. They do not contain model weights or all 360 raw episode traces. The original trace archive and independent replay receipts remain separately retained, with their hashes documented in [METHODS.md](METHODS.md); full episode-level reproduction requires access to that archive. New Nav2 three-arm and additional training-seed results are **PENDING** in this snapshot.
