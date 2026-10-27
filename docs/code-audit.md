# selfForge Agent 代码质量审计（第一阶段）

审计基线：`67cd21b4758665e8e6a86b7757bd3296fedd5006`（`main`）。本轮只分析与验证；除本报告外没有修改实现、测试或配置。下列问题描述的是该审计基线，后续修复应以各自 PR 的验证结果为准。问题等级：P0 为严重正确性或安全问题，P1 为重要可靠性问题，P2 为维护性与设计问题。“已复现”指本轮用临时目录或模拟请求触发；“代码证据”指执行路径明确，但尚未做相应故障注入或真实环境验证。

## 范围与实际执行路径

- 阅读了 `README.md`、`README.en.md`、`AGENTS.md`、`docs/architecture.md`、`docs/product-scope.md`、`docs/benchmark.md`、CI 配置及相关测试；检查了 CLI、Web API、Coding Agent、工具与模型适配、Session/Context、工作区与任务管理、Docker/Host 执行器、pytest Runner、PR Review/Repair、差异测试生成和重复评测的核心路径。
- 通用任务：CLI `code` 或 Web `/api/code/jobs` → 配置供应商与独立 worktree → `CodingAgent` 多轮 Tool Calling → `ToolRegistry` 读/查/改/运行 → Docker 默认执行命令及 pytest，Host 需显式信任 → JSONL 会话/轨迹与 Web 任务记录 → Diff/恢复。`completed` 目前依据 Agent 最后一次 `edit_file` 之后的 `run_tests` 成功状态。
- 专项工作流：PR Review 读取 GitHub diff 并运行规则/可选模型审查；Repair 在 Base、Head、Repaired 临时 worktree 运行测试；差异测试生成要求 Base 通过、Head 出现断言失败；`eval-suite` 从固定 SHA 建立干净副本，独立运行可见测试和隐藏测试。
- 权限与资源边界：文件工具校验工作区路径，Docker 默认禁网、非 root、只读根文件系统及 CPU/内存/PID/tmpfs 限额；Host 执行需要显式信任。现有测试覆盖了正常工具循环、部分失败分支、跨进程工作区锁、Docker 清理、PR 修复证据和评测防作弊。没有发现需要新增框架或大规模重写才能解决的问题；Repair 的独立 proposer loop 是文档声明的阶段性边界，不单独列为缺陷。

## 验证结果

| 命令/检查 | 本轮结果 | 说明 |
| --- | --- | --- |
| `uv run --no-sync ruff check .` | 通过：`All checks passed!` | 使用本地已安装的锁定依赖；未重新执行依赖同步。 |
| `uv run --no-sync pytest -q -m 'not integration'` | **95 passed，5 deselected**；1 个 `StarletteDeprecationWarning` | 与 CI 单元测试选择相同。 |
| `uv run --no-sync pytest -q -m integration` | **5 passed，95 deselected**；同一弃用警告 | 本机 Docker daemon 与 `selfforge-agent-sandbox:latest` 镜像可用；这 5 项在缺少 daemon/镜像时会跳过。 |
| `uv run --no-sync pytest -q` | **100 passed**；同一弃用警告 | 全量结果与分组一致。 |
| 临时目录/模拟请求的定向复现 | 下文 P1-01～P1-04 | 未改项目源码，也未调用真实模型服务。 |

上述通过结果只证明当前环境和现有断言通过，不等于所有失败路径或真实模型任务已覆盖。本轮没有运行真实 DeepSeek/OpenRouter、GitHub PR 网络操作、20 任务真实模型评测、远端 CI 或新镜像构建；这些仍是待验证范围。弃用警告来自当前 FastAPI/TestClient 依赖组合，非测试失败。

## 问题清单

### P0

本轮没有发现并证实达到 P0 的问题。下列 Web 来源校验风险需要在真实浏览器与部署配置中进一步确认；在确认利用路径前不把它标成 P0。

### P1：重要可靠性问题

#### P1-01：Host 命令可在成功测试后改代码，但任务仍标记 `completed`（已复现）

