# Coding 工作区交付

CLI 与 Web 共用 `runtime.delivery`。交付操作由用户显式发起；Agent 工具列表不包含 commit、push 或创建 PR。

```sh
uv run selfforge-agent workspaces diff SESSION_ID --repo ./project
uv run selfforge-agent workspaces commit SESSION_ID --repo ./project -m '修复回归'
uv run selfforge-agent workspaces pr SESSION_ID --repo ./project --title '修复回归' --base main
```

## 提交与回退

- commit 暂存可见的修改与未跟踪文件。`.env`、密钥目录等受保护路径不会新加入暂存区；如果这些路径已经被暂存，整个操作会拒绝，保留现场供用户处理。Git 路径按字面值传递，避免文件名被解释为 pathspec 规则。
- Diff 为有界预览，文件多时会省略部分内容。commit 会提交全部允许的变更，暂未提供逐文件挑选；大量改动应在本地 Git 中完整检查。
- `workspaces revert` 显式丢弃允许路径中的未提交改动，包括暂存的新文件、删除和重命名，并删除未跟踪文件。symlink 只删除链接本身。受保护路径保留，因此有本地凭据的工作区仍可能不能直接清理。
- 回退先保存测试证据失效事件，日志写入失败时停止修改。持久工作区的 remove 保留 Git 的脏工作区检查，不提供强制删除；分支在移除后保留。

## Push、PR 与恢复

PR 操作固定一个 commit SHA，正常 push 到所选 remote 的工作区分支，不强推。随后通过 `gh pr list` 查询同 head/base 的开放 PR：已存在则返回该 PR；否则创建。若 GitHub 已创建 PR 而响应丢失，用户重试即可找回。未指定 base 且有多个候选时需明确选择 base。

PR 描述中的测试结论明确绑定到推送的 SHA。只有会话保存的内容指纹与当前工作区相同、且指纹范围内的路径相对该 SHA 没有未提交变更时才写为 Verified；其他情况写为 Unverified。检查包含 Git 忽略的输入文件和未跟踪空目录；固定排除的缓存仍不计入。扫描无法完整完成时也不建立结论。提交本身不会替代测试。已有 PR 的描述不会被自动覆盖，旧描述中的 SHA 仍指向原来的验证对象。

push 或 `gh` 失败会保留本地提交和工作区，供用户修复 remote/权限后重试。应用不自动评论或合并 PR。

## 执行边界

Web 使用既有跨进程 workspace guard，阻止交付、清理与 Web Coding 同时修改同一工作区。CLI 与外部编辑器/其他 Git 进程之间尚无统一锁；指纹不是原子快照，最后检查后的竞态仍需单独治理。

Git/gh 操作使用本机已有 Git 配置、凭据助手、hooks 和 remote transport；这些显式交付操作不在 Docker 命令沙箱内。Docker 隔离仍用于 Agent 命令和测试。

回归入口：`tests/test_workspace_delivery.py`、`tests/test_delivery_safety.py`、`tests/test_delivery_revert_and_lock_export.py`。本次审查和验证记录见[分支复核](./pr-delivery-review.md)。
