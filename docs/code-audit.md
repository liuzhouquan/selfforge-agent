# selfForge Agent 代码质量审计

当前复核见本节与[组合验证记录](./runtime-integration.md)。下方各阶段保留当时的证据、测试数和分支状态，不代表它们仍是当前缺陷。

## 本轮评测复核：2026-10-30

### P1-09：Host 可见测试改写隐藏验证副本，导致错误成功或错误失败（已复现，本地修复）

- **位置/证据**：`runtime/benchmark.py` 的 `_run_once` 原先在同一 `verification` 副本先运行可见测试，再安装并运行 holdout。Host Runner 的文件写入会保留；可见测试若改写 `app.py`，隐藏测试就没有验证 Agent 结束时的源码。
- **复现**：`uv run --no-sync pytest -q tests/test_benchmark_isolation.py`；两个参数分别令 Agent 留下正确/错误源码，而可见测试将自己的 `app.py` 改成相反版本。旧实现两项均失败：错误源码被计成功，正确源码被计 `holdout_assertion_failed`。
- **影响**：显式信任的 Host `eval-suite` 结果不可靠；不涉及默认 Docker 测试副本回写，也不是 Host 的安全隔离承诺。参考修复预检已有独立副本，无此复现路径。
- **最小修复**：在任何可见测试开始前，从 Agent 最终工作区分别生成可见/隐藏副本；在隐藏副本中安装 holdout。两项回归由失败转为通过；完成门槛和既有断言保持不变。
- **验证边界**：回归单独得到 **2 passed**；完整 `uv run --no-sync pytest -q` 得到 **259 passed**、无跳过，226.99 秒，包含本机 Docker 集成；Ruff 与 Git 空白检查通过，保留一条现有 Starlette/httpx 弃用警告。远端 CI/合并状态以对应 PR 为准。Host 依然需要显式信任；恶意测试访问其他绝对路径、伪造 JUnit 与断电事务不在此修复范围。

新增三题与原有两题共同通过 Docker 参考预检，覆盖不同的迭代/遍历机制；三题固定预算真实 DeepSeek 运行 **9/9**。契约、哈希、兼容性控制与完整运行限制见[参考控制记录](../benchmark/validated-candidates/iterator-traversal.md)。没有新增已确认 P0，正式 20 题验收仍未完成。

## 当前整合复核：2026-10-30

将 #24–#30 纳入同一交付版本，保留原提交与全部测试；解决 CLI 分派、参考预检/私有快照、控制模块导入和文档状态冲突。补充跨模块用例：JUnit 成功后中断写入，续跑必须撤销旧证据、重新测试并修复；环境与预算均保留；非法参考补丁不得创建模型或失败快照。