- **位置与证据**：`src/selfforge_agent/runtime/agent.py:146-151` 仅在成功 `edit_file` 后清除验证状态；`bash` 可能修改文件，但不会清除。`src/selfforge_agent/runtime/tools.py:245-267` 允许 HostExecutor 运行任意已信任命令，`run_tests` 只依据当次退出码返回成功。`src/selfforge_agent/runtime/agent.py:121-126` 随后使用旧验证状态决定 `completed`。
- **复现**：在临时目录放入返回 `1` 的 `app.py` 和相应通过的 pytest；模拟模型依次调用 `run_tests`、Host 模式 `bash` 将函数改为返回 `0`、给出最终答复。本轮输出为 `agent_status= completed`，最终源码确已返回 `0`。默认 Docker 命令修改不会回写，此问题影响显式信任的 Host 路径及其他可写执行器。
- **影响**：任务状态及 Web 展示的“已验证”可能对应旧源码；直接工作区模式也可能把未测试改动留在原仓库。
- **最小修复**：在可能写入工作区的命令后撤销验证状态，或记录成功测试时的工作区内容指纹，并在完成前比较；增加“测试通过 → Host 命令改源码 → 最终答复”的回归测试。

#### P1-02：会话 JSONL 的不完整尾行使恢复整体失败（已复现）

- **位置与证据**：`src/selfforge_agent/runtime/session.py:43-69` 的 `verification_passed()` 与 `load()` 对 `splitlines()` 每一行直接 `json.loads`；同文件 `:71-81` 的 `trace_events()` 已显式忽略尚未写完的尾行。`src/selfforge_agent/runtime/agent.py:73-89` 在恢复时调用 `load()`。
- **复现**：临时 `SessionStore` 先保存一条合法消息，再向会话文件追加未结束的 `{"type":"message"`；本轮 `load()` 与 `verification_passed()` 均抛 `JSONDecodeError`。
- **影响**：进程退出、写入中断或并发读取遇到尾行时，原本完整的历史也无法恢复；Web 可能仍显示 `can_resume=true`（`api/coding_jobs.py:263` 只检查文件存在）。
- **最小修复**：按完整换行记录读取；仅允许忽略不完整的最后一行，完整但损坏的历史行仍应明确报错；在异常恢复后保留有效前缀及其最后验证状态，并加故障注入测试。

#### P1-03：上下文压缩会静默丢失中间用户约束（已复现）

- **位置与证据**：`src/selfforge_agent/runtime/context.py:21-44` 只把首条和末条用户消息当锚点，摘要循环没有处理其他 `role=user` 的分支；`src/selfforge_agent/runtime/agent.py:99-101` 会持久化该压缩结果。
- **复现**：输入“Fix parser”→“Do not change the public API”→大量历史→“Please add a test”，以 500 token 预算调用 `ContextManager.compact()`；本轮得到 `compacted=True`，压缩后的消息中没有“Do not change the public API”。
- **影响**：多轮续做时可能违反之前明确的用户限制；一旦压缩写入会话，这项约束也不会在下次恢复时重新出现。
- **最小修复**：把所有仍有效的用户约束保留为有界摘要或独立锚点，预算不足时明确失败而不是静默丢弃；加入中间约束及多次压缩/恢复测试。

#### P1-04：本地 Web API 只检查连接端地址，不校验 Host/Origin（代码证据；实际浏览器利用待确认）

- **位置与证据**：`src/selfforge_agent/api/app.py:194-196` 的 `_require_local()` 只检查 `client.client.host`；代码任务、供应商配置等敏感路由调用它（如 `:222-265`、`:336-343`）。PR checkout/repair 另有同样的手写检查（`:396-409`），未见 Host 或 Origin 校验。
- **复现/边界**：使用进程内 `TestClient` 从允许的客户端地址发送 `Host: untrusted.example` 至 `/api/code/jobs/no-such-job`，本轮返回业务层 **404** 而非来源校验 **403**，证明 Host 未参与检查。DNS rebinding/跨站请求在真实浏览器和实际监听地址上的可利用性**待确认**，本轮没有声称完成攻击链。
- **影响**：若浏览器能以攻击者控制的域名访问本地服务，可能触达创建任务、配置密钥等本地权限接口；具体影响取决于监听方式及浏览器防护。
- **最小修复**：对 Web 工作台启用可信 Host 列表，并对有副作用的接口校验 Origin/CSRF；将 PR 路由统一使用一处来源校验；用真实浏览器/本地服务补跨域与 DNS rebinding 回归测试。

