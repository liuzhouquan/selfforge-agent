# Token、上下文与费用

`SessionStore.token_metrics()` 汇总私有 trace 中供应商上报的输入、输出和总 Token，覆盖续跑会话。`total_tokens` 保留供应商上报值，不强制改写为输入与输出之和。混合模型通过 `models`/`providers` 列出；缺失或非法用量使 `usage_complete=false`。CLI `trace --summary` 和 Web 任务响应共用这份统计。

## 显式费率

默认不猜测任何模型的价格。需要费用估算时，操作者设置 `SELFFORGE_PRICING_JSON`，精确匹配响应中的供应商名与模型 ID。以下数字仅演示格式，不是任何供应商的真实报价：

```sh
export SELFFORGE_PRICING_JSON='{"provider-name":{"model-id":{"prompt_per_million":1.0,"completion_per_million":2.0,"source":"operator-rate-card","as_of":"2026-10-31"}}}'
```

单位为 USD / 百万 Token。`source` 应是可公开的费率来源或标识，`as_of` 是操作者核对日期。每次调用分别计价，再汇总；任一调用缺失费率或用量时，总费用为 null，避免把已知部分当成全部花费。格式错误、负值和非有限数字不能建立费用估算。

`calibrated=true` 表示使用了操作者显式配置的卡片，不表示已对账。缓存、折扣、路由差价及计费规则需由操作者纳入有效费率；当前双费率估算不是账单。参考测试中的价格为合成夹具，不是线上报价或 Benchmark 成绩。

## 上下文与审批

上下文使用语法/CJK 感知的启发式估算，没有接入各模型的精确 Tokenizer。长标识符和大量空白也计入预算。压缩保留系统约束、全部用户消息，并在空间允许时保留近期完整的 assistant/tool 块；一个 assistant 的多项工具回填不能被截成孤立 tool 消息。

CLI `code --interactive` 在执行工具前请求用户批准；拒绝会回填工具观察。批准后重新检查时间和取消状态，避免等待批准期间过期的调用继续修改文件。模型或工具本身的阻塞限制仍见[开发边界](./development.md)。