- P1-07 中断恢复、P1-08 非空 JUnit 门槛与 Host 字节码问题均包含在组合代码中。完整回归 **257 passed**、无跳过，包含本机 Docker；Ruff 通过，保留一条现存 Starlette/httpx 警告。
- 真实 DeepSeek 组合演示 5 轮完成失败→读取→编辑→重测，原测试未改，独立 Docker 4 项通过；这是生成夹具，不是新真实仓库评测。
- 锁定依赖经官方 PyPI 同步通过；本机默认镜像源曾返回 403，未更改锁文件或放宽版本。
- 尚未验证或完成：工作区内容指纹、断电持久性、恶意测试伪造证据防御、正式 20 题 × 3 次模型验收、完整产品 PR 交付流程。历史 4/4、8/9、两次 5/6 仍分别记录，不相加。
- 仓库交付状态与远端验证以 [#30](https://github.com/liuzhouquan/selfforge-agent/pull/30) 为准；本节不将本地通过写作已合并。

## 第一阶段：历史基线

2026-10-30 的 P1-08 修复记录见文末；前面的只读审计声明与测试数量保留为历史基线。

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

## 基线问题后续修复状态（截至 2026-10-29）

上面的等级和复现记录针对审计基线 `67cd21b`，不表示这些问题仍存在于当前 `main`。逐项对照当前源码后，P1/P2 均有后续修复提交；对应 PR 已合并。此表记录修复落点和仍未验证的范围：

| 问题 | 合并修复 | 当前代码/回归覆盖 | 剩余验证 |
| --- | --- | --- | --- |
| P1-01 Host 命令写入后沿用旧测试结果 | [#13](https://github.com/liuzhouquan/selfforge-agent/pull/13) | 持久化写入型命令会清除验证状态；`tests/test_runtime.py` | 未发现该基线问题仍存在 |
| P1-02 不完整 JSONL 尾行阻断恢复 | [#14](https://github.com/liuzhouquan/selfforge-agent/pull/14) | 只恢复完整换行事件；写入时清理未完成尾记录；`tests/test_runtime.py` | 未发现该基线问题仍存在 |
| P1-03 上下文压缩丢弃中间用户约束 | [#15](https://github.com/liuzhouquan/selfforge-agent/pull/15) | 保留所有用户消息；约束超出预算时明确失败；`tests/test_context.py` | 未发现该基线问题仍存在 |
| P1-04 本地 API 未校验 Host/Origin | [#12](https://github.com/liuzhouquan/selfforge-agent/pull/12) | `TrustedHostMiddleware` 和变更请求的 Origin/Fetch Metadata 校验；`tests/test_web_origin.py`；Chromium 真实浏览器检查见下文 | 动态 DNS 切换过程及反向代理/不同监听配置仍待验证 |
| P1-05 Docker 暂存缺少资源/时间上限 | [#16](https://github.com/liuzhouquan/selfforge-agent/pull/16) | 暂存加入文件数、字节数、打包大小、deadline 和取消边界；`tests/test_docker_staging.py` | 大型真实仓库的资源表现仍待观察 |
| P1-06 Provider 回退重复使用完整超时 | [#17](https://github.com/liuzhouquan/selfforge-agent/pull/17) | 一次模型调用共享 deadline，回退只用剩余时间；`tests/test_model_deadline.py` | 真实供应商网络时延分布仍未测量 |
| P2-01 Web Diff 先完整读取 Git 输出 | [#18](https://github.com/liuzhouquan/selfforge-agent/pull/18) | Git 输出按字节上限读取并报告截断；`tests/test_workspace.py` | 极大仓库的峰值内存未量化 |
| P2-02 并发供应商配置可能丢更新 | [#19](https://github.com/liuzhouquan/selfforge-agent/pull/19) | 完整读-改-写事务使用跨进程锁；`tests/test_providers.py` | 未发现该基线问题仍存在 |
| P2-03 损坏元数据使工作区列表整体失败 | [#20](https://github.com/liuzhouquan/selfforge-agent/pull/20) | 列表逐项隔离坏记录并保留诊断；`tests/test_workspace.py` | 未发现该基线问题仍存在 |
| P2-04 架构文档遗漏可见测试门槛 | [#21](https://github.com/liuzhouquan/selfforge-agent/pull/21) | 架构文档现与实现和 `docs/benchmark.md` 一致 | 文档差异已修复 |

本轮审计没有证实 P0。Coding Agent 的 20-task acceptance set 仍未完成，见 [roadmap](./roadmap.md)，这是评测覆盖目标，不是上述基线缺陷之一。

### P1-04 真实浏览器验证记录

2026-10-29 使用缓存的 Chromium 对本地 Uvicorn 实例进行了检查。跨端口来源 `http://127.0.0.1:8766` 向 `http://127.0.0.1:8765` 发起简单 POST，浏览器实际发送请求，服务端记录 `403 Forbidden`；同源 POST 继续到业务路由，对不存在的任务返回预期 `404`。另用 Chromium host resolver 将 `attacker.example` 映射到 `127.0.0.1`，通过该 Host 请求 `/health`，服务端记录 `400 Bad Request`。这验证了来源校验和可信 Host 检查在浏览器请求中的拒绝行为。该实验模拟了攻击域名解析到 loopback 时的请求状态，没有验证从公网地址切换到 loopback 的动态 DNS 时序，也没有覆盖反向代理部署。

## 2026-10-30 增量检查：Host 重测读取陈旧字节码（P1，已复现并在本分支修复）

- **位置**：`src/selfforge_agent/verification/runner.py:67` 的 Host pytest 启动命令；原实现没有隔离 Python 字节码缓存。
- **复现**：`tests/test_test_diagnostics.py::test_host_retest_ignores_stale_source_bytecode` 先用返回 0 的函数生成 `.pyc`，改成同尺寸的返回 1，再把修改时间固定回同一整数秒。修复前第二次 pytest 仍读取旧实现并失败；复现不依赖 sleep 或调度时机。
- **影响**：显式信任的 Host 测试可能验证旧源码，使修复后的结果失真；本轮证明的是旧实现造成误报失败，没有把未经反向故障注入的“误报通过”写作已验证事实。Docker 暂存排除了 `__pycache__`，不是本复现路径。
- **最小修复**：每次 Host pytest 使用临时 `-X pycache_prefix` 与 `-B`，忽略旧缓存且不写新缓存，不删除用户已有文件。代价是额外解释器启动工作。
- **验证**：修复前定向用例失败；修复后定向诊断/评测回归 29 项通过，完整 `uv run --no-sync pytest -q` **175 passed**（无跳过，含 Docker），Ruff 通过。一条现存 Starlette/httpx 弃用警告。真实 DeepSeek 生成夹具以 5 轮完成失败→编辑→重测，独立 Docker 4 项通过，原测试未改。
- **状态与剩余边界**：当前功能分支基于尚未合并的 #26，不代表 main 已修复；其他 Runtime 分支仍需合并后的联合回归。可选失败快照只保留有界独立测试诊断，不保留工作区、完整源码或模型历史，不能承诺完整重放。详见[测试诊断](./test-diagnostics.md)。

## 回归覆盖与剩余风险（第二阶段同步）

| 领域 | 已有覆盖 | 剩余验证 |
| --- | --- | --- |
| 完成状态 | 编辑、Host 命令、失败测试清除成功；本轮补中断恢复 | 外部编辑与成功测试之间缺少内容指纹；全部 skipped 仍可完成，见 P1-08 |
| 会话恢复 | 截断尾行、完整坏行、测试状态恢复、跨进程任务重启 | 磁盘满、断电后持久性、Web `can_resume` 与损坏会话的一致性 |
| Context | 保留中间用户约束、压缩/恢复、预算不足明确失败 | 真实长任务的摘要质量、精确 token 预算 |
| Web 权限 | 不可信 Host/Origin、浏览器跨端口请求拒绝 | 动态 DNS rebinding 时序、反向代理与不同监听配置 |
| Docker/资源 | 暂存数量/字节限额、超时、打包取消、容器清理、live 集成 | 磁盘满、清理失败、大仓库资源量化 |
| 模型与配置 | 回退共享 deadline、取消、配置并发锁 | 真实供应商失败分布、异常工具响应的结构校验 |
| Diff/工作区 | 大 patch 字节上限、路径列表截断、坏元数据隔离 | 并发外部修改/删除、CLI 工作区锁一致性 |
| 专项/评测 | Base/Head/Repaired、独立可见/隐藏测试 | 依赖复杂的项目、GitHub 网络故障、正式 20 任务 × 3 次真实模型运行 |

## 第一阶段修复规划（历史记录，已执行）

以下是第一阶段只读审计时的计划，执行状态见上表，不再作为当前待办。每个后续 PR 先为对应缺陷加入能失败的回归用例，再做最小修改；运行相关测试、`uv run --no-sync pytest -q` 与 `uv run --no-sync ruff check .`，涉及 Docker/Web 的 PR 再跑相应集成或浏览器验证，保留 Base/Head/Repaired 与评测门槛。

1. **P0 条件项 / P1-04**：先验证真实浏览器利用路径；若证实可绕过本地权限直接执行高风险操作，提升为 P0 并优先单独修复。即使未证实攻击链，也为已确认的 Host/Origin 校验缺口单独提交 P1 修复与边界测试。
2. **P1-01**：统一“最后一次可能写入”与测试证据的工作区指纹/状态失效规则。回归 Host 路径及 Docker 不回写路径。
3. **P1-02、P1-03**：分别处理 JSONL 恢复和用户约束压缩；每项独立 PR，验证续跑和重新压缩。
4. **P1-05、P1-06**：分别给 Docker 暂存和多供应商回退设总预算；测试超限、慢失败及清理。
5. **P2-01～P2-04**：按资源影响和维护收益拆成小 PR；每次保留已有断言，不以重构或代码风格替代行为修复。

尚未验证的结论在条目中逐一标明。本报告不把没有复现的竞态、真实网络攻击或性能量化写成已证实故障。


## 2026-10-30：P1-08 通用 Coding 的 JUnit 证据门槛

**状态：本分支修复，未合并。** 本次从已合并的 `278ff34` 开始；#24 的评测候选集及 #25 的中断恢复修复仍是独立 PR。P1-08 的首次复现记录在 #25 的审计文档中，此处记录它的实施与验收。

- **问题与影响**：`runtime/tools.py` 的 `_test()` 先前只检查 pytest 退出码。一个只包含 `pytest.skip()` 的测试文件会产生 `exit=0`、`1 skipped`，随后 CodingAgent 可返回 `completed`。旧版 `SessionStore.verification_passed()` 也会恢复这种成功。影响通用 Coding 的完成状态；benchmark 独立可见/隐藏验证原本已有 `passed > 0` 门槛。
- **复现与回归**：`tests/test_coding_evidence.py` 的 Host/真实 Docker 用例构造全部跳过的测试，再让脚本模型结束任务。修复前 Host 得到 `completed`；修复后为 `unverified`，续跑仍不能恢复成功；替换成真实通过的测试并重跑后可完成。初版新增断言在旧实现上 21 项失败、1 项通过、1 项集成测试未选择，包含证据门槛和新返回格式的检查，不代表发现了 21 个独立缺陷。
- **实现位置**：`runtime/tools.py::_test` 复用已有 `TestRunner`/`DockerTestRunner`；`verification/runner.py::_result_status` 要求退出码 0、至少一项通过、没有失败/错误，`_junit_counts` 拒绝负数或相互矛盾的计数。缺失/损坏 XML、空选择和全部跳过均不能成功。
- **执行边界**：Docker 复用已有 executor，校验工作区和镜像，保留 Web 取消标记。Host 保持显式信任，使用 Agent 所在 Python 环境；Ctrl-C 会杀死并回收测试进程。未知执行器不允许降级为只看退出码。
- **恢复兼容**：新的私有会话验证事件标记 `evidence=junit-v1`。旧消息保持可读，但旧的无标记成功状态必须重测；不改写历史 Web job 显示的旧状态。该改动不替代 #25 对中断顺序的修复。
- **专项流程**：共享 Runner 的严格门槛也作用于 Repair；新增全部跳过 Base 的拒绝用例，保留既有 Base/Head/Repaired 通过、失败、修复控制组。

| 本次验证 | 结果 |
| --- | --- |
| `uv run --no-sync pytest -q tests/test_coding_evidence.py tests/test_repair_agent.py` | **38 passed**；包含真实 Docker 的全跳过拒绝与随后成功重测 |
| `uv run --no-sync pytest -q` | **158 passed**，无 skipped，62.36 秒；1 条已有 Starlette/httpx 弃用警告 |
| `uv run --no-sync ruff check .`、`git diff --check` | 通过 |
| `uv run --no-sync python examples/coding_demo.py` | **2/2**，脚本模型驱动真实文件编辑和独立 pytest；不是新的真实模型评测成绩 |
| 与 #25 的源码组合检查 | **67 passed**（Runtime、#25 中断恢复和新证据测试）；临时源码快照，不修改或合并 PR 分支。首次直接启动解释器时 1 项评测失败，诊断为子命令使用未安装 pytest 的系统 Python；改用项目 uv 环境后通过 |

边界：JUnit 来自被执行的项目，不防恶意测试伪造报告，也不证明测试覆盖完整；证据仍未绑定工作区内容指纹。本轮未测供应商 API 或新增真实模型评测。下一步优先完善带额外项目依赖的可复现镜像，再扩充经过任务契约和参考修复审核的验收集。证据协议与复现命令见 [test-evidence.md](./test-evidence.md)。
<a id="runtime-recovery-review"></a>

## 第二阶段：2026-10-29，Runtime 恢复与开发文档

### 基线、范围与结论

- 当前 main 为 `278ff34`；PR #24 的 `a680788` 候选评测清单尚未合并。本轮修复独立基于 `278ff34`，不依赖 #24。
- 阅读 README/双语产品边界/路线图/架构/AGENTS，重点复核 `runtime.agent`、`session`、`context`、`tools`、`model`、`verification.runner`、`api.coding_jobs` 的控制流及相关测试，抽查 benchmark 输入约束和 CI。
- **本轮为重点复核，不是全仓重新逐行审计**。既有 Runtime/Harness 分层可以沿用，未发现本轮需要新建框架或大规模重构的依据。
- 未确认新的 P0。确认一个中断恢复 P1（P1-07，本分支修复）、一个测试证据 P1（P1-08，下一独立修复）及文档 P2（P2-05，本分支修正）。

### P1-07：中断窗口可使过期测试证据在续跑时重新生效（已复现）

- **位置**：基线 `runtime/agent.py:85-88,149-162`；`_resume_pending()`、`run()`。调用 `execute()` 后才追加 `verification=false`；旧会话未完成工具调用仅补一条中断回复。新任务也是先保存消息再清证据。
- **复现**：先实际运行通过的 pytest，再编辑源码或执行可信 Host 命令，在工具已执行但未返回时注入 `KeyboardInterrupt`/`SystemExit`；历史仍是 `verification=true`。另构造旧版未完成调用日志、在新任务消息落盘后中断、令证据写入失败，分别触发过期状态或先改源码后报错。
- **影响范围**：Web/CLI 共用 Runtime 的中断与恢复；进程异常退出、Ctrl-C、会话存储故障可能令未验证修改显示为完成。Docker bash 不回写，但新一轮测试中断也应清除旧证据。
- **最小修复**：在写操作、重测、新任务之前记录证据失效；旧日志若有未完成的 edit/bash/run_tests，先失效再补回复。读工具不清除证据。保存失效失败时不得执行写操作。
- **验证**：`tests/test_runtime_recovery.py` 初始 12 个用例在旧实现 **11 failed / 1 passed**；另补真实子进程 `os._exit(73)` 回归。包含续跑后 `unverified`、重新测试后 `completed` 和重复恢复的检查。修复后结果见下方验证表。
- **边界**：这解决进程中断顺序，不承诺断电事务一致性，不检测外部编辑器改动；旧日志已有合成回复且已经丢失中断信息时，无法逆向重建证据。

### P1-08：全部跳过的测试也会给通用 Coding 标记 completed（已复现，未修复）

- **位置**：`runtime/tools.py:259-274` 的 `_test()` 仅判断退出码与超时；`runtime/agent.py` 使用 `outcome.ok` 建立证据。评测器则另行要求 `passed > 0`。
- **复现**：临时仓库只有一个 `pytest.skip('not configured')` 的测试；脚本模型依次返回 `run_tests` 和最终答复。本轮实际结果为 `exit=0`、`1 skipped`、`agent_status=completed`。
- **影响范围**：通用 Coding 的完成状态可能没有任何真正执行通过的测试。独立 benchmark 的非空通过门槛仍生效，不应把此结论扩大为评测器失效。
- **最小修复建议**：复用 JUnit-backed Runner，使通用工具也要求至少一个通过的测试且无 failure/error；不要解析 stdout 判断计数。补全部 skipped、空选择、缺失/损坏报告、超时以及正常成功控制组。独立小 PR 完成并验证 Host/Docker 两条路径。

### P2-05：审计待办和开发顺序已过时（已确认并修正文档）

- **位置**：原审计“缺少的关键测试”仍列出已补的尾行恢复、用户约束、deadline、供应商锁等；末尾计划写着“本轮不实施修复”，却没有标注是历史阶段。README/产品计划优先扩充题数，缺少贡献者的检查入口和单项验收条件。
- **影响**：可能重复开发已有修复，或把候选题数量误当产品可靠性进度。
- **最小修复**：保留历史证据与修复表，更新当前缺口；新增 `docs/development.md` 的职责映射、验证命令和逐项验收；同步双语入口/产品顺序、Roadmap 和 AGENTS。先可靠性/测试证据/依赖环境，再审核并冻结正式评测任务。

### 本轮实际验证

| 检查 | 结果与范围 |
| --- | --- |
| 初始完整 pytest | 开始时检出 `a680788`，收集 131 项：**131 passed**，1 条 Starlette/httpx 弃用警告；测试期间已切至 main 基线，故此项只作初查，不作为最终分支验收 |
| 初始 Ruff | 通过 |
| 修复前中断回归 | **11 failed / 1 passed**，断言失败与上述窗口一致 |
| 修复后相关回归 | Runtime、恢复、Context、Web 持久任务：**45 passed**，1 条同样警告（随后新增真实子进程退出用例） |
| 全部 skipped 复现 | 实际 pytest 返回 `1 skipped`，Agent 返回 `completed`；P1-08 保持未关闭 |
| 最终中断回归 | **13 passed**，包括真实子进程退出、两类异常中断、旧会话、日志写入失败和新任务窗口 |
| 最终全量 pytest | `uv run --no-sync pytest -q`：**143 passed**，无 skipped，52.80 秒；1 条现存 Starlette/httpx 弃用警告，包含本机 Docker 集成测试 |
| 最终静态/格式检查 | `uv run --no-sync ruff check .` 与 `git diff --check` 均通过 |
| 离线端到端演示 | `uv run --no-sync python examples/coding_demo.py`：**2/2**，真实文件编辑并独立 pytest 验证；脚本模型，不是真实模型评测 |

未调用真实模型或执行新的评测任务；不发布新的模型成功率。外部网络故障、断电、恶意测试伪造报告、动态 DNS 攻击及大仓库峰值资源仍未验证。本轮后续按 P1-08 → 依赖环境 → 候选任务质量/正式评测推进，验收条件见[开发指南](./development.md)。
