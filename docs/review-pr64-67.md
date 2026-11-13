# PR #64–#67 审查与整合

2026-11-13；基点 `60a1b386cda8f811be1edb5b7baf9e433a894c58`。按用户授权审查、修复并依次合并，保留所有原分支提交；[SDD](./SDD.md) 是当前任务状态入口。

## Standards

规范轴确认 5 项，均已修复：

- #64 prune 联动只比较 session ID，清理仓库 B 的同名会话会关闭仓库 A 的交付与回归视图；现在同时绑定当前仓库。
- #65 策略自带 model/base URL 时必走 custom 分支，忽略供应商配置及凭据；现在采用真实供应商路由、保留其身份与密钥，端点冲突在评测前拒绝。
- #65 先验证文件却把路径交给评测器重新读取，执行输入可与 policy 来源摘要脱钩；现在将已验证的 manifest、holdout 和 reference 字节冻结并传至实际 preflight，报告摘要还需一致，策略路径来源只保留文件名。
- #67 读取日志无界、不清洗字段、坏 JSON 抛异常，且不能安全处理最新损坏/未提交事件；现在使用受限、非阻塞 no-follow 和共享锁读取，严格过滤 session/base/test/workspace 指纹、状态/原因和摘要，未知最新状态撤销确认。
- #67 生产 writer 只保存两侧 status，GET 恢复丢失计数/退出码/耗时；现在写入完整安全 Report 摘要，真实生产写入能经 GET 原样恢复。

本轴最严重项是 #65 凭据和执行来源脱钩。#66 安全 writer 的重复仅属非阻断抽取判断，本轮没有为此进行额外框架重构。

## Spec

规格轴确认 6 组：供应商配置；策略预算/冲突缺口；持久日志边界；完整恢复及 GET/交付绑定；跨仓库 UI 作用域；sandbox JSON 输出。与规范轴交集去重后共 **7 类修正**。

- #65 的上下文预算此前没有下传，实际始终 8000；现在贯穿 CodingAgent 与 provenance。补齐显式 fallback 冲突和默认 image 被误当显式覆盖的问题。
- #66 `freshness/check --json` 混入人类文本，多个 `--json --output` 组合仅输出写入提示；现在 stdout 为纯 JSON，同时保留无 flag 的旧终端行为。export-lock 的文件仍是原始 requirements 文本，JSON stdout 使用 requirements 字段。
- #67 GET 改为验证真实 Git 仓库和 ASCII 1–64 字符会话 ID，底层错误脱敏；查询的是历史报告，不重新执行 Docker，也不把历史 confirmed 自动提升为当前 Runtime 验证。PR 注入还需匹配 session、初始 base、当前内容与实际推送的提交。
- 旧格式确认必须同时具有 base_status=failed、repaired_status=passed 与合法标识；不编造没有保存的运行摘要。坏历史之后的新合法完整事件可重新建立状态。

本轴最严重项为持久确认与实际来源不一致。原夹具中非法非 hex 摘要、非公开状态及空 .git 目录已改为真实生产格式/真实 Git，原数量、状态复位、API 和交付断言均保留，另增更严格反例。

## 验证证据与范围

- 修复前已实际复现：跨仓库同名 prune 错误失效；策略绕过 route_providers 且无正确密钥、上下文未下传、运行摘要与 policy 不符；sandbox 10 组 JSON 组合有 8 组失败；持久记录安全与完整恢复首批 14 个反例全部失败。
- 治理/回归 UI 组合：20 passed；策略及关联规格：21 passed、1 integration deselected；sandbox/策略 CLI：23 passed；持久/回归专项最终：42 passed、1 integration deselected。deselected 不计通过，完整组合另行覆盖。
- 完整组合：设置 `SELFFORGE_EVAL_SOURCE_ROOT` 指向通过 `prepare_eval_sources.py --profile v3 --offline` 的五源缓存，执行 `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q` → **741 passed，0 skipped**，516.18 秒；1 条既有 Starlette/httpx 弃用警告。
- 完整受测源码与提交 `913ca755b5b4474cf2d971775c6ff7cb1a6681fe` tree 一致；交付 head `c3b311ff674c6912218122967fee27f4d471deb0` 只补入 main 历史，tree 不变。后续状态对账仅文档。
- Ruff、Node 语法、SDD 和空白检查通过。SDD 检查只证明结构与本地引用，不证明测试语义。
- 真 Docker 覆盖 22 题无模型参考正负控，以及回归验证后通过 GET 完整恢复原报告。基础镜像 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`；锁定依赖镜像 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。不改历史 v2/v3 冻结资产或成绩，未执行付费模型测量。上下文传播测试只使用离线脚本模型。
- Chromium/agent-browser 实际展开治理面板并点击存储、确认清理；使用明确 API 替身，验证未知配额提示、跨仓库活动会话保留、确认框复位及 390×844 移动视口无横向溢出。它不冒充真实模型端到端运行；真实后端由 TestClient/CLI/Git/Docker 独立验证。浏览器和本任务临时预览服务已关闭。
- 回归日志：总量 4 MiB、单行 16 KiB、10000 项限制；超限、symlink、忙锁、损坏最新行或不完整尾部不能建立最新确认。旧日志缺失摘要保持缺失；新生产日志完整保存安全摘要，不记录源码、原始错误正文或工具参数。

## 远端交付

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#64](https://github.com/liuzhouquan/selfforge-agent/pull/64) | `796a4e7c0baf2113242489aedf7a2bb4ec72f549` | [43369759894](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43369759894)：682 单元 + 15 Docker，通过 | `c7229c9dec1a923a01fb50d777ed53036c2a4c65` |
| [#65](https://github.com/liuzhouquan/selfforge-agent/pull/65) | `4f4ccf6d0f8f795bd30a595d65ba28776655ab68` | [43370539362](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43370539362)：691 单元 + 15 Docker，通过 | `6a703e7ca0695ac5505246d0aad137d6afcc361f` |
| [#66](https://github.com/liuzhouquan/selfforge-agent/pull/66) | `b3ce49b1784563da12ccad873c8d245ff6ad0972` | [43371114590](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43371114590)：705 单元 + 15 Docker，通过 | `839840acef5681f7b7ca6fb727d483b20a566998` |
| [#67](https://github.com/liuzhouquan/selfforge-agent/pull/67) | `c3b311ff674c6912218122967fee27f4d471deb0` | [43372329570](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)：726 单元 + 15 Docker，通过 | `59a03b8a91aaa18f1648ee9b761ab894fb845a09` |

不同版本的数量不相加。最终 main CI 的准确运行与结论回填 [PR #67 交付描述](https://github.com/liuzhouquan/selfforge-agent/pull/67)，避免为记录本次文档提交 SHA 递归提交。
