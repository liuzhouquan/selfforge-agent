# 可接手任务

本清单描述基线 `955d8cd66f2119c3bb47ff1ba85f8b81f22da57e` 之后的开发入口，不代替实时 Git/PR 状态。产品完成条件仍以[产品边界](./product-scope.md)为准。领取前检查是否已有 Owner/分支/PR；按[任务模板](./task-template.md)登记实际选择和范围。

下文源码路径相对 `src/selfforge_agent/`；benchmark 路径相对仓库根目录。

状态含义：**Ready** 可按给定范围开始；**Investigate** 先验证风险，不能先宣布缺陷；**Waiting** 依赖尚未满足；**Needs Design** 先明确一项可交付行为。一次只取一个工作单元。已复现的重要可靠性问题优先于增加评测题数量。

| ID | 状态 | 工作单元 | 前提 |
| --- | --- | --- | --- |
| SF-EVAL-01 | Complete | 15 个单题分支与原有 5 题形成 20 题 / 5 仓库池 | 审查修订 v2 通过真实 Docker 参考正负控；不是模型成绩 |
| SF-RUN-01 | Complete | CLI Coding/交付/删除与 Web 任务统一排他锁 | 同一用户/XDG；自定义存储目录共享锁；升级先停止旧 worker |
| SF-WS-02 | Complete | 工作区孤立 Worktree 状态审计与安全批量清理 | PR #47；Web/CLI共享锁，持锁重查dirty/ignored/merged；保留分支和用户输入 |
| SF-EVAL-02 | Complete | 冻结 v2 清单、参考/holdout 哈希及模型/预算/失败策略 | [v2 策略](../benchmark/validated-candidates/twenty-tasks-v2.md)；旧 v1 保留历史 |
| SF-EVAL-03 | Complete | 按 v2 策略完成每题 3 次真实模型运行并报告 | 20 题 × 3 次 = 60 次已完成，53/60（88.33%）；环境来源记录有限，见 [评测记录](./evaluation.md) |
| SF-ENV-01 | Complete | 只读离线准备计划及有界哈希 wheelhouse 审计 | CLI/API；不自动下载、不保证安装成功；见[审查记录](./incoming-branches-review.md) |
| SF-RUN-02 | Complete | 倒数第二轮与末轮预算提示纠正 | PR #38；仅改进提示并保留既有执行保护，未验证模型成功率提升 |
| SF-RUN-03 | Complete | 中断、超时与异常下保留已完成模型轮次数 | PR #39；当前模型调用未返回不计入，工具阶段异常保留当前轮 |
| SF-RUN-04 | Complete | 只读工具死循环检测与瞬态预算引导 | PR #45；只读循环仅建议，清除旧任务/旧轮诊断，不宣称成功率提升 |
| SF-ENV-02 | Complete | 有限 Poetry/uv 锁文件哈希识别与离线导出 | PR #40；条件标记和版本分叉拒绝，VCS/本地源排除范围见依赖文档 |
| SF-ENV-03 | Complete | 有限 Pipfile.lock 哈希识别与离线导出 | PR #41；default/develop 严格校验，条件标记拒绝，不替代 Pipenv 解析器 |
| SF-EVAL-04 | Complete | 在评测输出自动记录应用 SHA、Python/Docker 版本和生效预算/供应商配置 | PR #42；开始时记录来源并脱敏；旧53/60历史报告不补写 |
| SF-ENV-04 | Complete | 有界 pip-compile / flat 锁导出和规范化计划 | PR #43；仅规范化后才能构建的输入会返回显式 export_command |
| SF-ENV-05 | Complete | 共享有界 wheelhouse 审计与哈希就绪诊断 | PR #44；失败/超时不报告 ready，不证明可安装性 |
| SF-EVAL-05 | Complete | 实际报告schema比较和回归门禁 | PR #46；计数精确比较，缺失/无效输入不能通过门禁 |
| SF-EVAL-06 | In Progress | 增加1道真实仓库多文件修改任务的参考正负控 | 详见 [SF-EVAL-06](./tasks/SF-EVAL-06.md)；独立新manifest，跨3个源码文件协同修改，不改历史成绩；先完成无模型资格验证 |

## SF-EVAL-01：增加一个有参考控制的任务

