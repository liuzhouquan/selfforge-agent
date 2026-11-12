# PR #61–#63 审查与整合

2026-11-12；基点 `19882cc1cf7d6c29309e677c1449b5154c6f8e1c`。按用户授权审查、修复并依次合并，保留原分支提交。任务状态只维护于 [SDD](./SDD.md)。

## Standards

规范轴确认 5 项缺陷，均已实际复现并修复：

- #61 入口先 resolve，使策略文件的 symlink 父目录绕过拒绝规则；改为先验证并读取原始路径。
- #61 接受无限时间预算；两个时间字段现拒绝非有限数值。
- #61 接受执行次数、成功次数、成功率与执行状态互相矛盾的成绩；补上状态、计数、比例及计划尝试数检查。
- #62 回归请求只核对 job/session；切到 B 再回到 A，或还原/清理/启动新任务后，旧响应可重新显示 confirmed。加入请求代次，success/catch/finally 均检查，状态失效立即递增代次。
- #63 导出在 fstat 前阻塞 open 并 O_TRUNC；检查后替换为 FIFO 会阻塞，类型拒绝前也可能截断文件。改为非阻塞打开，确认普通文件后才 ftruncate。

本轴最严重项为 #62 的旧成功证据重现；没有把一般命名或抽象偏好当作阻断项。

## Spec

规格轴确认 6 类缺口：上述 symlink、无限预算、矛盾成绩、迟到 UI 结果、导出竞态，以及 #61 镜像条目自带 lockfile/hash 没有被核验。现在镜像锁路径与摘要必须同时提供，并实际读取检查摘要，独立于环境锁索引检查。

分别对应 SF-EVAL-08 的冻结输入/预算/成绩约束、SF-TEST-02-A3 的状态失效、SF-WS-05-A3 的非普通文件拒绝。本轴最严重项为 #62 无效结果重新显示。两轴合计 **6 个独立缺陷**，全部修复。

另修正文档的绝对表述：显式重新构建可以因缓存得到相同内容 ID，应记录本轮实际解析的镜像 ID，不能凭“重新构建”声称一定不同。未为制造新 ID 人为修改镜像字节。

## 实测与范围

- 修复前实际反例：策略路径/无限预算/镜像锁 4 个失败用例，矛盾成绩 4 个失败用例；UI 8 组迟到成功/错误用例失败；导出 FIFO 超过 3 秒仍阻塞，以及被拒绝前已有文件被清空。修复后这些反例全部通过。
- 三模块专项：`uv run --no-sync pytest -q tests/test_ws_cli_export_and_storage_parity.py tests/test_eval_v3_policy.py tests/test_coding_web_regression_ui.py -m 'not integration'` → **35 passed, 1 deselected**。该 deselected 为集成测试，已由完整组合覆盖。
- 完整组合：`SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q` → **691 passed，0 skipped**，677.08 秒；1 条既有 Starlette/httpx 弃用警告。
- 完整测试源码提交 `e4ab2dc6c23879be1ad558425bbe0d50fb81a710`；交付 head `f88c2b95355869834e9c4f8bb406dfc6f97baea9` 只补入 main 历史，二者 tree 相同。后续状态对账只改文档。
- Ruff、SDD 检查与 `node --check src/selfforge_agent/api/static/app.js`、空白检查通过。SDD 检查不证明测试执行或语义充分。
- 显式准备五个真实固定 GitHub 源，显式构建基础镜像与使用既有哈希 wheelhouse 的 itsdangerous 镜像。运行 22 题独立 baseline 负控及 reference visible/holdout 正控，均使用禁网 Docker；没有模型构造或付费调用，没有改写历史 v2 资产或 53/60 成绩。
- 本机镜像：基础 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`；锁定依赖 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。
- 真实 Chromium / agent-browser smoke：FastAPI 正确提供页面和静态资产，API 使用明确替身；实际点击回归按钮，确认 A→B→A 旧响应隐藏、正常报告可见、Host 禁用、Coding 验证标记保持不变。桌面与 390×844 布局检查，移动视口无横向溢出。它不是模型或真实仓库的浏览器端到端运行；后端真 Docker 由独立集成测试验证。浏览器及临时预览进程已退出。

## 远端交付

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#61](https://github.com/liuzhouquan/selfforge-agent/pull/61) | `888ad43b3d63bece537b0fdc0a974e46be9d2dc2` | [43228769934](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43228769934)：653 单元 + 15 Docker，通过 | `9db8fc42bf60d25c4826cee1b918de3c6a4aa845` |
| [#62](https://github.com/liuzhouquan/selfforge-agent/pull/62) | `d753221fc4a2faa6d6f943c55374611cc8b6490f` | [43229735317](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43229735317)：667 单元 + 15 Docker，通过 | `22d7b4dc3c110b7a21016f83ace184143707ce13` |
| [#63](https://github.com/liuzhouquan/selfforge-agent/pull/63) | `f88c2b95355869834e9c4f8bb406dfc6f97baea9` | [43231456227](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)：676 单元 + 15 Docker，通过 | `473f04d9f8d8b394801b86a6f9f6e9a386821274` |

不同版本的计数不相加。合并时 GraphQL 请求遇到瞬时 EOF，#63 改用 REST 在同一准确 head SHA 校验下成功合并，没有绕过保护。最终 main 的准确 CI 运行和结论回填 [PR #63 交付描述](https://github.com/liuzhouquan/selfforge-agent/pull/63)，避免文档为记录自身 SHA 而递归提交。