#### P1-05：Docker 工作区打包没有受时间和大小预算约束（代码证据；大仓库影响待确认）

- **位置与证据**：`src/selfforge_agent/runtime/execution.py:83-106` 虽先计算 deadline，但 `shutil.copytree()` 和 `tarfile.add()` 完成前未检查剩余时间/总字节数；有界的 `_run(..., remaining())` 到 `:117-118` 才开始。`src/selfforge_agent/verification/runner.py:143-145` 的测试运行也走该路径。
- **复现方法**：在一次性工作区加入大量非排除文件，以短 `timeout` 调用 `DockerExecutor.run()`，记录实际耗时及临时磁盘使用；本轮未制造大文件，故耗时/耗盘程度**待确认**。
- **影响**：较大仓库或未跟踪产物可能在启动容器前耗尽主机磁盘或显著超时；Web 任务的取消检查也无法介入此同步阶段。
- **最小修复**：在暂存前做文件数/总字节限额和显式错误，按 deadline 检查或采用可中断的有界打包；对超限及中途取消加测试。

#### P1-06：供应商回退对每个供应商重复使用完整单次超时（已复现）

- **位置与证据**：`src/selfforge_agent/runtime/model.py:42-58` 对每个候选供应商都传入同一个 `timeout`；`src/selfforge_agent/runtime/agent.py:96-105` 把任务剩余时间作为这一次调用的预算，却只能在调用返回后再次检查。
- **复现**：模拟两个供应商各耗 `0.8 × timeout` 后失败；本轮传入 `0.2s`，两次请求各收到 `0.2s`，总耗时约 **0.320s**。这是模拟传输层复现；真实 HTTP 时延分布待测。
- **影响**：两个或更多供应商故障时，单轮模型调用可超过 Agent 的剩余时间，取消/总预算响应延后。
- **最小修复**：适配器内部使用单个 monotonic deadline，每次回退只传剩余时间；耗尽时停止并返回明确超时状态；测试多供应商慢失败与取消场景。

### P2：维护性与设计问题

#### P2-01：Web Diff 的“有界”限制发生在完整 Git 输出进入内存之后（代码证据）

- **位置与证据**：`src/selfforge_agent/runtime/workspace_diff.py:45-50` 先调用 `git diff` 得到完整 patch，再截成 24,000 字符；`src/selfforge_agent/coding/worktree.py:15-26` 用 `subprocess.run(capture_output=True, text=True)` 收集完整 stdout。
- **复现方法**：在临时 worktree 制造非常大的文本改动，再请求 Diff，观察峰值内存；本轮只确认执行路径，资源量化**待确认**。
- **影响**：Web 返回体虽有限，单个大 diff 仍可造成短时内存压力或较长阻塞。
- **最小修复**：对 Git 子进程输出做流式读取/字节上限，并保留明确的截断标记；加入超过限制的回归测试。

#### P2-02：并发保存不同供应商配置可能丢失其中一次更新（代码证据）

- **位置与证据**：`src/selfforge_agent/providers.py:104-130` 每次先读取整份配置、只改一项，再以临时文件原子替换整份配置；读-改-写之间没有锁。Web 同步接口 `src/selfforge_agent/api/app.py:336-343` 可由多个请求调用。
- **复现方法**：让两个线程/进程在读完同一个初始配置后分别保存 DeepSeek 与 OpenRouter，检查最终文件；本轮未做并发调度复现，实际概率**待确认**。
- **影响**：并发配置时其中一个模型设置或 API Key 可能被旧快照覆盖；单请求的原子替换只能防半文件，不能防丢更新。
- **最小修复**：对整个读-改-写过程使用跨进程文件锁，并保持现有临时文件与权限设置；加并发保存测试。

#### P2-03：一个损坏的 worktree 元数据会让整个工作区列表失败（代码证据）

- **位置与证据**：`src/selfforge_agent/runtime/workspace.py:124-127` 直接对所有 `*.json` 调用 `get()`；`get()` 在元数据损坏时抛 `WorkspaceError`（`:101-115`），列表没有逐项隔离。
- **复现方法**：在临时仓库建立两个工作区，损坏其中一个元数据后调用 `CodingWorkspaceManager.list()`；本轮未做故障注入，结果**待确认**。
- **影响**：单个坏记录会遮住其他正常工作区，使 CLI 列表不可用。
- **最小修复**：逐项收集有效工作区，同时返回/记录坏记录供人工处理；加混合健康/损坏记录测试。