目标：为一个新的行为机制提供固定仓库 SHA、可解的契约、负例基线与参考正例。一个 PR 默认交付 1 题；不能把“补齐剩下 15 题”当成一次实现任务。

- 入口：`benchmark/validated-candidates/`、`runtime/benchmark.py`；先读[评测契约](./benchmark.md)及相关工程约束。
- 交付：契约/上游依据与许可证说明、允许修改路径、独立 holdout、最小参考补丁、固定 manifest、实际 `eval-validate` 结果及兼容边界。
- 保留历史：已有 manifest、契约、holdout 与结果保持冻结；新题使用新 manifest 并记录哈希，不通过修改旧输入后继续引用旧成绩。
- 验收：原始 SHA 的 holdout 因断言失败而失败，不能是收集/环境错误；参考修复在独立可见和隐藏副本均有非空通过；测试能区分合理修复与常见错误修复。
- 范围外：本轮不改变 Runtime、不批量重写测试、不运行整套正式模型验收、不宣称上游确认了项目自定义功能需求。
- 完成后：更新独立任务清单和证据；实时模型探索若是另一项独立目标则另立任务，按实际授权和预算执行。

当前参考清单为 [v2 20 题 / 5 仓库](../benchmark/validated-candidates/twenty-tasks-v2.md)，20 题均通过真实 Docker 参考预检。原 `suite-five-tasks.json`（SHA-256 `5fbdc62e2e5be199e72a79c9b3ae23dcc325847cdb4e07bbddf20be4bfc20942`）作为 5 题历史清单保留。覆盖异步空输入、Unicode 分隔、目录剪枝、零分块和替换回调停止；不要将候选/早期 pilot 中相同机制重复计数。历史模型分数使用不同策略，不能套用到新 Runtime 或相加，见[评测历史](./evaluation.md)。

## SF-RUN-01：调查工作区并发协调

已完成同一用户/XDG 内的 CLI/Web 统一锁（含不同记录/工作区存储目录）；独立进程覆盖冲突拒绝与进程退出释放。外部 Git/编辑器和不同 XDG 仍不参与，内容指纹不是锁。以下为原调查验收，实际证据见[审查记录](./incoming-branches-review.md)。

- 入口：`api/coding_jobs.py` 的 workspace guard、CLI Coding 与 workspaces 子命令、`runtime/workspace.py`、`runtime/delivery.py`。
- 首个验收：用受控临时仓库和两个进程，证明实际冲突或证明现有保护生效；记录交错顺序、期望和观察结果。
- 若确认需修复：另明确一项“第二个写入者被拒绝/安全等待”的行为；复用现有锁机制，覆盖进程退出/取消后的锁释放以及 CLI/Web 的组合。
- 范围外：新调度平台、多 Agent 产品能力、全局分布式锁、强制清理用户工作区。

## SF-EVAL-02 / 03：冻结与正式运行

题目补齐之后再冻结清单字节/哈希、模型与供应商回退策略、每题重复次数、步数/时间、测试选择、镜像 ID 和失败分类。参考预检必须在模型构造前实际通过，隐藏信息不能进入 Agent 输入。

运行目标至少为 3 个真实仓库、20 个独立任务、每题 3 次。报告全部成功与失败、耗时及供应商 Token；未配置有效费率时不猜费用。离线演示不能混入模型成功率，旧小样本不能替代新策略的正式结果。成功率门槛需基于首轮真实基线确定，不能先编造一个已达标数字。

## SF-ENV-01：先定义一种环境扩展

现有能力包括只读布局/依赖/锁文件识别、有限 uv.lock 导出、CLI/API 离线计划/有界 wheelhouse 审计与显式离线镜像构建；不等同于自动安装完成。选择一种实际项目配置，先定义可接受的输入、产生的计划/镜像和失败结果，再依据任务卡实现。保留固定哈希、无网络构建、输入边界及会话镜像绑定；不默认运行仓库 hooks、源码构建或系统包安装。

## 更新规则

认领/交接在同一任务记录里更新 Owner、基线、范围、PR 和下一步；合并后将任务从当前队列移到有 SHA/证据的历史记录。新增独立目标先成为单独条目，不在进行中的任务里隐式扩展。用户明确调整优先级或范围时，以用户最新指令为准。
