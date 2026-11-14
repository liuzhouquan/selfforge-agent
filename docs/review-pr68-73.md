# PR #68–#73 审查与整合

2026-11-14；基点 `10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`。按用户授权审查、另加修复提交并依次合并，保留六个分支的原始历史；[SDD](./SDD.md) 是当前任务状态入口。

## Standards

规范轴初审确认 6 项，最终只读复核无剩余阻断：

- #68 Host 会话仍请求并显示 Docker 持久记录；现在仅明确记录 Docker 环境、具有独立分支且停止运行的会话可以恢复。
- #69 `generate-test --auto-target --json` 的选取说明污染 stdout；现在 JSON 模式把说明送到 stderr，stdout 保持完整 JSON。
- #71 搜索只按读取前 stat 计预算，文件增长后实际可读取 10 MB 而没有截断提示；现在通过锚定目录描述符、no-follow/nonblocking 普通文件打开和有界实际读取，落实读取/搜索预算，并给编辑原文读取复用同一限制。
- #72 预暂存的外向符号链接绕过新路径过滤；现在同时检查当前路径和 index 中实际 staged symlink blob，拒绝非法暂存但保留 index 供用户处理。
- #73 测试目标只检查原始路径，安全名称别名可指向 `.env` / `.selfforge`；现在也检查解析后相对仓库的组件。
- #73 JUnit 仅约束每个 suite，两个各 100,000 的 suite 会被当作 200,000 passed；现在累计计数同样受 100,000 上限约束，非法报告不返回部分通过证据。

本轴最严重项为实际读取预算和保护路径绕过。没有把一般代码风格偏好列为阻断。

## Spec

规格轴初审确认 8 组问题，复核另确认 1 项硬链接问题，均已修复：

- #68 除 Host 门禁外，还修复 Commit/Revert/Remove 后同一任务轮询重新恢复旧 confirmed 的问题。上下文绑定 job/session/repository/branch/sandbox；显式验证占用请求代次，失效会撤销在途请求并抑制同一上下文再次恢复。持久恢复的 badge、原因、卡片与状态均标明历史快照，缺失旧摘要显示“历史运行明细不可恢复”，不称为当前已验证。
- #69 repair 与 generate-test 两个导出路径若相同或硬链接别名，后写的 patch/test 会覆盖 JSON。现在模型/动作前拒绝冲突。
- #70 报告指向将被移除的工作区时，曾先删除工作区再报导出失败。现在 mutation 报告必须位于受影响树外；prune 报告位于管理根目录外。复核再现树外报告与源码共享硬链接会在 commit 后覆写源码，因此共享导出预检及实际 fd 写入前均拒绝多硬链接目标。
- #71 搜索预算必须按实际读取量约束，不能仅依赖 stat 估计；read_file 的同类增长绕过也已补齐。
- #72 循环符号链接 `loop -> loop` 曾抛未捕获 RuntimeError 并阻止正常修复提交；现在安全略过。预暂存危险链接仍明确拒绝。
- #73 解析后保护组件和 JUnit 累计上限缺口与规范轴交叉。

本轴最严重项为历史证据误用及导出覆写源码。独立最终复核相关 59 项及硬链接/文案复验 6 项通过；这些专项数量与完整组合不相加。

## 验证证据与范围

- 修复前先运行反例：Host/未知 sandbox 恢复与失效重载 3 项失败；CLI 自动目标 JSON / 双输出别名 5 项失败；mutation 报告树内位置 2 项失败；实际读取增长 2 项失败；预暂存/循环符链 2 项失败；Runner 保护别名与 JUnit 累计 4 项失败；树外硬链接报告 1 项失败。均在修复后通过，不使用付费模型。
- UI 专项 20 passed；CLI 导出相关最终 31 passed；Runtime 相关 45 passed；Diff/Delivery 相关 28 passed。完整组合单独记录，不将历史数量累加。
- 最终受测源码 `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`。早期两次测试在源码收尾后主动中断，不作为完整通过证据。最终完整组合：**787 passed，0 skipped**，493.90 秒；1 条既有 Starlette/httpx 弃用警告。
- 命令：`SELFFORGE_EVAL_SOURCE_ROOT=<已预检五源缓存> SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`。五源缓存通过 `prepare_eval_sources.py --profile v3 --offline`；实际 Docker 覆盖集成、22 题无模型参考正负控。基础镜像 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`；锁定依赖镜像 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。
- Ruff、Node 语法、SDD 结构/引用和空白检查通过；SDD 检查不证明测试语义。没有改写冻结评测资产、历史成绩或运行付费模型测量。
- Chromium 加载真实应用和 app.js，使用明确 API 替身验证：Host 查询数 0；Docker 仅恢复一次；历史 badge 与缺失摘要文案正确；表单提交后同一 job 轮询仍保持失效。浏览器 smoke 不冒充真实模型端到端运行，后端另由真实 Git/CLI/Docker 与 API 测试覆盖。

## 远端交付

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#68](https://github.com/liuzhouquan/selfforge-agent/pull/68) | `35e8507f835f90efd4074561e91168fa4d95d231` | [43551486918](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43551486918)：732 单元 + 15 Docker，通过 | `68aae685e1515d54d3aa03dbd45acfa7fdae33f4` |
| [#69](https://github.com/liuzhouquan/selfforge-agent/pull/69) | `c18619aca9e9d5c765ce86283b7555e2ebe856f1` | [43551648137](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43551648137)：741 单元 + 15 Docker，通过 | `9e2b42a2e8a66dd1c3f272e3eb7ee088b8e62b69` |
| [#70](https://github.com/liuzhouquan/selfforge-agent/pull/70) | `f825e3408c2502f6bb48af8d9657b2dd51050d6d` | [43551722625](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43551722625)：748 单元 + 15 Docker，通过 | `87fe852183df180df0fb87835d2875c980026d24` |
| [#71](https://github.com/liuzhouquan/selfforge-agent/pull/71) | `b59e63a344f85a48f454cd00152b4f42488216d7` | [43551890826](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43551890826)：755 单元 + 15 Docker，通过 | `60cce4d6cd2f16cb1266ad1f1c546ba69671de3b` |
| [#72](https://github.com/liuzhouquan/selfforge-agent/pull/72) | `8ff5e5709ee97f158efa3f7e29b4abeb8843ba2e` | [43552133757](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552133757)：761 单元 + 15 Docker，通过 | `1f1518a4a30e5d16c3ff369ecf05c5759ba39b87` |
| [#73](https://github.com/liuzhouquan/selfforge-agent/pull/73) | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7` | [43552476659](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)：772 单元 + 15 Docker，通过 | `af31537e72f39aeccf98fb9149edb51787d4bcaa` |

分支按批次累计集成，因此后续准确 head 包含前序修复；#73 的最终组合还包含 UI 历史文案及共享导出硬链接的收尾修复。后续状态对账只改文档。最终 main CI 的准确运行与结论回填 [PR #73 交付描述](https://github.com/liuzhouquan/selfforge-agent/pull/73)，避免为记录本次文档提交 SHA 递归提交。
