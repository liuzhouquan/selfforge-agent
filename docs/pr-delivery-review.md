# PR Delivery 分支审查记录

基线：`2861c935d478c742e686c284a3230378c073a01b`。传入分支：`feat/pr-delivery-workflow`，原 HEAD `1fdf5e900d0f91befe7f5748ba524a85d666383c`；共 7 个提交、37 个文件，涵盖交付、环境识别、风险线索、计量和审批。审查依据为基线 `AGENTS.md`、开发指南、产品边界及 Roadmap；没有关联 Issue 或现成 PR 描述。

## Standards

- **硬性问题，已修正**：分支新增持久 worktree `remove --force`，违反 AGENTS 的安全清理约束。已移除该入口，保留显式 revert 和正常 Git remove。
- **设计判断，保留**：一个分支包含多个 Roadmap 功能，审查与回滚范围较大。后续变更应拆成可独立验证的小 PR。
- **API 清晰度建议，保留**：环境检测内部使用 Pydantic 模型但公开返回 dict；当前 CLI/API 消费者都使用 JSON 字段，此项没有已复现功能失败。

Standards 初审：1 项硬性问题、2 项设计判断；最严重的强制清理入口已移除。

## Spec

- **受保护暂存文件泄漏，已修正**：先暂存 `.env`，再修改正常源码，旧 commit 函数会提交完整 index，同时只在返回列表中显示安全文件。现在遇到预先暂存的受保护路径即拒绝，且所有修改路径使用 literal pathspec。
- **远端恢复不完整，已修正**：旧实现只能报告 push/gh 失败；PR 创建成功但响应丢失后无法通过重试恢复。现在按 head/base 查询并复用开放 PR，重复调用不再重复创建。
- 其余新增领域均能对应基线 Roadmap 中已规划的工作；没有发现需要另起 Runtime 的需求。

Spec 初审：2 项问题；最严重的受保护文件泄漏已修正。

## 组合路径复核

| 级别 | 触发与影响 | 修复与覆盖 |
| --- | --- | --- |
| P1 | revert 对未跟踪 symlink 调用 resolve 后 unlink，误删受跟踪目标；暂存新增/重命名也无法还原 | 只 unlink 链接本身；Git restore 处理完整 index/worktree 状态；先持久化证据失效 |
| P1 | PR 直接沿用会话布尔成功，把后续未测试提交或尚未提交的修复称为 Verified | 校验当前指纹和相对固定推送 SHA 的洁净状态；描述绑定明确 commit |
| P1 | 按消息条数截近期历史可能生成孤立工具回填；审批等待后过期/取消的 edit 仍可执行 | 保留完整 assistant/tool 块；批准后重查截止时间和取消 |
| P1 | Web response model 丢弃新增 Token 字段；混合模型按首模型计价、未定价调用被漏算 | 增加 API 字段；逐次汇总上报用量，显式费率，缺失数据时费用留空 |
| P1 | 仅凭 basename/附近行堆栈就宣布因果关联 | 路径完整匹配，精确行关联；保留 `causally_linked=false`，界面写作关联线索 |
| P1 | uv.lock 导出未验证包名/版本，且静默扁平化不同平台版本 | 校验字段及 SHA-256；不支持的版本分叉/缺失外部包哈希明确失败 |

## 验证与剩余范围

传入版本的本地全量基线：319 passed / 11 skipped。审查修复后的完整本地运行：339 passed / 11 skipped，252.02 秒；保留一条已有 Starlette/httpx 弃用警告。其后修复 protected rename 和 ignored/空目录边界，交付定向组合 22 passed；这些数量有重叠，不相加。最终完整组合与 Docker 验证以合并 PR 的精确提交 CI 为准。

新增的最初 8 项交付安全用例在旧实现上全部失败。二次独立复核又验证 protected rename 拒绝、ignored 输入/空目录为 Unverified、固定缓存仍能 Verified；原合并阻止项均已解决。Ruff、Git 空白检查和前端 JS 语法检查通过。完整本地运行的 11 项跳过需要 Docker daemon/镜像，不能当作本机隔离验证成功。

当前未完成：20 题 × 每题至少 3 次的正式真实模型验收、自动安装构建环境、因果反事实验证及模型专用精确 Tokenizer。当前运行没有新增模型成功率。外部进程并发修改、Git hooks/remote transport 与实际账单边界见[交付指南](./pr-delivery.md)和[计量规则](./token-metrics.md)。
