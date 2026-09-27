# MaterialBrain 手工测试题库

这是一个完全离线的人工评测页面，不会调用 Agent API，也不会上传导入的真实公司数据。

1. 通过双重只读的 Production Shadow Generator 在本机生成
   `backend/.local-evals/production-shadow/shadow_cases.jsonl`。
2. 直接用浏览器打开 `MaterialBrain_Manual_Test_Bank.html`。
3. 导入上述 JSONL，在真实应用里逐题询问模型，再人工记录答案、结论和备注。
4. 按 `qwen3.5-flash`、`qwen3.6-flash`、`qwen3.7-flash` 分别记录；不得调用任何
   `qwen3.8` 模型。
5. 导出的 CSV 和浏览器 `localStorage` 都可能包含真实公司信息，只能留在本机，禁止提交 Git。

这个页面不自动判断答案正确与否。人工结果未实际录入时，报告必须明确标记为“待人工评测”，不能推断或补造结果。