#### P2-04：架构文档对评测成功门槛的描述不一致（已确认文档差异）

- **位置与证据**：`docs/architecture.md:21` 说明可见测试和隐藏测试都必须独立通过；`:35` 的成功条件仍只写 Agent `completed`、允许的文件变化与隐藏测试通过。实际代码 `src/selfforge_agent/runtime/benchmark.py:186-204`、`:324-340` 对可见测试另设门槛。
- **影响**：读者可能低估评测条件，尤其误以为只用无关测试跑通 Agent 即可。代码与 `AGENTS.md` 一致，属于文档陈旧，不是评测实现回退。
- **最小修复**：在单独的小型文档修复中同步该段文字及相关示例。

## 缺少的关键测试与未验证风险

| 领域 | 现有覆盖 | 建议补的失败/边界路径 |
| --- | --- | --- |
| 完成状态 | `tests/test_runtime.py` 覆盖 `edit_file` 后重测、失败测试撤销旧成功 | Host `bash` 在成功测试后修改文件；外部改动与最后验证指纹；新建文件/修改测试目录后的状态。 |
| 会话恢复 | 正常 resume、跨进程任务恢复、trace 尾行测试 | 会话文件尾行截断、完整坏行、压缩事件中断、恢复时保留最后测试状态、Web `can_resume` 准确性。 |
| Context | 一次压缩保留原始任务的测试 | 中间用户约束、多次压缩后再次恢复、接近预算时的明确错误与约束保留。 |
| Web 权限 | Host 双重信任门槛、工作区锁、PR 路由基本行为 | 非可信 Host/Origin、真实浏览器跨站请求、DNS rebinding 与不同监听地址；以上攻击链仍待确认。 |
| Docker/资源 | 网络/资源参数、密钥排除、超时清理、5 个 live Docker 集成测试 | 暂存阶段超时/超大文件、磁盘满、容器启动失败及清理失败、打包期间取消。 |
| 模型与配置 | 供应商失败回退、HTTP 慢响应、配置保存 | 多供应商共用总 deadline、并发配置丢更新、实际模型 API 的错误码和 token 用量。 |
| Diff/工作区 | 一般 Diff、截断输出、正常工作区清理 | 大 Git patch 的内存上限、损坏单条元数据时列出其他工作区、并发删除文件。 |
| 专项工作流/评测 | Base/Head/Repaired 和差异测试的正常及若干错误路径；固定 SHA、独立可见/隐藏测试 | 真实 GitHub 网络故障、更多依赖复杂的真实仓库、20 任务 × 3 次真实模型运行；现有 4 题试点不能代表正式成功率。 |

## 分阶段小型修复 PR 规划

本轮不实施修复或创建 PR。每个后续 PR 先为对应缺陷加入能失败的回归用例，再做最小修改；运行相关测试、`uv run --no-sync pytest -q` 与 `uv run --no-sync ruff check .`，涉及 Docker/Web 的 PR 再跑相应集成或浏览器验证，保留 Base/Head/Repaired 与评测门槛。

1. **P0 条件项 / P1-04**：先验证真实浏览器利用路径；若证实可绕过本地权限直接执行高风险操作，提升为 P0 并优先单独修复。即使未证实攻击链，也为已确认的 Host/Origin 校验缺口单独提交 P1 修复与边界测试。
2. **P1-01**：统一“最后一次可能写入”与测试证据的工作区指纹/状态失效规则。回归 Host 路径及 Docker 不回写路径。
3. **P1-02、P1-03**：分别处理 JSONL 恢复和用户约束压缩；每项独立 PR，验证续跑和重新压缩。
4. **P1-05、P1-06**：分别给 Docker 暂存和多供应商回退设总预算；测试超限、慢失败及清理。
5. **P2-01～P2-04**：按资源影响和维护收益拆成小 PR；每次保留已有断言，不以重构或代码风格替代行为修复。

尚未验证的结论在条目中逐一标明。本报告不把没有复现的竞态、真实网络攻击或性能量化写成已证实故障。
