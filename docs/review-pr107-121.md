# PR #107–#121 审查、修复与合并记录

日期：2026-11-19。固定基线 `a046afacd0eded27d64a3b3185d1a2b6c21b0fa9`；最终受测源码 `e47c868effad55d8b8310ceb0b3c3f691e94e412`。使用独立 `review/pr107-121` worktree，保留十五个 PR 原始提交；修复分别回填原分支，并由最后的 #121 汇集完整组合，按准确 head 的成功 CI 依次合并。统一需求源为 [SDD](./SDD.md) 本批十五项任务。

## Standards

最终独立复核未发现剩余可复现阻断，无新增主观 smell 建议。标准源：[AGENTS.md](../AGENTS.md)、[CONTRIBUTING.md](../CONTRIBUTING.md)、[工程不变量](./engineering-invariants.md)、[验证门槛](./development.md#verification-gates)。

先前源码的独立边界复验8项通过；最终增量的两项目录隔离反例独立2项通过，0.15秒。前一源码的结论经增量审查更新到最终受测SHA；计数不与完整集合相加。

## Spec

最终独立复核已报缺口全部闭合，未见剩余确定阻断。旧组合 `c806ed4a00d293dd8d2c9a03f35f3b5c26b50ed9` 的准确十五专项曾有249项通过，但未覆盖随后发现的目录隔离反例，因此不冒充最终源码验收。最终源码artifact专项独立22项通过；完整结果另列。

本轮建立 **40个确定失败用例**，另有正向兼容对照；同一反例重复运行不重复计数。没有删除测试或放宽为任选结果。

| PR | 审查发现、修复与证明边界 |
| --- | --- |
| #107 | 费率卡、缓存深度、未知费用与币种边界核读后未见确定阻断，保留原实现；专项与完整组合复验。 |
| #108 | 5个失败：合法feature+fix分支、.github/_tools仓库URL被拒；CommitResult/RevertResult改写真实带空格文件名。保留原始文件名，使用符合Git约束的分支门禁及合法GitHub仓库字符。相关交付回归62 passed；实际结果模型、本地Git及gh边界替身，无产品外部PR发布。 |
| #109 | 5个失败、1个正控：审查路径去空格/归一化可改写added身份，合法JSON描述中的Markdown围栏破坏解析，巨大timeout溢出。保留字面路径、先拒原始父目录段，仅解包顶层围栏，先验数值范围。实际Baseline/ProviderReviewer加HTTP替身；相关37 passed。 |
| #110 | 锁文件递归深度、PEP508条件拒绝、规范版本及哈希边界核读后未见确定阻断，保留原实现。 |
| #111 | 4个失败：Git特殊路径被当作pathspec选中受保护内容；检查后文件变成FIFO仍读取；锁文件替换后修改外部目标权限；总预算未包含截断标记。使用--literal-pathspecs、共享FD普通文件读取、锚定nofollow/nonblock锁和fchmod(fd)；标记计入总预算，超出文件计omitted。真实Git/文件及受控替换；相关32 passed。 |
| #112 | 1个失败：父进程退出后立即SIGKILL整组，子进程未获得承诺的150ms清理宽限。宽限按组计时；仍保留硬清理。真实fork/信号/清理文件证明，相关35 passed。 |
| #113 | 2个失败：默认snapshot原样导出私有reasoning；原子重命名失败遗留临时私有文件。默认脱敏reasoning而私有session保持原内容；目录FD锚定原子落盘并在失败时清理。真实文件与rename故障注入，相关48 passed。 |
| #114 | 1个失败：巨大整数backoff触发OverflowError。重试退避先验0..300秒，再finite检查，保持共享deadline。HTTP边界替身与相关回归26 passed，无真实模型请求。 |
| #115 | 1个失败：真实read_file返回6019字符，超过6000上限。为截断提示预留空间，保留提示且总长不越界；相关24 passed。 |
| #116 | 2个失败：NFC/NFD改名或0744→0645执行权限变化仍给相同指纹。哈希保留原始路径字节及完整mode，NFC仅用于稳定排序。两项新PR测试原要求权限变更等价，与既有证据身份不变量冲突，现明确要求不同；未放宽旧证据门禁。真实rename/chmod，相关33 passed。 |
| #117 | 外部堆栈隔离、通用模块降级与causally_linked=False约束核读后未见确定阻断，保留原实现；位置关联不是因果证明。 |
| #118 | 3个失败：原始父目录段被规范化后接受、合法前导空格路径被改写、清洗后的风险目标反被下游拒绝。路径先拒越界且保留身份，复用同一证据清洗规则；保留等价./前缀兼容。相关27 passed，不将启发式风险当已验证缺陷。 |
| #119 | 11个失败：公共模型接受bool版本/次数和负摘要、prepare/read/write/list/cleanup穿透祖先符链、OSC原因被接受；修复中另发现raw父目录段绕过双向隔离，已补两项无写入反例并在mkdir前拒绝。模型与磁盘共享严格摘要校验，目录FD锚定，拒终端控制序列。最终artifact专项22 passed；未声称旧磁盘严格reader可接受坏摘要。 |
| #120 | 3个失败：Sha256等混合大小写被解析接受，却以原文传给profile/build消费者。两条消费入口都输出规范sha256摘要；真实锁文件/wheelhouse读取与构建输入验证，相关63 passed。Docker执行证据来自完整集合，不把字节fixture称为真实镜像构建。 |
| #121 | 2个失败：8层嵌套100k文本仅估算前缀，500预算误判无需压缩；摘要保留BEL/OSC。采用有node/累计字符上限的迭代遍历，完整估算文本，超限明确拒绝；生成摘要清洗终端控制而不改用户锚点。相关上下文回归41 passed；估算仍是启发式。 |

两项规格冲突按既有工程不变量纠正：#116不再将实际不同的权限/路径当作相同树，保留同一真实树的确定性；#121不以深度截断前缀冒充完整计量。#111旧测试的总预算耗尽标记改验当前截断标记，另新增100字节总预算反例，严格验证连标记在内的上限。相关历史E1–E4来源保留，当前验收指向E5–E8。

两轴分别为Standards 0剩余阻断、0新主观建议；Spec 0剩余确定阻断。测试数量不是完成度评分，不相加历史分支计数。

## 完整验证与环境

完整组合 **1908 passed，0 skipped**，739.57 秒；1 条既有 Starlette/httpx 弃用警告。[最终准确组合CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491657686) 单元与Docker均成功。Ruff通过；SDD结构及本地引用64 tasks / 255 acceptance items通过；空白检查通过。

```sh
uv sync --locked --dev --default-index https://pypi.org/simple
uv run --no-sync ruff check .
uv run --no-sync python scripts/check_sdd.py
git diff --check
uv run --no-sync python scripts/prepare_eval_sources.py --profile v3 --offline --root "$SELFFORGE_EVAL_SOURCE_ROOT"
SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q
```

五源jmespath、boltons、pocketflow、more-itertools、itsdangerous均通过v3 offline预检。Docker基础镜像 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`；锁定依赖镜像 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。首次组合单元运行仅作诊断；旧组合完整运行因发现目录隔离问题主动中断，不计通过。最终完整结果绑定上述准确SHA。

文件/Git/进程与Docker边界按实际环境运行，HTTP、模型和产品gh动作使用本地替身/fixture/受控故障注入，不证明真实外部服务。没有付费模型调用、新增live-model成绩或UI修改。

合并后核对main与受测组合全树一致，再仅修改SDD/本记录；文档链接、引用与空白检查通过。最终main CI结论回填十五个PR描述，避免递归文档提交。

## 交付

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#107](https://github.com/liuzhouquan/selfforge-agent/pull/107) | `1ac7ffaecf2c640621f95ba8775697c600b59a17` | [44134432052](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44134432052)：单元及Docker成功 | `27ada87dbee8611949f2cc60073e7279d69c6dcf` |
| [#108](https://github.com/liuzhouquan/selfforge-agent/pull/108) | `a5bac3277acaf6448b38b33654e43ea626c22ce8` | [44491407795](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491407795)：单元及Docker成功 | `cc2063a9bb5ed326980e08b9d659f003513109c8` |
| [#109](https://github.com/liuzhouquan/selfforge-agent/pull/109) | `ce4f5f396ff9242b50050b9ed55661ece3db760c` | [44491416132](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491416132)：单元及Docker成功 | `94ae82afd013308f8727bd658eaac3a44c921cff` |
| [#110](https://github.com/liuzhouquan/selfforge-agent/pull/110) | `3c0f7cdf21b9799a5a97ee752b9dc44ed9077c4d` | [44491422115](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491422115)：单元及Docker成功 | `cb8cf85633bc2ee180bdd38bc3ba2986c082cd25` |
| [#111](https://github.com/liuzhouquan/selfforge-agent/pull/111) | `2c55608cd67ae47b026bb8bf00736b792cbb13ee` | [44491431426](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491431426)：单元及Docker成功 | `328a828eb30ed4ea40c018320fb12ae86913703b` |
| [#112](https://github.com/liuzhouquan/selfforge-agent/pull/112) | `a966d48c5cb5225b8cf318bd14fff4f4bb2ea155` | [44491444970](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491444970)：单元及Docker成功 | `f75bdf2442f10fb9cbcf76e43d1a31c18e433071` |
| [#113](https://github.com/liuzhouquan/selfforge-agent/pull/113) | `be418b1d5da386c5303ecd3bd7b0e2d3984000bc` | [44491451376](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491451376)：单元及Docker成功 | `6a3eacb0513ed9d2741e107746ec5977c923f471` |
| [#114](https://github.com/liuzhouquan/selfforge-agent/pull/114) | `96e67068d027eb79aa2261397ef711e2ea9ccd25` | [44491460897](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491460897)：单元及Docker成功 | `fb14adc51b8008c50c2b37a8f6bf01b4475fa456` |
| [#115](https://github.com/liuzhouquan/selfforge-agent/pull/115) | `c09660542bf365583abc0ccbe7ac3d5b81a5e293` | [44491468644](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491468644)：单元及Docker成功 | `af29de1c5daf42c6f1e44a591b8733fd2f3e65cf` |
| [#116](https://github.com/liuzhouquan/selfforge-agent/pull/116) | `c135c58e1e5217e8134a9e723ffe23bfc5c5fa53` | [44491475554](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491475554)：单元及Docker成功 | `d5e84bfd3f3e3da15aaa71c87f07d2ee52b955e6` |
| [#117](https://github.com/liuzhouquan/selfforge-agent/pull/117) | `d19c2cb1ddfa3ea0c163f825ccc91a0359e2a6ff` | [44491483988](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491483988)：单元及Docker成功 | `d190c3a4e4aecd63e2823e43002a0c23130bf73b` |
| [#118](https://github.com/liuzhouquan/selfforge-agent/pull/118) | `3b2f4c676baed63f75f461d67a2bf808ad6332d8` | [44491493401](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491493401)：单元及Docker成功 | `7450cfc57c357ef7a207b5556cb79f16bf174ff6` |
| [#119](https://github.com/liuzhouquan/selfforge-agent/pull/119) | `a3343e7548e00c9fd01ecb0c60c268191e70d3a6` | [44491503938](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491503938)：单元及Docker成功 | `0256454589070534f640993864458eb2ca90f681` |
| [#120](https://github.com/liuzhouquan/selfforge-agent/pull/120) | `b45c232056ac226653c7bb3e64fb08b1dd66d682` | [44491509706](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491509706)：单元及Docker成功 | `a088bc3f56d8959d2f2a04cfe4f9b9546b6306d0` |
| [#121](https://github.com/liuzhouquan/selfforge-agent/pull/121) | `bb437f642603e92d293bf05cde7a0495bc452746` | [44491657686](https://github.com/liuzhouquan/selfforge-agent/actions/runs/44491657686)：单元及Docker成功 | `a6cdcd86e257ad4b1ca303643f00415004a64465` |
