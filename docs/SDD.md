# selfForge Agent：开发规格与交付协议（SDD）

本文件是新 Agent 的**首读文档和当前任务状态的唯一维护入口**。读完即可选择下面的 Ready 任务并开始；仅在涉及具体模块时查阅链接的详细契约，不要求先遍历全部历史文档。

- 文档版本：1；更新：2026-11-16。
- 已核验产品基线：`04452154addaeff3375fcd15996585c2d5e374e3`；开工必须重新 fetch 并记录实际基线，不把这里的 SHA 当作永远最新。
- 前轮 SF-DEV-01 / SF-DOC-01 / SF-TEST-01 的已验证快照：源码 `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`，完整 pytest 653 passed、无跳过。合并状态以第 4 节及审查记录为准；SF-EVAL-08 的新策略与无模型预检已交付；该策略下的真实模型测量尚未运行。
- 当前用户指令优先。SDD 不增加外部服务、付费模型调用、推送或合并授权，也不增加重复确认程序；沿用会话已有授权和仓库保护。

## 1. 产品目标与范围

**selfForge Agent — A Python Coding Agent with Reliable Execution and Self-Testing**。

面向 Python Git 仓库，从 Web/CLI 接收自然语言任务，在独立 worktree 中自主读、搜索、修改代码，在 Docker 中执行测试，交付可检查的 Diff、测试证据与执行轨迹；支持取消和显式恢复。PR Review、差异测试与 Repair 是专项工作流。

产品主线：**任务 → 受限工具调用 → 修改代码 → 隔离测试 → 证据验证 → 用户检查 → 显式 commit/PR**。

本阶段不增加产品 Multi-Agent、通用 MCP、额外 RAG、大型前端、跨语言 IDE 或另一套 Agent Runtime。开发者之间并行协作与产品 Multi-Agent 是两件事；是否使用子 Agent 服从当前工具/用户规则。

### 已实现与仍有边界

| 能力 | 已有实现 | 不应推断的能力 |
| --- | --- | --- |
| Runtime | 原生 tool calling、结果回填、步数/时间预算、只读/编辑失败/上下文提示 | 提示改善不等于已证明模型成功率提高 |
| 模型与界面 | OpenAI-compatible、多供应商预设/回退、模型发现、Web 工作台 | 预设不保证供应商任意账号/产品协议均兼容 |
| Harness | 持久会话/轨迹、启发式上下文压缩、取消、恢复、非空 JUnit 与工作区指纹 | 非精确 tokenizer；非断电事务；阻塞 HTTP 不保证即时取消 |
| 隔离与工作区 | Docker 默认、Host 显式信任、持久 worktree、共享协同锁、dirty guard、审计/prune | 文件指纹非原子快照；外部编辑器/Git/不同 XDG 不参与锁 |
| 环境 | 只读发现、有限锁导出、离线准备计划、wheelhouse 审计、显式镜像构建/会话绑定、声明诊断 | 不自动安装；哈希可用不等于可安装；Host markers 不等于目标容器解析 |
| 测试与交付 | Review/差异测试/Repair、worktree Diff、显式提交/PR、重试恢复、CI | 通过既有测试不代表全部需求充分验证；不自动合并 PR |
| Eval | 冻结 v2 20 题、两道独立多文件候选、无模型参考正负控、来源元数据/报告比较 | 两道新题未加入历史正式成绩；不能把旧分数当作当前 Runtime 成绩 |

已核验基线的本地完整 pytest 为 568 passed，无跳过；[基线 main CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43135807788) 为 555 单元 + 13 Docker，无跳过。此数字仅属于上述基线，不是后续任务验收配额。历史 53/60 模型成绩及来源限制见[评测记录](./evaluation.md)。

## 2. 开工与选择任务

```sh
git status --short --branch
git worktree list
git fetch origin
git rev-parse origin/main
gh pr list --state open
```

1. 核对当前项目，不因 IDE 标签切换到其他仓库。未提交内容先确认归属；不 reset、clean 或覆盖未知文件。
2. 查开放 PR 与本表 Owner，已有工作先接手/审查，不重复实现。各 Agent 使用自己拥有的分支和 worktree。
3. 用户指定任务时执行该任务；仅说“继续开发”时，选**优先级最高、前提已满足且未被认领的一项 Ready**。完成一项后交接，不自行遍历整份路线图。
4. 领取时在本文件该任务的执行记录填 Owner、完整实际 base SHA、分支/worktree 标识、依赖与状态。不要新建一份重复规格。
5. 没有 Ready 时，先把最接近的 Waiting/Needs Design 任务补足前提/规格；不得把历史已完成项当作新任务。只有确实缺少用户决策才提问，独立工作继续推进。

创建独立工作区的例子（名称换成自己拥有且未占用的名称）：

```sh
git worktree add ../selfforge-sf-dev-01 -b feat/sf-dev-01 origin/main
```

### 状态与文档权威

当前任务状态仅在本文件维护：`Ready → In Progress → In Review → Merged`；依赖缺失为 `Waiting`，行为尚未定清为 `Needs Design`。`In Review` 不代表合并；`Merged` 必须有合并 SHA 和 PR，远端不可用时保持真实状态。

- 本文件维护产品主线、当前优先级、规格、验收及当前交接。范围变化先更新这里的原因、依赖和验收，再实施。
- [AGENTS.md](../AGENTS.md) 是自动加载的约束入口；[工程不变量](./engineering-invariants.md)是具体权限/状态/证据契约，本文件不放宽它们。发现冲突用源码/复现确认，不能自行选更宽松的一条。
- [开发指南](./development.md)保留安装与模块索引；[架构](./architecture.md)保留实现细节。只读涉及模块的部分。
- [Backlog](./backlog.md)、`docs/tasks/` 和旧审计是历史证据，不是另一个实时任务队列。历史验证保留，不能把旧结果改成新结果。
- 新任务直接增加本文件中的规格和执行记录；[旧模板](./task-template.md)只供历史记录/外部 Issue 兼容使用，不要求再复制文件。
- PR 描述引用任务 ID/验收 ID，并承载准确提交与 CI 证据；不复制整份 SDD。合并时同步本文件当前状态，避免在多个文件追加竞争的“最新状态”。

## 3. 实际架构与必须保持的约束

```mermaid
flowchart LR
    U[Web / CLI 自然语言任务] --> W[工作区与协同锁]
    W --> R[CodingAgent 原生循环]
    R <--> M[模型适配与回退]
    R <--> T[ToolRegistry]
    R <--> S[Session / Context / Trace]
    T --> F[受限文件工具]
    T --> D[Docker 执行与 JUnit Runner]
    D --> E[测试结果与工作区指纹]
    E --> R
    W --> P[Diff 与显式 commit / PR]
```

| 改动领域 | 先读的实现（相对 `src/selfforge_agent/`） | 关键回归入口（相对 `tests/`） |
| --- | --- | --- |
| Runtime/Context | `runtime/agent.py`、`control.py`、`context.py`、`model.py` | `test_runtime_budget.py`、`test_runtime_recovery.py`、`test_context*.py` |
| 工具/隔离/证据 | `runtime/tools.py`、`execution.py`、`fingerprint.py`；`verification/` | `test_coding_evidence.py`、`test_docker_staging.py`、`test_workspace_fingerprint.py` |
| 工作区/交付 | `runtime/workspace.py`、`workspace_diff.py`、`delivery.py` | `test_workspace_prune.py`、`test_delivery_safety.py`、`test_workspace_dirty_guard.py` |
| Web/模型 | `api/app.py`、`api/coding_jobs.py`、`providers.py` | `test_coding_web.py`、`test_web_origin.py`、`test_providers.py` |
| 环境 | `runtime/environment.py`、`images.py`、`lockfiles.py` | `test_dependency_freshness.py`、`test_wheelhouse_audit.py`、`test_session_environment.py` |
| 测试生成/Repair | `coding/test_generation.py`、`coding/agent.py`、`coding/worktree.py` | `test_test_generation.py`、`test_repair_agent.py`、`test_docker_repair.py` |
| 独立 Eval | `runtime/benchmark.py`、`benchmark_diff.py` | `test_reference_validation.py`、`test_benchmark_isolation.py`、`test_benchmark_diff.py` |

不可回退的约束：

1. 保持一个通用 `CodingAgent`、共享 ToolRegistry/JUnit Runner；Repair 的独立 proposer 是现有边界，不新增竞争 Runtime。
2. Docker 是默认执行方式；Host 必须显式信任。文件保护、路径/链接校验、网络/资源限制、输出/输入/超时上限继续生效；不默认运行仓库 hooks、setup.py 或自动安装脚本。
3. `completed` 必须有非空 JUnit 成功、测试前后及当前指纹一致、非空最终答复。bash 退出 0、旧测试布尔值、LLM 自评均不能替代证据。
4. 新任务、写入/编辑尝试、持久 Host 命令或新测试之前先持久化撤销旧证据；取消、超时、异常、恢复均不能复活失效证据。
5. 保持 assistant/tool 消息成组与 user 约束；预算提示瞬态生成，不能持久化成旧预算；token 估算明确标为启发式。
6. CLI/Web 对同一工作区使用共享锁。持久 worktree 不强制删除；dirty/ignored 用户输入保留；prune 在锁内重查；commit/PR 证据只绑定实际推送 SHA。
7. 私有会话/密钥保持受限权限；trace 仅元数据，不泄漏模型正文、推理、原始错误、工具参数或私有路径。日志、密钥、依赖缓存、课程源码不提交。
8. Eval 固定源 SHA/清单/镜像，外部 holdout 与 reference 不进 Agent；基线负控与独立 visible/holdout 正控、允许修改范围、非空 JUnit 缺一不可。冻结资产/历史成绩不得覆写。
9. 缺失输入、扫描不完整和环境不可用要显式表示，不当作通过；费用未知不能编造；堆栈关联不能写成因果证明。

## 4. 下一阶段里程碑与任务队列

当前阶段目标：**使新环境可复现开发与验证，让规格能追溯到测试，并把自主编程后的回归测试验证补成可复用流程。** 不按功能数量或测试数量宣称产品完成。

| 顺序 | 任务 ID | 状态 | Owner | 前提 | 交付结果 |
| --- | --- | --- | --- | --- | --- |
| 1 | [SF-DEV-01](#sf-dev-01) | Merged | Codex（审查整合） | 无 | [PR #57](https://github.com/liuzhouquan/selfforge-agent/pull/57)：新 checkout 与 CI 复用同一个固定评测源准备入口 |
| 2 | [SF-DOC-01](#sf-doc-01) | Merged | Codex（审查整合） | 本 SDD | 有界 SDD 一致性检查，阻止失配状态与虚假验收引用（[PR #58](https://github.com/liuzhouquan/selfforge-agent/pull/58)） |
| 3 | [SF-TEST-01](#sf-test-01) | Merged | Codex（审查整合） | 既有 Runner/worktree/指纹 | 对 Coding 修复提供独立的旧失败/新通过回归证据 |
| 4 | [SF-EVAL-08](#sf-eval-08) | Merged | Codex（审查整合） | SF-DEV-01；策略已冻结 | [PR #61](https://github.com/liuzhouquan/selfforge-agent/pull/61)：新 Runtime 22 题 v3 评测规格、冻结输入与无模型 Docker 预检 |
| 5 | [SF-TEST-02](#sf-test-02) | Merged | Codex（审查整合） | SF-TEST-01（Merged） | [PR #62](https://github.com/liuzhouquan/selfforge-agent/pull/62)：Web Coding 工作台接入独立回归验证面板、状态门槛与结构化证据展示 |
| 6 | [SF-WS-05](#sf-ws-05) | Merged | Codex（审查整合） | SF-WS-04 / SF-TEST-01（Merged） | [PR #63](https://github.com/liuzhouquan/selfforge-agent/pull/63)：修复 `/api/workspaces/storage` 管理器复用不一致并补齐 CLI `trace` / `test-regression` 安全结构化导出 |
| 7 | [SF-WS-06](#sf-ws-06) | Merged | Codex（审查整合） | SF-WS-04 / SF-WS-05 / SF-ENV-06（Merged） | [PR #64](https://github.com/liuzhouquan/selfforge-agent/pull/64)：Web Coding 工作台接入工作区审计/存储/安全清理（`audit`/`storage`/`prune`）与依赖新鲜度诊断（`/api/sandbox/freshness`） |
| 8 | [SF-EVAL-09](#sf-eval-09) | Merged | Codex（审查整合） | SF-EVAL-08 / SF-WS-05（Merged） | [PR #65](https://github.com/liuzhouquan/selfforge-agent/pull/65)：CLI `eval-suite` / `eval-validate` 接入 `--policy` 冻结策略强校验、预算门禁与安全导出（`_write_cli_output`） |
| 9 | [SF-ENV-07](#sf-env-07) | Merged | Codex（审查整合） | SF-WS-05 / SF-ENV-06（Merged） | [PR #66](https://github.com/liuzhouquan/selfforge-agent/pull/66)：CLI `sandbox` 诊断与规划命令补齐结构化安全导出（`--json` / `--output`）与 `export-lock` 安全写盘 |
| 10 | [SF-TEST-03](#sf-test-03) | Merged | Codex（审查整合） | SF-TEST-02 / SF-WS-05（Merged） | [PR #67](https://github.com/liuzhouquan/selfforge-agent/pull/67)：SessionStore 补齐回归验证记录追溯与 GET /api/code/workspaces/{session_id}/regression 只读接口，并在 PR 交付中绑定回归验证证据 |
| 11 | [SF-TEST-04](#sf-test-04) | Merged | Codex（审查整合） | SF-TEST-02 / SF-TEST-03 / SF-WS-06（Merged） | [PR #68](https://github.com/liuzhouquan/selfforge-agent/pull/68)：Web Coding 工作台接入持久化回归验证记录恢复与会话切换状态同步 |
| 12 | [SF-CLI-01](#sf-cli-01) | Merged | Codex（审查整合） | SF-WS-05 / SF-ENV-07（Merged） | [PR #69](https://github.com/liuzhouquan/selfforge-agent/pull/69)：CLI `review` / `repair` / `generate-test` 安全落盘防符链穿透与结构化 `--json` 导出对齐 |
| 13 | [SF-WS-07](#sf-ws-07) | Merged | Codex（审查整合） | SF-WS-05 / SF-WS-06（Merged） | [PR #70](https://github.com/liuzhouquan/selfforge-agent/pull/70)：CLI `workspaces` 全动作（`list`/`diff`/`commit`/`pr`/`revert`/`remove`/`audit`/`prune`/`storage`）结构化 `--json` 与安全 `--output` 导出对齐 |
| 14 | [SF-RUN-07](#sf-run-07) | Merged | Codex（审查整合） | 无 | [PR #71](https://github.com/liuzhouquan/selfforge-agent/pull/71)：ToolRegistry 搜索预算上限中断与截断通知、文件读取非普通文件防御（FIFO/目录）与编辑/测试边界防御 |
| 15 | [SF-WS-08](#sf-ws-08) | Merged | Codex（审查整合） | 无 | [PR #72](https://github.com/liuzhouquan/selfforge-agent/pull/72)：WorkspaceDiff 与 Delivery 安全加固：未跟踪符号链接目录隔离、不可读文件安全回退与未跟踪断链/目录符号链接安全清理 |
| 16 | [SF-VERIF-01](#sf-verif-01) | Merged | Codex（审查整合） | 无 | [PR #73](https://github.com/liuzhouquan/selfforge-agent/pull/73)：验证执行器与 JUnit 解析安全加固：有限正超时校验、非普通文件（FIFO/设备）与受保护路径防御、深层递归容错与无符链穿透测试工件读取 |
| 17 | [SF-ENV-08](#sf-env-08) | Merged | Codex（审查整合） | SF-ENV-07（Merged） | [PR #74](https://github.com/liuzhouquan/selfforge-agent/pull/74)：依赖输入与锁文件安全加固：平台条件标记 fail-closed 拒绝、PEP 503 包名规范化与非阻塞/无符链安全读取 |
| 18 | [SF-EVAL-10](#sf-eval-10) | Merged | Codex（审查整合） | SF-EVAL-09（Merged） | [PR #75](https://github.com/liuzhouquan/selfforge-agent/pull/75)：评测执行器与基准测试安全加固：未绑定状态防御、非阻塞常规文件读取、测试拷贝防FIFO阻塞与原子工件安装 |
| 19 | [SF-TEST-05](#sf-test-05) | Merged | Codex（审查整合） | SF-TEST-04（Merged） | [PR #76](https://github.com/liuzhouquan/selfforge-agent/pull/76)：差分测试生成与因果推断安全加固：测试工件原子写盘防符链穿透、AST 深层递归与畸形异常防御、观察读取防 FIFO 挂起与有界因果推断日志防御 |
| 20 | [SF-API-01](#sf-api-01) | Merged | Codex（审查整合） | 无 | [PR #77](https://github.com/liuzhouquan/selfforge-agent/pull/77)：Web Coding API 与会话管理安全加固：请求体大小防御（413）、会话 ID 路径穿越防御、DurableCancel 防符链与有界作业记录读取 |
| 21 | [SF-RUN-08](#sf-run-08) | Merged | Codex（审查整合） | 无 | [PR #78](https://github.com/liuzhouquan/selfforge-agent/pull/78)：运行时执行器与测试失败工件安全加固：工件原子读写防符链穿透/FIFO阻塞、执行器超时与命令有界校验、工作区根目录隔离 |
| 22 | [SF-PROV-02](#sf-prov-02) | Merged | Codex（审查整合） | 无 | [PR #79](https://github.com/liuzhouquan/selfforge-agent/pull/79)：模型供应商配置与 HTTP 传输安全加固：SSRF 协议限制与本地文件读取杜绝、CRLF 响应头注入防御、配置读取防符链/FIFO阻塞、进程锁加固 |
| 23 | [SF-INT-01](#sf-int-01) | Merged | Codex（审查整合） | 无 | [PR #80](https://github.com/liuzhouquan/selfforge-agent/pull/80)：GitHub PR 提取与检出安全加固：命令注入与参数投毒防御、URL 规范化、工作区路径防符链穿透、Diff Hunk 解析有界防御 |
| 24 | [SF-PRICE-01](#sf-price-01) | Merged | Codex（审查整合） | 无 | [PR #81](https://github.com/liuzhouquan/selfforge-agent/pull/81)：模型费率卡与成本估算安全加固：JSON 反序列化深度防御、Token 计数严格类型与非负校验、货币代码规范化、浮点溢出安全回退 |
| 25 | [SF-GH-01](#sf-gh-01) | Merged | Codex（审查整合） | 无 | [PR #82](https://github.com/liuzhouquan/selfforge-agent/pull/82)：GitHub PR 抓取与检出安全加固：URL 严格正则与选项防注入、变更文件路径防穿越与状态规范化、工作区软链接防御、命令输出有界防御 |
| 26 | [SF-CORE-01](#sf-core-01) | Merged | Codex（审查整合） | 无 | [PR #83](https://github.com/liuzhouquan/selfforge-agent/pull/83)：Git 差异上下文与补丁解析安全加固：仓库路径软链接防御、差异解包受控映射、补丁与行号有界防 DoS |
| 27 | [SF-REV-01](#sf-rev-01) | Merged | Codex（审查整合） | 无 | [PR #84](https://github.com/liuzhouquan/selfforge-agent/pull/84)：代码审查引擎安全加固：规则基线有界防御、模型审查输入/输出防御与执行隔离 |
| 28 | [SF-CTRL-01](#sf-ctrl-01) | Merged | Codex（审查整合） | 无 | [PR #85](https://github.com/liuzhouquan/selfforge-agent/pull/85)：运行时控制流与瞬态指导安全加固：工具循环检测防御、编辑失败循环防护、上下文压力评估与预算通知有界防御 |
| 29 | [SF-MODEL-01](#sf-model-01) | Merged | Codex（审查整合） | 无 | [PR #86](https://github.com/liuzhouquan/selfforge-agent/pull/86)：编码模型客户端安全加固：有限正超时校验、Base URL与凭据防御、输入观测类型与有界负载防御、Markdown代码块剥离与CodeEdit边界校验 |
| 30 | [SF-WT-01](#sf-wt-01) | Merged | Codex（审查整合） | 无 | [PR #87](https://github.com/liuzhouquan/selfforge-agent/pull/87)：Git worktree与临时分支执行安全加固：子进程执行与有界读取防御、修订与参数注入防御、目标路径与符号链接隔离防御、清理容错与工作区元数据修剪 |
| 31 | [SF-RISK-01](#sf-risk-01) | Merged | Codex（审查整合） | 无 | [PR #88](https://github.com/liuzhouquan/selfforge-agent/pull/88)：PR风险优先级排序与RiskTarget契约边界加固：上下文与发现类型Fail-Closed校验、扫描深度与代码行有界防御、PrioritizedRisk模式与空字节防御、确定性排序与有效转换 |
| 32 | [SF-FPR-01](#sf-fpr-01) | Merged | Codex（审查整合） | 无 | [PR #89](https://github.com/liuzhouquan/selfforge-agent/pull/89)：工作区快照指纹哈希安全加固：root输入防御与斜杠绕过软链接防御、有限正数deadline与cancel状态机对齐、目录递归自循环防御、非普通文件与负大小防御 |
| 33 | [SF-CTX-01](#sf-ctx-01) | Merged | Codex（审查整合） | 无 | [PR #90](https://github.com/liuzhouquan/selfforge-agent/pull/90)：模型上下文管理器与结构化压缩防御加固（`estimate_tokens` / `estimate_message_tokens` / `_recent_interactions` / `ContextManager`） |
| 34 | [SF-DELIV-01](#sf-deliv-01) | In Review | Codex（审查整合） | 无 | [PR #94](https://github.com/liuzhouquan/selfforge-agent/pull/94)：工作区交付工作流（commit/revert/pr）输入守门、配置注入防御与证据绑定加固 |

阶段完成条件：前三项验收全部闭合且已合并；新环境完整验证可复现；通用修复验证具备独立旧失败/新通过的 Docker 证据；当前规格能追溯到测试与准确提交。SF-EVAL-08 是后续测量准备，仍不代表产品的真实任务可靠性已经达标。

上表同时记录当前交付状态与未来任务。需要并行认领时先核对开放 PR；状态/Owner 回填到同一行。多个分支基于最新 main 开发，合并前更新基线并复查公共入口，不机械拼接表尾。

### SF-DEV-01

**目标：** 新 checkout 不依赖开发者 `.local` 缓存即可按明确命令准备两道候选的固定源；CI 复用同一准备入口。只显式获取 Git 对象，不执行外部代码。

**范围/入口：** 新建 `scripts/prepare_eval_sources.py`，参数 `--root PATH`、`--timeout SECONDS`（总预算，默认 120，合法范围 1–300）、`--offline`。固定登记 jmespath/boltons 的现有 URL 与清单修订；复用 `.github/workflows/ci.yml` 的策略、`tests/conftest.py` 的 `SELFFORGE_EVAL_SOURCE_ROOT`。不开发任意 URL 下载器/新缓存平台，不安装项目依赖、不调用模型。

**行为：** 显式在线模式仅 fetch 固定 URL/SHA，并创建可被独立 clone 获取的 named ref；已有完整缓存离线可用且不变更其 HEAD。缺失缓存离线失败。拒绝 symlink 根/目标、非本任务拥有的已有非仓库目录和缺少预期来源的缓存；不删除/重置已有文件。用 argv subprocess、总 deadline 和有界输出；失败退出非零，不伪报 ready。首次创建需标记本入口拥有的目录；失败留下可诊断但不执行的状态。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-DEV-01-A1 | 空临时根显式在线准备后，两固定提交能从生成仓库独立 clone/checkout | `tests/test_eval_source_preparation.py`；本地 Git remote 替身测控制流，CI 实际固定 URL | E5（本轮组合复验） |
| SF-DEV-01-A2 | 已有合格缓存使用 `--offline`，成功且不联网、不改变用户 HEAD/文件 | 同文件 CLI 回归 + 前后状态；替身阻断 fetch | E6（本轮组合复验） |
| SF-DEV-01-A3 | 超时、坏源、缺提交、离线缺缓存、symlink/未知目录输入均失败，保留已有内容 | 同文件错误注入；可观察退出码与磁盘内容 | E7（本轮组合复验） |
| SF-DEV-01-A4 | 新 checkout 按文档先准备再运行全部测试；CI 不再复制 fetch 命令 | 真实 CI + Docker 两候选控制；不需要模型密钥 | E8（本轮组合复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-DEV-01-A1 | `tests/test_eval_source_preparation.py::test_online_preparation_creates_cloneable_repos_and_ownership_marker`、`tests/test_eval_source_preparation.py::test_pinned_sources_match_candidate_manifests_and_policy`、`tests/test_eval_source_preparation.py::test_default_root_uses_env_var_and_cli_subprocess`、`tests/test_eval_source_preparation.py::test_online_fetch_into_existing_repo_preserves_head_and_candidate_branch`；GitHub Actions `Provision pinned multi-file candidate sources` | Host 本地 Git remote 替身 + `git clone --no-local`；GitHub `ubuntu-latest` CI 真实拉取 pinned GitHub URL；[PR #57](https://github.com/liuzhouquan/selfforge-agent/pull/57) | 源码 SHA `57117238f34c9082b5ce3b3b9e6b1931f2b0549d`；本地专项 18 passed / 非集成 573 passed, 13 deselected / Ruff passed；CI [Run #43150979908](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43150979908) 真实固定 URL 准备成功（jmespath `2812594e69d43098ef60f81f4efc404c071b0418`、boltons `d31d821d56c8f7756af9c30a864077f2e5855321`） |
| E2 | SF-DEV-01-A2 | `tests/test_eval_source_preparation.py::test_offline_mode_succeeds_without_fetch_and_preserves_user_head_and_files` | Host 本地缓存 + git wrapper 阻断 mutating/fetch 命令 + 前后 HEAD 与文件比对；[PR #57](https://github.com/liuzhouquan/selfforge-agent/pull/57) | `57117238f34c9082b5ce3b3b9e6b1931f2b0549d`；本地与 CI [Run #43150979908](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43150979908) passed（573 passed, 13 deselected） |
| E3 | SF-DEV-01-A3 | `tests/test_eval_source_preparation.py::test_invalid_timeout_cli_rejected`、`tests/test_eval_source_preparation.py::test_timeout_kills_slow_git_and_leaves_diagnosable_unready_state`、`tests/test_eval_source_preparation.py::test_online_bad_remote_and_missing_commit_fail_and_recover_on_retry`、`tests/test_eval_source_preparation.py::test_run_git_stdout_overflow_fails_closed`、`tests/test_eval_source_preparation.py::test_offline_rejects_missing_cache_bad_origin_and_orphan_commit`、`tests/test_eval_source_preparation.py::test_rejects_symlinks_unknown_dirs_and_nested_parent_repo_without_modification`、`tests/test_eval_source_preparation.py::test_bounded_output_and_corrupt_state_file_fail_closed` | Host CLI 子进程退出码、超时期限进程组终止、错误注入与磁盘内容保留检查；[PR #57](https://github.com/liuzhouquan/selfforge-agent/pull/57) | `57117238f34c9082b5ce3b3b9e6b1931f2b0549d`；本地与 CI [Run #43150979908](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43150979908) passed（573 passed, 13 deselected） |
| E4 | SF-DEV-01-A4 | `tests/test_eval_source_preparation.py::test_ci_workflow_uses_shared_prepare_eval_sources_script`；`.github/workflows/ci.yml`（`Lint` / `Unit tests` / `Build sandbox image` / `Provision pinned multi-file candidate sources` / `Docker integration tests`） | 本地 Ruff + 非集成全量 + CI 真实 Docker 集成测试（`uv run --no-sync pytest -q -m integration`）；本机 WSL2 未安装 `docker`，Docker 集成由 CI 完成；[PR #57](https://github.com/liuzhouquan/selfforge-agent/pull/57) | `57117238f34c9082b5ce3b3b9e6b1931f2b0549d`；CI [Run #43150979908](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43150979908) 全绿：Lint passed、Unit tests `573 passed, 13 deselected`、Docker integration tests `13 passed, 573 deselected`（含 jmespath 与 boltons 候选控制） |
| E5 | SF-DEV-01-A1 | `tests/test_eval_source_preparation.py::test_online_preparation_creates_cloneable_repos_and_ownership_marker` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E6 | SF-DEV-01-A2 | `tests/test_eval_source_preparation.py::test_offline_mode_succeeds_without_fetch_and_preserves_user_head_and_files` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E7 | SF-DEV-01-A3 | `tests/test_eval_source_preparation.py::test_cli_rejects_fifo_state_without_blocking`、`tests/test_eval_source_preparation.py::test_preparation_does_not_execute_checkout_filters` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E8 | SF-DEV-01-A4 | `tests/test_eval_source_preparation.py::test_ci_workflow_uses_shared_prepare_eval_sources_script` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |

执行记录（Owner/状态以上表为准）：Base=`e07153e092150ab2d1f86aa98545a676836b6e14`；Branch=`feat/sf-dev-01`；Worktree=`selfforge-sf-dev-01`；PR=[#57](https://github.com/liuzhouquan/selfforge-agent/pull/57)。先核对两项固定 SHA 与 `--no-local` clone 行为并先红后绿实现共享脚本；首次推送 `8d91cc5a848b50d9296c6494d1e3c10824f0d98f` 在 CI `Run #43150067989` 因冗余 shebang 触发 Ruff `EXE001` 失败，移除后在源码提交 `57117238f34c9082b5ce3b3b9e6b1931f2b0549d` 通过本地回归与 CI `Run #43150979908` 全套验证（仅保留一份共享实现与一份准备文档）。

E5–E8 为本轮组合复验；E1–E4 保留为原分支历史结果，不充当当前源码的执行证明。

审查整合记录：Base=`e07153e092150ab2d1f86aa98545a676836b6e14`；Branch=`review/pr57-59`；PR=#57；Merge=`fa387bc4e427d9a682a00932802a7e4c8920bee7`。用户已授权修改与合并；新增回归保留原缺陷的实际失败结果，修复后完整组合验证通过；后续状态提交仅修改文档。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：e07153e092150ab2d1f86aa98545a676836b6e14 / review/pr57-59 (worktree: selfforge-review-pr57-59) / 已验证源码 9bc111cb07cb3b6b6dd8e44a7483c49e34960220；本次状态提交仅文档
已完成验收与证据 ID：SF-DEV-01-A1..A4（E5、E6、E7、E8）；历史 E1–E4 保留
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；本次状态与审查记录归 Codex；本机真 Docker 已完成完整组合验证；不调用模型
PR / 准确 head CI / merge SHA（有则填）：PR #57 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813（组合源码 head；各源 PR head CI 见审查记录） / fa387bc4e427d9a682a00932802a7e4c8920bee7
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 的准确运行及结论记录到 PR #59 交付描述
下一步：核对最终 main CI；SF-EVAL-08 仍需先冻结策略，未满足 Ready 前提。
```

### SF-DOC-01

**目标：** 将本文件的状态和验收约定变成可执行门禁，而非靠审查者记忆。

**范围/入口：** 新建标准库脚本 `scripts/check_sdd.py --root PATH`（默认仓库根），只检查本文件明确规定的表格/锚点/证据格式和本地引用；新建 `tests/test_sdd_contract.py`，在既有 CI 调用。限制文档读取总量 4 MiB；不执行文档命令、不请求 GitHub、不调用模型、不做通用 Markdown 平台。扫描仓库内路径，不跟随越界链接。

**最低规则：** 队列 ID 唯一；状态属于本节枚举；每个排队任务有同名规格节；验收 ID 唯一；In Progress/In Review 有 Owner 与实际 base；Merged 有 PR/完整 merge SHA；声明通过的验收有真实 `file.py::test_name`（参数化基名可用）、命令、验证 SHA 与结果/CI 引用。文件和测试节点存在仅表示可追溯，**不能证明测试执行或语义充分**。`待实现/未验证` 可留在 Ready/Waiting，不能当作通过证据；历史快照不按实时任务误报。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-DOC-01-A1 | 当前合法 SDD 通过；重复 ID、坏状态、缺规格/验收引用均非零 | `tests/test_sdd_contract.py` 临时规格夹具，逐项反例 | E5（本轮组合复验） |
| SF-DOC-01-A2 | 标为通过却引用不存在测试/缺验证 SHA，或 Merged 缺 PR/SHA，被拒绝 | 同文件错误夹具；不能为通过而弱化规则 | E6（本轮组合复验） |
| SF-DOC-01-A3 | 有界读取、越界/symlink 输入失败；不运行任何文档命令 | 同文件权限/限额/副作用回归 | E7（本轮组合复验） |
| SF-DOC-01-A4 | CI 实际运行该检查；文档明确其只检查一致性、不证明测试语义 | 实际 CI 与输出摘要 | E8（本轮组合复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-DOC-01-A1 | `tests/test_sdd_contract.py::test_current_repo_sdd_passes_and_states_consistency_only_scope`、`tests/test_sdd_contract.py::test_valid_sdd_across_statuses_tables_and_historical_snapshot_passes`、`tests/test_sdd_contract.py::test_rejects_queue_status_spec_and_link_violations`、`tests/test_sdd_contract.py::test_rejects_acceptance_id_or_table_violations`；入口 `scripts/check_sdd.py` | 本地 Linux `uv run python scripts/check_sdd.py` 与 `uv run pytest -vv tests/test_sdd_contract.py`；GitHub Actions `ubuntu-latest` `uv run --no-sync python scripts/check_sdd.py` 与 `uv run --no-sync pytest -q -m "not integration"` | 验证源码 SHA `a0739c26ca2d3fcc01dd44e9c15632b706a80235`，passed（本地 `check_sdd.py` 通过且 `tests/test_sdd_contract.py` 39 passed，全量非集成 `594 passed, 13 deselected`；CI [Run #43157245377](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43157245377) `SDD contract check` 通过、`Unit tests` `594 passed, 13 deselected`、`Docker integration tests` `13 passed, 594 deselected`） |
| E2 | SF-DOC-01-A2 | `tests/test_sdd_contract.py::test_rejects_status_metadata_and_merge_requirements`、`tests/test_sdd_contract.py::test_rejects_fake_or_incomplete_acceptance_evidence`；入口 `scripts/check_sdd.py` | 本地 Linux `uv run pytest -vv tests/test_sdd_contract.py` 与 `uv run ruff check .`；GitHub Actions `ubuntu-latest` `uv run --no-sync ruff check .`、`uv run --no-sync python scripts/check_sdd.py`、`uv run --no-sync pytest -q -m "not integration"` | 验证源码 SHA `a0739c26ca2d3fcc01dd44e9c15632b706a80235`，passed（状态/owner/base SHA/Merged 与虚假验收反例均准确拒绝并退出码 1，`ruff check .` `All checks passed!`，`39 passed` 且 CI [Run #43157245377](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43157245377) 全绿通过） |
| E3 | SF-DOC-01-A3 | `tests/test_sdd_contract.py::test_rejects_read_budget_exceeded_over_4mib`、`tests/test_sdd_contract.py::test_rejects_symlinks_non_regular_files_and_out_of_bounds_paths`、`tests/test_sdd_contract.py::test_does_not_execute_documented_commands_or_import_exec_network`；入口 `scripts/check_sdd.py` | 本地 Linux `uv run pytest -vv tests/test_sdd_contract.py`；GitHub Actions `ubuntu-latest` `uv run --no-sync pytest -q -m "not integration"` 与 `uv run --no-sync pytest -q -m integration` | 验证源码 SHA `a0739c26ca2d3fcc01dd44e9c15632b706a80235`，passed（4 MiB 读限额、symlink 与越界拒绝、零执行/零 exec 与网络模块导入全部 passed，CI [Run #43157245377](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43157245377) 594 单元测试 + 13 Docker 集成测试全部通过） |
| E4 | SF-DOC-01-A4 | `tests/test_sdd_contract.py::test_ci_workflow_and_docs_declare_sdd_consistency_scope`、`tests/test_sdd_contract.py::test_current_repo_sdd_passes_and_states_consistency_only_scope`；入口 `.github/workflows/ci.yml` 与 `docs/development.md` | GitHub Actions `ubuntu-latest` 实际工作流 `.github/workflows/ci.yml`（步骤 `SDD contract check`: `uv run --no-sync python scripts/check_sdd.py`）与本地 `uv run pytest -vv tests/test_sdd_contract.py` | 验证源码 SHA `a0739c26ca2d3fcc01dd44e9c15632b706a80235`，passed（[Run #43157245377](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43157245377) 实测输出 `OK: verified 4 SDD task(s) and 15 acceptance item(s)` 且明确仅校结构、状态与本地引用一致性，不证明测试执行或语义充分） |
| E5 | SF-DOC-01-A1 | `tests/test_sdd_contract.py::test_valid_sdd_across_statuses_tables_and_historical_snapshot_passes` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E6 | SF-DOC-01-A2 | `tests/test_sdd_contract.py::test_cli_rejects_uncollectable_test_references` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E7 | SF-DOC-01-A3 | `tests/test_sdd_contract.py::test_rejects_symlinks_non_regular_files_and_out_of_bounds_paths` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E8 | SF-DOC-01-A4 | `tests/test_sdd_contract.py::test_ci_workflow_and_docs_declare_sdd_consistency_scope` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |

执行记录（Owner/状态以上表为准）：Base=`e07153e092150ab2d1f86aa98545a676836b6e14`；Branch=`feat/sf-doc-01`；Worktree=`selfforge-sf-doc-01`；PR=[#58](https://github.com/liuzhouquan/selfforge-agent/pull/58)。源码、测试与 CI 门禁提交 `a0739c26ca2d3fcc01dd44e9c15632b706a80235` 已通过本地检查与 GitHub Actions [Run #43157245377](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43157245377)（`Lint`、`SDD contract check`、`Unit tests` 594 passed / 13 deselected、`Docker integration tests` 13 passed / 594 deselected）。

E5–E8 为本轮组合复验；E1–E4 保留为原分支历史结果，不充当当前源码的执行证明。

审查整合记录：Base=`e07153e092150ab2d1f86aa98545a676836b6e14`；Branch=`review/pr57-59`；PR=#58；Merge=`06787d8a04cfa8d3b38c16e2b49ce7071f12c129`。用户已授权修改与合并；新增回归保留原缺陷的实际失败结果，修复后完整组合验证通过；后续状态提交仅修改文档。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：e07153e092150ab2d1f86aa98545a676836b6e14 / review/pr57-59 (worktree: selfforge-review-pr57-59) / 已验证源码 9bc111cb07cb3b6b6dd8e44a7483c49e34960220；本次状态提交仅文档
已完成验收与证据 ID：SF-DOC-01-A1..A4（E5、E6、E7、E8）；历史 E1–E4 保留
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；本次状态与审查记录归 Codex；本机真 Docker 已完成完整组合验证；不调用模型
PR / 准确 head CI / merge SHA（有则填）：PR #58 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813（组合源码 head；各源 PR head CI 见审查记录） / 06787d8a04cfa8d3b38c16e2b49ce7071f12c129
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 的准确运行及结论记录到 PR #59 交付描述
下一步：核对最终 main CI；SF-EVAL-08 仍需先冻结策略，未满足 Ready 前提。
```

### SF-TEST-01

**目标：** 为通用 Coding 修复验证一个显式提供的 pytest 回归用例，得到“原代码断言失败、修复后通过”的独立证据。它不同于现有 PR 风险流程的“Base 通过、Head 失败”，不能反转/破坏原流程。

**公开契约：** CLI `test-regression --repo PATH --session ID --test-file PATH --timeout SECONDS`；API `POST /api/code/workspaces/{session_id}/validate-regression`，JSON 为 `repository`、`test_file`、`timeout`（默认 60，范围 1–180，总预算）。二者共享一个服务，返回 `status`（`confirmed/inconclusive/error`）、`base_sha`、`workspace_sha256`、`test_sha256`、`base_result`、`repaired_result`、固定分类 `reason`。CLI 对应退出码 0/1/2。JSON 不回显测试源码/私有错误正文；该 API 仍仅本机可用。

**范围/入口：** 从 `CodingWorkspaceManager` 取得持久工作区及初始 base，持共享锁；显式读取一个有界、非 symlink 的 Python 候选文件（最多 8 KiB），保留 protected-path 边界。在两份独立快照运行相同测试：原 committed base 与当前修复内容。复用 `coding/test_generation.py`、`coding/worktree.py`、`runtime/fingerprint.py`、`verification/runner.py` 和既有 Docker staging；不得新建 Runner/Agent。只用当前会话绑定镜像，默认 Docker，不新增 Host 分支。

**证据：** 仅 base 非空运行且有断言失败、无收集错误，修复副本非空 JUnit 全通过，且快照/当前工作区指纹一致时 `confirmed`。空测试、全 skip、收集失败、超时、内容变化均不能确认。候选只进入临时副本，不落入用户 worktree；结果单独记录，不自动授予 Runtime `completed` 或 commit/PR 资格。保持旧证据/状态不被此只读验证错误覆盖。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-TEST-01-A1 | 临时真实 Git bug + 未提交修复 + 同一回归测试，旧断言失败/新非空通过，confirmed | `tests/test_coding_regression_validation.py` + Docker 集成；CLI/API 响应均断言字段 | E5（本轮组合复验） |
| SF-TEST-01-A2 | 旧也通过、新失败、空/all-skip/收集错误均不可确认；超时明确 error | 同文件独立反例，保留 Runner 真实状态 | E6（本轮组合复验） |
| SF-TEST-01-A3 | 忙碌工作区、越界/受保护/symlink/超限候选、镜像缺失或会话不匹配被拒绝 | CLI 非零/API 4xx；发生在模型/外部测试执行前 | E7（本轮组合复验） |
| SF-TEST-01-A4 | 两份测试互不污染；执行期间内容变化撤销确认；结束/失败后临时资源释放且用户文件不变 | 故障注入/指纹/磁盘对比；现有 Review/Repair 回归 | E8（本轮组合复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-TEST-01-A1 | `tests/test_coding_regression_validation.py::test_confirmed_regression_via_service_cli_and_api`、`tests/test_coding_regression_validation.py::test_confirmed_regression_from_workspace_candidate_and_exact_size_limit`、`tests/test_coding_regression_validation.py::test_real_docker_regression_validation_confirmed_and_counterexample` | 本地 `pytest -v -m "not integration"`（临时 Git worktree + `TestRunner`）及真实 CLI/API；GitHub CI `uv run --no-sync pytest -q -m integration`（`selfforge-agent-sandbox:latest` 真 Docker `--network=none`） | `44f7382ef005a1ac9610f344a640e9c104cf258c`；passed；CI [R1 43173574675](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43173574675) 集成 `14 passed, 570 deselected` |
| E2 | SF-TEST-01-A2 | `tests/test_coding_regression_validation.py::test_non_confirming_outcomes_preserve_runner_status_and_exit_codes`、`tests/test_coding_regression_validation.py::test_base_import_error_only_cannot_confirm`、`tests/test_coding_regression_validation.py::test_timeout_yields_error_status_and_cli_exit_2` | 本地 `pytest -v -m "not integration" tests/test_coding_regression_validation.py`（9 组参数化反例 + 仅 base import error + 真实超时）；GitHub CI `Unit tests` | `44f7382ef005a1ac9610f344a640e9c104cf258c`；15 passed（超时 `status="error"`、`reason="timeout"`、CLI exit `2`）；CI [R1 43173574675](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43173574675) `570 passed, 14 deselected` |
| E3 | SF-TEST-01-A3 | `tests/test_coding_regression_validation.py::test_preflight_rejections_before_runner_execution` | 本地 `pytest` + CLI `selfforge-agent test-regression`（exit `2`）+ FastAPI `TestClient`（400/403/404/409/422），断言 Runner `calls == []` | `44f7382ef005a1ac9610f344a640e9c104cf258c`；passed；CI [R1 43173574675](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43173574675) `passed` |
| E4 | SF-TEST-01-A4 | `tests/test_coding_regression_validation.py::test_isolation_tampering_cleanup_and_session_evidence_preservation`、`tests/test_test_generation.py`、`tests/test_repair_agent.py`、`tests/test_workspace_concurrency.py`、`tests/test_coding_web.py`、`tests/test_session_environment.py` | 本地 `ruff check .`、`pytest -v -m "not integration"`（定向 6 套件 `79 passed, 5 deselected`）及全量 `pytest -q -m "not integration"`（隔离/篡改撤销/worktree 释放/原会话证据零覆盖） | `44f7382ef005a1ac9610f344a640e9c104cf258c`；Ruff 全绿；全量非集成 `570 passed, 14 deselected`；CI [R1 43173574675](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43173574675) `570 passed, 14 deselected` |
| E5 | SF-TEST-01-A1 | `tests/test_coding_regression_validation.py::test_real_docker_regression_validation_confirmed_and_counterexample` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E6 | SF-TEST-01-A2 | `tests/test_coding_regression_validation.py::test_runtime_exception_is_not_assertion_regression` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E7 | SF-TEST-01-A3 | `tests/test_coding_regression_validation.py::test_cli_workspace_git_preflight_obeys_total_budget` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E8 | SF-TEST-01-A4 | `tests/test_coding_regression_validation.py::test_unreadable_final_fingerprint_cannot_confirm`、`tests/test_coding_regression_validation.py::test_final_fingerprint_uses_validation_deadline`、`tests/test_coding_regression_validation.py::test_isolation_tampering_cleanup_and_session_evidence_preservation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；本机 Linux 真 Docker + CLI/API；Git 替身/故障注入只证明相应边界 | `9bc111cb07cb3b6b6dd8e44a7483c49e34960220`；passed；完整组合 653 passed、无跳过；[组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813) 639 单元 + 14 Docker 全部通过；详见[审查记录](./review-pr57-59.md) |
| E9 | SF-TEST-01-A3 | `tests/test_coding_regression_validation.py::test_api_sanitizes_unclassified_validation_errors` | `uv run --no-sync pytest -q tests/test_coding_regression_validation.py::test_api_sanitizes_unclassified_validation_errors`；FastAPI TestClient，注入 OSError/ValueError | `3612563fb8876917670c45ee238128454d4c5ae1`；2 passed；修复前的实际结果见审查记录；完整组合与准确 head CI 的最终结果记录到 [PR #59 交付](https://github.com/liuzhouquan/selfforge-agent/pull/59) |

执行记录（Owner/状态以上表为准）：Base=`e07153e092150ab2d1f86aa98545a676836b6e14`；Branch=`feat/sf-test-01`；PR=`#59` (`https://github.com/liuzhouquan/selfforge-agent/pull/59`)；Worktree=`selfforge-sf-test-01`；Source=`44f7382ef005a1ac9610f344a640e9c104cf258c`。共享服务 `src/selfforge_agent/coding/regression_validation.py` 已接入 CLI `selfforge-agent test-regression` 与 API `POST /api/code/workspaces/{session_id}/validate-regression`，严格绑定 Docker 会话镜像与临时隔离快照；不在本任务增加测试自动生成模型循环、覆盖率平台或新前端。UI 接入、自动候选生成另立规格；保留现有 Web Coding 主入口。

E5–E8 为本轮组合复验；E1–E4 保留为原分支历史结果，不充当当前源码的执行证明。

审查整合记录：Base=`e07153e092150ab2d1f86aa98545a676836b6e14`；Branch=`review/pr57-59`；PR=#59；Merge=`b3ad75dfdfedefcd3218f790ae26f04b086b9fc1`。用户已授权修改与合并；新增回归保留原缺陷的实际失败结果，修复后完整组合验证通过；后续状态提交仅修改文档。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：e07153e092150ab2d1f86aa98545a676836b6e14 / review/pr57-59 (worktree: selfforge-review-pr57-59) / API 脱敏源码 3612563fb8876917670c45ee238128454d4c5ae1；此前完整组合源码 9bc111cb07cb3b6b6dd8e44a7483c49e34960220；当前补充仅文档
已完成验收与证据 ID：SF-TEST-01-A1..A4（E5、E6、E7、E8），API 脱敏补充 E9；历史 E1–E4 保留
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；本次状态与审查记录归 Codex；本机真 Docker 已完成完整组合验证；不调用模型
PR / 准确 head CI / merge SHA（有则填）：PR #59 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43189888813（组合源码 head；各源 PR head CI 见审查记录） / b3ad75dfdfedefcd3218f790ae26f04b086b9fc1
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 的准确运行及结论记录到 PR #59 交付描述
下一步：核对最终 main CI；SF-EVAL-08 仍需先冻结策略，未满足 Ready 前提。
```

### SF-EVAL-08

**目标：** 为新 Runtime 准备下一轮可复现评测；本任务只冻结新规格、来源和无模型参考资格，不运行整套付费模型测量。

前提：SF-DEV-01 合并；已与用户确认并冻结新策略：
- **任务集**：22 题扩展集 v3（保留原 v2 20 题 + `jmespath_unknown_function_suggestions` + `boltons_remap_mappings`，共 5 个公开仓库），历史 `suite-twenty-tasks.json` / `suite-twenty-tasks-v2.json` / `suite-twenty-tasks-v2.assets.json` 字节与 `53/60` 成绩完全不变。
- **镜像策略**：默认 20 题使用当前 `docker/Dockerfile` 构建的 `selfforge-agent-sandbox:latest`，2 题 `itsdangerous` 使用锁定 `docker/profiles/itsdangerous.lock` 离线构建的 `selfforge-itsdangerous:locked`；预检与后续运行统一解析并记录实际不可变 `sha256:...` 镜像 ID，不复用历史 `abad14d3...` / `205fa9f6...` 宣称同镜像。
- **供应商 / 模型 / 回退**：`deepseek`、`deepseek-flash`、`https://api.deepseek.com`、`--no-fallback`（与 v2 主口径一致）；正式运行前必须显式核对生效配置。
- **预算与重复**：3 次重复（22 × 3 = 66 次独立运行）、`--max-steps 18`、`--time-budget 180`、`--test-timeout 30`、`8000` 启发式上下文 token，并明确列后续真实运行的调用/时间/费用上限与全部分类口径（`runtime/benchmark.py` 全部失败原因及脱敏 `agent_error_code`），未执行前不填任何模型成绩。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-EVAL-08-A1 | 新策略有明确任务/允许路径/预算/重复/失败口径与资产哈希；与历史可区分 | 新 manifest/资产索引结构回归 + 旧资产哈希保持 | E4（本轮复验） |
| SF-EVAL-08-A2 | 所选任务都通过独立 Docker 参考正负控，报告来源明确且无模型构造 | 既有 `eval-validate`；记录实际镜像、源 SHA、报告哈希 | E5（本轮复验） |
| SF-EVAL-08-A3 | 后续真实模型运行单独列执行计划、有效供应商及调用/时间/费用上限；没有结果不填成绩 | 策略审查；本任务不得写成已完成模型评测 | E6（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-EVAL-08-A1 | `tests/test_eval_v3_policy.py::test_v3_policy_bundle_integrity_and_historical_v2_preservation`、`tests/test_eval_v3_policy.py::test_v3_policy_normalization_and_fail_closed_rejections` | `pytest -q -m "not integration"`（校验 v3 22 题清单 `b8351e62...`、资产索引 `21a11779...`、策略 `c7f20757...` 及历史 v1/v2 哈希不变；覆盖 symlink/篡改/残缺分类反例） | `ada36e2824b89dc1ff73900af5210163966561c1`；645 passed, 15 deselected；CI [R1 43200650684](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43200650684) `Unit tests` 645 passed |
| E2 | SF-EVAL-08-A2 | `tests/test_eval_v3_policy.py::test_v3_suite_docker_reference_validation`、`tests/test_eval_v3_policy.py::test_eval_validate_v3_model_free_cli_and_source_shas`、`tests/test_eval_v3_policy.py::test_prepare_eval_sources_v3_profile_covers_all_five_repos` | 本地 Host 真实 `selfforge-agent eval-validate` CLI（禁用 provider/HTTP 构造）+ GitHub Actions `ubuntu-latest` 真 Docker (`--network=none`) 对 22 题运行 `validate_suite`，解析镜像 `selfforge-agent-sandbox:latest` (`sha256:db4ad5110e4edb4dae4ad7e7771b4c3707cf64dc2a23f15f4e1f23ec2335c115`) 与 `selfforge-itsdangerous:locked` (`sha256:bcbb09a24c9b6aadd8536fe4ff46e040037205e22052a417c5e283f826fab2b1`)，报告 SHA-256 `f68e74054a1412e06bdf1a9508fc1ec83f18e00dd310f5a538ece9ad89c8737b` | `ada36e2824b89dc1ff73900af5210163966561c1`；passed；CI [R1 43200650684](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43200650684) `Docker integration tests` 15 passed, 645 deselected |
| E3 | SF-EVAL-08-A3 | `tests/test_eval_v3_policy.py::test_v3_policy_bundle_integrity_and_historical_v2_preservation`、[v3 规格文档](../benchmark/validated-candidates/twenty-two-tasks-v3.md) | 审查策略与约束门禁：`deepseek` / `deepseek-flash` / `--no-fallback`、`repeats=3`（66 次尝试）、上限 1188 次调用 / 90 分钟 / 25.00 USD、`model_benchmark_executed=false`、`success_rate=null`，未执行付费模型 | `ada36e2824b89dc1ff73900af5210163966561c1`；passed；CI [R1 43200650684](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43200650684) `passed` |
| E4 | SF-EVAL-08-A1 | `tests/test_eval_v3_policy.py::test_v3_policy_bundle_integrity_and_historical_v2_preservation`、`tests/test_eval_v3_policy.py::test_policy_rejects_symlink_ancestor_before_resolution`、`tests/test_eval_v3_policy.py::test_policy_rejects_infinite_budgets`、`tests/test_eval_v3_policy.py::test_policy_checks_image_profile_lockfile_digest`、`tests/test_eval_v3_policy.py::test_policy_rejects_inconsistent_executed_scores` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；Linux 真 Docker；22 题无模型参考正负控；无付费模型调用 | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E5 | SF-EVAL-08-A2 | `tests/test_eval_v3_policy.py::test_v3_suite_docker_reference_validation`、`tests/test_eval_v3_policy.py::test_prepare_eval_sources_v3_profile_covers_all_five_repos` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；Linux 真 Docker；22 题无模型参考正负控；无付费模型调用 | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E6 | SF-EVAL-08-A3 | `tests/test_eval_v3_policy.py::test_v3_policy_bundle_integrity_and_historical_v2_preservation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；Linux 真 Docker；22 题无模型参考正负控；无付费模型调用 | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |

执行记录（Owner/状态以上表为准）：Base=`19882cc1cf7d6c29309e677c1449b5154c6f8e1c`；Branch=`feat/sf-eval-08`；PR=`#61` (`https://github.com/liuzhouquan/selfforge-agent/pull/61`)；Worktree=`selfforge-sf-eval-08`；Source=`ada36e2824b89dc1ff73900af5210163966561c1`。已交付 22 题 v3 清单 `suite-twenty-two-tasks-v3.json`（`b8351e62...`）、资产索引 `suite-twenty-two-tasks-v3.assets.json`（`21a11779...`）、机器校验策略 `suite-twenty-two-tasks-v3.policy.json`（`c7f20757...`）、规格说明 `twenty-two-tasks-v3.md`、`src/selfforge_agent/runtime/eval_policy.py` 与 `scripts/prepare_eval_sources.py --profile v3`；未运行付费整套模型评测。

本轮记录 E4–E6 取代当前验收引用；原 E1–E3 是历史来源，不把旧计数相加。

审查整合记录：Base=`19882cc1cf7d6c29309e677c1449b5154c6f8e1c`；Branch=`review/pr61-63`；PR=#61；Merge=`9db8fc42bf60d25c4826cee1b918de3c6a4aa845`；Source=`f88c2b95355869834e9c4f8bb406dfc6f97baea9`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：19882cc1cf7d6c29309e677c1449b5154c6f8e1c / review/pr61-63 (worktree: selfforge-review-pr61-63) / 已验证源码 f88c2b95355869834e9c4f8bb406dfc6f97baea9；后续对账仅文档
已完成验收与证据 ID：SF-EVAL-08-A1..A3（E4–E6）
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；本次文档对账归 Codex；完整组合含真 Docker 无跳过；UI 浏览器 smoke 使用 API 替身，不能冒充真实端到端模型运行
PR / 准确 head CI / merge SHA（有则填）：PR #61 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227（组合 head；各源 PR head CI 见审查记录） / 9db8fc42bf60d25c4826cee1b918de3c6a4aa845
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 的准确结果回填 PR #63 交付描述
下一步：后续真实模型测量需另行明确执行任务并遵守 v3 冻结策略与预算，当前未运行该测量。
```

### SF-TEST-02

**目标：** 将 `SF-TEST-01` 已合入的独立回归验证能力（`POST /api/code/workspaces/{session_id}/validate-regression`）接入 Web Coding 工作台 UI（`src/selfforge_agent/api/static/index.html`、`src/selfforge_agent/api/static/app.js`、`src/selfforge_agent/api/static/style.css`），使用户在 Docker 隔离工作区完成或暂停编程任务后，可直接指定候选测试文件与有界超时，查看 Base 与当前工作区的独立运行对照结果及 SHA 指纹，同时严格遵守只读展示、失败闭合与状态同步边界。

**范围/入口：** 仅在 `#coding-job` 内增加回归验证面板（不改动 PR 审查/修复/TestGen 主流程，不新建自动化测试生成循环，不修改后端 `validate-regression` 的安全校验、脱敏与只读边界）。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-TEST-02-A1 | 当 Coding 会话绑定在 Docker 独立工作区（`branch` 存在、`sandbox === "docker"`）且非运行态时，提交 `test_file` 与 `timeout`（默认 60，范围 1–180）调用 `POST /api/code/workspaces/{session_id}/validate-regression`，正确渲染结构化 `RegressionValidationReport`（`status`：`confirmed`/`inconclusive`/`error`、`reason`、`base_sha`、`workspace_sha256`、`test_sha256` 与 `base_result`/`repaired_result` 明细），且不篡改 `#metric-verification` | Web UI/API 回归测试（Node DOM 运行时行为验证 + FastAPI TestClient 序列化验证） | E5（本轮复验） |
| SF-TEST-02-A2 | 本机执行（`sandbox === "host"`）、直接修改原仓库或已清理工作区（`!branch`）、未记录隔离环境、任务运行中（`codingJobRunning`）、空 `test_file` 或越界 `timeout`（<1 或 >180 或非整数）均被严格拦截或禁用并给出清晰原因；后端 HTTP 400/404/409/422 错误展示清晰且不破坏既有 Diff 与交付表单 | 前端状态门槛测试 + HTTP 错误路径与不变量断言 | E6（本轮复验） |
| SF-TEST-02-A3 | 切换历史任务、发起新任务、恢复会话或清理工作区时自动重置上一会话的回归结果、错误与提交态；全部服务端字符串经 `textContent` / DOM API 安全渲染，禁止 `innerHTML` 拼接 | 会话切换/清理/XSS 转义自动化断言 | E7（本轮复验） |
| SF-TEST-02-A4 | JS 语法检查、Web 回归测试、Ruff、SDD 一致性检查与全量非集成测试通过；本地若无可用浏览器则明确记录验证层级边界 | `node --check` + `pytest` + `ruff check .` + `check_sdd.py` + CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-TEST-02-A1 | `tests/test_coding_web_regression_ui.py::test_coding_workbench_html_contains_regression_panel_inside_coding_job`、`tests/test_coding_web_regression_ui.py::test_web_ui_submits_regression_validation_and_renders_report_safely`、`tests/test_coding_web_regression_ui.py::test_web_ui_roundtrips_fastapi_validate_regression_endpoint` | `pytest -q tests/test_coding_web_regression_ui.py`；FastAPI `TestClient` 真实 HTML/资产与 `RegressionValidationReport` 序列化 + Node VM 执行实际 `app.js` 与解析自 `index.html` 的 DOM 清单；[PR #62](https://github.com/liuzhouquan/selfforge-agent/pull/62) | 源码 SHA `f4e392239b81ecfe5388528de6b48dd275fe94b5`（含 `inconclusive` 状态对齐修正）；本地 6 passed；CI [Run #43214170049](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43214170049) success |
| E2 | SF-TEST-02-A2 | `tests/test_coding_web_regression_ui.py::test_web_ui_enforces_sandbox_worktree_timeout_and_error_guards`、`tests/test_coding_web_regression_ui.py::test_web_ui_revert_invalidates_stale_report_and_unrecorded_sandbox_disables_submit` | 同文件 Node VM 执行实际 `app.js` 状态门槛、`validate-regression` 请求拦截与 HTTP 409 错误处理检查；既有 API/CLI 验证节点 `tests/test_coding_regression_validation.py` 保持通过 | `f4e392239b81ecfe5388528de6b48dd275fe94b5`；本地 6 passed（`test_coding_web_regression_ui.py`）；CI [Run #43214170049](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43214170049) success |
| E3 | SF-TEST-02-A3 | `tests/test_coding_web_regression_ui.py::test_web_ui_resets_regression_state_on_job_switch_and_workspace_removal`、`tests/test_coding_web_regression_ui.py::test_web_ui_revert_invalidates_stale_report_and_unrecorded_sandbox_disables_submit` | Node VM 严格禁止 `innerHTML` 读写（`innerHTML` 写入即抛错）并验证 `inconclusive` 中文渲染、会话切换、工作区还原、工作区清理的不变量 | `f4e392239b81ecfe5388528de6b48dd275fe94b5`；本地 6 passed；CI [Run #43214170049](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43214170049) success |
| E4 | SF-TEST-02-A4 | `tests/test_coding_web_regression_ui.py::test_coding_workbench_html_contains_regression_panel_inside_coding_job`、`tests/test_coding_web_regression_ui.py::test_web_ui_roundtrips_fastapi_validate_regression_endpoint`；`node --check src/selfforge_agent/api/static/app.js`、`ruff check .`、`python scripts/check_sdd.py` 与 GitHub Actions CI | 本地 Host + Node v24.18.0；本 WSL2 环境未安装 Chromium/Playwright，未执行桌面真实浏览器点击（以真实 DOM 清单 + Node VM + TestClient 替代并如实标注）；CI `ubuntu-latest` 运行完整单元与 Docker 集成 | 基线 `87997ed` 本地全量 `647 passed, 14 deselected` + CI [Run #43209267239](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43209267239) 全绿；修复提交 `f4e392239b81ecfe5388528de6b48dd275fe94b5` 本地 Ruff/Node/SDD/专项 6 passed + CI [Run #43214170049](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43214170049) success |
| E5 | SF-TEST-02-A1 | `tests/test_coding_web_regression_ui.py::test_web_ui_submits_regression_validation_and_renders_report_safely` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；FastAPI TestClient + Node VM；另有真实 Chromium 桌面/390px smoke（API 替身） | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E6 | SF-TEST-02-A2 | `tests/test_coding_web_regression_ui.py::test_web_ui_enforces_sandbox_worktree_timeout_and_error_guards` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；FastAPI TestClient + Node VM；另有真实 Chromium 桌面/390px smoke（API 替身） | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E7 | SF-TEST-02-A3 | `tests/test_coding_web_regression_ui.py::test_regression_responses_cannot_survive_invalidation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；FastAPI TestClient + Node VM；另有真实 Chromium 桌面/390px smoke（API 替身） | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E8 | SF-TEST-02-A4 | `tests/test_coding_web_regression_ui.py::test_web_ui_roundtrips_fastapi_validate_regression_endpoint` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；FastAPI TestClient + Node VM；另有真实 Chromium 桌面/390px smoke（API 替身） | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |

执行记录（Owner/状态以上表为准）：Base=`19882cc1cf7d6c29309e677c1449b5154c6f8e1c`；Branch=`feat/sf-test-02`；Worktree=`selfforge-sf-test-02`；PR=[#62](https://github.com/liuzhouquan/selfforge-agent/pull/62)。先运行 `tests/test_coding_web_regression_ui.py` 确认 TDD 红灯（4 failed），随后完成 Web Coding UI 面板与状态约束（`87997ed`，CI `Run #43209267239` 全绿），并补充修正 `inconclusive` 状态键对齐（`f4e3922`，CI `Run #43214170049` success）；本状态提交仅修改文档。

本轮记录 E5–E8 取代当前验收引用；原 E1–E4 是历史来源，不把旧计数相加。

审查整合记录：Base=`19882cc1cf7d6c29309e677c1449b5154c6f8e1c`；Branch=`review/pr61-63`；PR=#62；Merge=`22d7b4dc3c110b7a21016f83ace184143707ce13`；Source=`f88c2b95355869834e9c4f8bb406dfc6f97baea9`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：19882cc1cf7d6c29309e677c1449b5154c6f8e1c / review/pr61-63 (worktree: selfforge-review-pr61-63) / 已验证源码 f88c2b95355869834e9c4f8bb406dfc6f97baea9；后续对账仅文档
已完成验收与证据 ID：SF-TEST-02-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；本次文档对账归 Codex；完整组合含真 Docker 无跳过；UI 浏览器 smoke 使用 API 替身，不能冒充真实端到端模型运行
PR / 准确 head CI / merge SHA（有则填）：PR #62 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227（组合 head；各源 PR head CI 见审查记录） / 22d7b4dc3c110b7a21016f83ace184143707ce13
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 的准确结果回填 PR #63 交付描述
下一步：后续真实模型测量需另行明确执行任务并遵守 v3 冻结策略与预算，当前未运行该测量。
```

### SF-WS-05

**目标：** 修复 `GET /api/workspaces/storage`（及 `/api/code/workspaces/storage`）忽略模块级 `_CODING_WORKSPACES` 注入而与 `/audit`/`/prune` 视图脱节的问题，并补齐 CLI `trace` 与 `test-regression` 的安全结构化导出能力（`--json` / `--output`，共用 fail-closed 写盘检查）。

**范围/入口：** 仅改动 `src/selfforge_agent/api/app.py`（`_storage_workspace_manager()` 与 `audit_workspaces_storage`）、`src/selfforge_agent/cli.py`（`_write_cli_output`、`trace`、`test-regression`、`workspaces storage` `--output`）及专项测试 `tests/test_ws_cli_export_and_storage_parity.py`；不改动 `benchmark/`、`api/static/` 或 `validate-regression` 安全脱敏规则。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-WS-05-A1 | 当服务端覆写模块级 `_CODING_WORKSPACES` 时，`GET /api/workspaces/storage` 与 `/api/code/workspaces/storage` 均复用该管理器与 `/audit`、`/prune` 保持一致；同时保留 `SELFFORGE_WORKSPACES_ROOT` 运行时环境变量覆写与非法 `quota_bytes` / 仓库路径 HTTP 400 拒绝 | FastAPI `TestClient` 正反对照与兼容性控制 | E5（本轮复验） |
| SF-WS-05-A2 | `selfforge-agent test-regression --output PATH` 在 `confirmed`（exit 0）、`inconclusive`（exit 1）与 `error`（exit 2）报告下均将标准 JSON 写入 `PATH` 并保留 stdout 输出；`trace --json` 与 `trace --summary --json` 均支持结构化 JSON 输出及 `--output PATH`，同时保留既有 `--summary` / NDJSON 默认输出 | CLI `main()` 与真实 `SessionStore` / 回归报告导出回归 | E6（本轮复验） |
| SF-WS-05-A3 | `test-regression`、`trace`、`workspaces storage` 共用同一 `_write_cli_output`：对符号链接目标、目录/FIFO 非常规文件、缺失父目录均提前 fail-closed 拒绝（非零退出且不破坏已有目标或跟随软链接写入） | 负路径（symlink/directory/missing-parent）回归断言 | E7（本轮复验） |
| SF-WS-05-A4 | Ruff、SDD 一致性检查、`git diff --check`、专项及既有回归与全量非集成单测通过，CI 全绿 | `ruff check .` + `check_sdd.py` + `pytest` + CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-WS-05-A1 | `tests/test_ws_cli_export_and_storage_parity.py::test_storage_api_uses_configured_coding_workspaces_manager_consistently`、`tests/test_ws_cli_export_and_storage_parity.py::test_storage_api_preserves_env_override_compatibility` | FastAPI `TestClient` + 真实 Git worktree；验证 `_CODING_WORKSPACES` 覆写一致、`SELFFORGE_WORKSPACES_ROOT` 兼容及 `quota_bytes=-1` HTTP 400 拒绝；[PR #63](https://github.com/liuzhouquan/selfforge-agent/pull/63) | 源码 SHA `4805e05e2cd4d00d1086de0f7e38165fdfe164e3`；本地专项 `53 passed, 1 skipped`；CI [Run #43216637277](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43216637277) success |
| E2 | SF-WS-05-A2 | `tests/test_ws_cli_export_and_storage_parity.py::test_cli_test_regression_writes_output_report_across_statuses`、`tests/test_ws_cli_export_and_storage_parity.py::test_cli_trace_supports_json_and_output_while_keeping_legacy_modes` | `CLI main()` + 真实 `SessionStore` trace/metrics 及 `test-regression` exit 0/1/2 三态报告导出；兼容既有 `tests/test_session_metrics.py::test_cli_trace_summary_output` | `4805e05e2cd4d00d1086de0f7e38165fdfe164e3`；passed；CI [Run #43216637277](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43216637277) success |
| E3 | SF-WS-05-A3 | `tests/test_ws_cli_export_and_storage_parity.py::test_cli_outputs_fail_closed_for_symlinks_directories_and_missing_parents` | `trace` / `test-regression` / `workspaces storage` 三入口共测 symlink、目录、缺失父目录及 FIFO fail-closed（含 `O_NOFOLLOW`/`lstat`/`fstat` 防护，不覆写目标文件） | `4805e05e2cd4d00d1086de0f7e38165fdfe164e3`；passed；CI [Run #43216637277](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43216637277) success |
| E4 | SF-WS-05-A4 | `tests/test_ws_cli_export_and_storage_parity.py::test_storage_api_uses_configured_coding_workspaces_manager_consistently`、`tests/test_incoming_storage_regressions.py::test_api_preserves_incomplete_scan_and_unknown_quota`、`tests/test_session_metrics.py::test_cli_trace_summary_output` | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q -m "not integration"` 与 GitHub Actions CI（含全套 Docker 集成） | `4805e05e2cd4d00d1086de0f7e38165fdfe164e3`；本地 `648 passed, 14 deselected`；CI [Run #43216637277](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43216637277) success |
| E5 | SF-WS-05-A1 | `tests/test_ws_cli_export_and_storage_parity.py::test_storage_api_uses_configured_coding_workspaces_manager_consistently`、`tests/test_ws_cli_export_and_storage_parity.py::test_storage_api_preserves_env_override_compatibility` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；CLI 子进程/真实 Git/存储 API；FIFO 替换与磁盘内容对比 | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E6 | SF-WS-05-A2 | `tests/test_ws_cli_export_and_storage_parity.py::test_cli_test_regression_writes_output_report_across_statuses`、`tests/test_ws_cli_export_and_storage_parity.py::test_cli_trace_supports_json_and_output_while_keeping_legacy_modes` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；CLI 子进程/真实 Git/存储 API；FIFO 替换与磁盘内容对比 | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E7 | SF-WS-05-A3 | `tests/test_ws_cli_export_and_storage_parity.py::test_trace_export_rejects_fifo_replacement_without_blocking`、`tests/test_ws_cli_export_and_storage_parity.py::test_export_checks_descriptor_before_truncation`、`tests/test_ws_cli_export_and_storage_parity.py::test_cli_outputs_fail_closed_for_symlinks_directories_and_missing_parents` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；CLI 子进程/真实 Git/存储 API；FIFO 替换与磁盘内容对比 | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |
| E8 | SF-WS-05-A4 | `tests/test_ws_cli_export_and_storage_parity.py::test_storage_api_uses_configured_coding_workspaces_manager_consistently` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；CLI 子进程/真实 Git/存储 API；FIFO 替换与磁盘内容对比 | `f88c2b95355869834e9c4f8bb406dfc6f97baea9`；passed；完整组合 691 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227)；见[审查记录](./review-pr61-63.md) |

执行记录（Owner/状态以上表为准）：Base=`19882cc1cf7d6c29309e677c1449b5154c6f8e1c`；Branch=`feat/sf-ws-05`；Worktree=`selfforge-sf-ws-05`；PR=[#63](https://github.com/liuzhouquan/selfforge-agent/pull/63)。先运行 TDD 红灯复现（6 failed，包括复现 `workspaces storage --output` 跟随符号链接覆盖目标文件以及 `/api/workspaces/storage` 忽略 `_CODING_WORKSPACES`），随后实现修复与导出一致性，并在源码提交 `4805e05e2cd4d00d1086de0f7e38165fdfe164e3` 通过本地全套非集成验证与 CI `Run #43216637277`；本状态提交仅修改文档。

本轮记录 E5–E8 取代当前验收引用；原 E1–E4 是历史来源，不把旧计数相加。

审查整合记录：Base=`19882cc1cf7d6c29309e677c1449b5154c6f8e1c`；Branch=`review/pr61-63`；PR=#63；Merge=`473f04d9f8d8b394801b86a6f9f6e9a386821274`；Source=`f88c2b95355869834e9c4f8bb406dfc6f97baea9`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：19882cc1cf7d6c29309e677c1449b5154c6f8e1c / review/pr61-63 (worktree: selfforge-review-pr61-63) / 已验证源码 f88c2b95355869834e9c4f8bb406dfc6f97baea9；后续对账仅文档
已完成验收与证据 ID：SF-WS-05-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；本次文档对账归 Codex；完整组合含真 Docker 无跳过；UI 浏览器 smoke 使用 API 替身，不能冒充真实端到端模型运行
PR / 准确 head CI / merge SHA（有则填）：PR #63 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43231456227（组合 head；各源 PR head CI 见审查记录） / 473f04d9f8d8b394801b86a6f9f6e9a386821274
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main 的准确结果回填 PR #63 交付描述
下一步：后续真实模型测量需另行明确执行任务并遵守 v3 冻结策略与预算，当前未运行该测量。
```

### SF-WS-06

**目标：** 将既有工作区治理接口（`GET /api/code/workspaces/audit`、`GET /api/code/workspaces/storage`、`POST /api/code/workspaces/prune`）与依赖新鲜度诊断接口（`POST /api/sandbox/freshness`）接入 Web Coding 工作台 UI（`src/selfforge_agent/api/static/index.html`、`src/selfforge_agent/api/static/app.js`、`src/selfforge_agent/api/static/style.css`），使开发者可在界面中按仓库只读审计过期 worktree、查看磁盘占用/不完整扫描预警、预演并显式确认安全清理工作区，以及只读检查依赖声明与 lockfile 一致性。

**范围/入口：** 仅改动 `src/selfforge_agent/api/static/` 与专项测试 `tests/test_coding_web_governance_ui.py`；不修改后端 `/workspaces/*` 与 `/sandbox/freshness` 语义和安全边界，不引入自动依赖安装或后台无确认删除，不改动 PR 审查/Repair/TestGen 主流程。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-WS-06-A1 | 输入有效 Git 仓库后，支持快速审计（`GET /api/code/workspaces/audit`）与存储/配额审计（`GET /api/code/workspaces/storage`，可选非负整数 `quota_bytes` 与 `head_ref`）；正确安全渲染汇总统计、建议、每工作区锁定/脏区/合并/可清理标志与不完整扫描（`complete: false` / `quota_exceeded: null` / `scan_error`）告警；非法 `quota_bytes`（负数/小数/非数字）在前端 fail-closed 拦截且 HTTP 错误展示清晰 | Node DOM 运行时契约测试 + FastAPI TestClient 真实仓库端到端验证 | E5（本轮复验） |
| SF-WS-06-A2 | `prune` 面板默认勾选 `merged_only`、未勾选清理确认时禁用物理执行按钮仅允许 Dry Run 预演；勾选确认后调用 `POST /api/code/workspaces/prune` 并清晰渲染 `pruned`/`skipped_locked`/`skipped_dirty`/`skipped_unmerged` 明细，随后重置确认复选框并刷新工作区状态；若当前打开的工作区被清理，同步更新交付/回归可用态 | Prune 门禁状态机与会话联动回归断言 | E6（本轮复验） |
| SF-WS-06-A3 | `POST /api/sandbox/freshness`（支持可选 `lock_file` / `requirements_file`）正确渲染 `fresh`/`no_declarations`/`missing_dependencies`/`incompatible_versions`/`stale_mtime`、缺失/冲突包明细与只读提示；所有服务端字符串与错误信息仅经 DOM API / `textContent` 渲染（禁用 `innerHTML`），仓库切换（含并发 `A -> B -> A` 竞态）严格作废过期响应 | Freshness 诊断契约测试 + 竞态与 XSS 安全断言 | E7（本轮复验） |
| SF-WS-06-A4 | `node --check`、Web 治理诊断专项与既有回归测试、`ruff check .`、`python scripts/check_sdd.py`、`git diff --check` 与全量非集成测试通过，CI 全绿 | 本地验证命令矩阵 + CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-WS-06-A1 | `tests/test_coding_web_governance_ui.py::test_coding_workbench_html_contains_governance_and_freshness_panels`、`tests/test_coding_web_governance_ui.py::test_web_ui_workspace_audit_and_storage_render_and_quota_guards`、`tests/test_coding_web_governance_ui.py::test_web_ui_governance_and_freshness_roundtrip_real_fastapi_repo` | Node v24.18.0 VM DOM 运行时 + FastAPI `TestClient` + 真实临时 Git 仓库；`pytest -q tests/test_coding_web_governance_ui.py` | 源码 SHA `28a1a037044667e23f9c2e928cef8dfa396c6e7a`；5 passed；[PR #64 R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43260693106) passed |
| E2 | SF-WS-06-A2 | `tests/test_coding_web_governance_ui.py::test_web_ui_workspace_prune_dry_run_confirmation_and_session_sync`、`tests/test_coding_web_governance_ui.py::test_web_ui_governance_and_freshness_roundtrip_real_fastapi_repo` | Node v24.18.0 VM DOM + FastAPI `TestClient`（真实合并/脏区 worktree 干跑校验） | 源码 SHA `28a1a037044667e23f9c2e928cef8dfa396c6e7a`；5 passed；[PR #64 R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43260693106) passed |
| E3 | SF-WS-06-A3 | `tests/test_coding_web_governance_ui.py::test_web_ui_freshness_diagnostics_and_repo_switch_race_invalidation`、`tests/test_coding_web_governance_ui.py::test_web_ui_governance_and_freshness_roundtrip_real_fastapi_repo` | Node VM 严格禁用 `innerHTML` 读写 + `A -> B -> A` 竞态延后返回对照 + FastAPI `TestClient` | 源码 SHA `28a1a037044667e23f9c2e928cef8dfa396c6e7a`；5 passed；[PR #64 R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43260693106) passed |
| E4 | SF-WS-06-A4 | `tests/test_coding_web_governance_ui.py::test_web_ui_governance_and_freshness_roundtrip_real_fastapi_repo`；`node --check src/selfforge_agent/api/static/app.js`、`ruff check .`、`python scripts/check_sdd.py`、`git diff --check` | 本地 `pytest -q tests/test_coding_web_governance_ui.py`（5 passed）与 `pytest -q -m "not integration"`（681 passed, 15 deselected）+ GitHub Actions CI | 源码 SHA `28a1a037044667e23f9c2e928cef8dfa396c6e7a`；681 passed；[PR #64 R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43260693106) passed |
| E5 | SF-WS-06-A1 | `tests/test_coding_web_governance_ui.py::test_web_ui_workspace_audit_and_storage_render_and_quota_guards` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E6 | SF-WS-06-A2 | `tests/test_coding_web_governance_ui.py::test_web_ui_workspace_prune_dry_run_confirmation_and_session_sync`、`tests/test_coding_web_governance_ui.py::test_prune_only_invalidates_active_session_in_same_repository` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E7 | SF-WS-06-A3 | `tests/test_coding_web_governance_ui.py::test_web_ui_freshness_diagnostics_and_repo_switch_race_invalidation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E8 | SF-WS-06-A4 | `tests/test_coding_web_governance_ui.py::test_web_ui_governance_and_freshness_roundtrip_real_fastapi_repo` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |

执行记录（Owner/状态以上表为准）：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`feat/sf-ws-06`；Worktree=`selfforge-sf-ws-06`；PR=[#64](https://github.com/liuzhouquan/selfforge-agent/pull/64)。先运行 `tests/test_coding_web_governance_ui.py` 确认 TDD 红灯（5 failed），随后完成 Web 治理与新鲜度诊断 UI 接入（源码提交 `28a1a037044667e23f9c2e928cef8dfa396c6e7a`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43260693106) 全绿）；本状态提交仅更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的计数不相加。

审查整合记录：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`review/pr64-67`；PR=#64；Merge=`c7229c9dec1a923a01fb50d777ed53036c2a4c65`；Source=`c3b311ff674c6912218122967fee27f4d471deb0`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：60a1b386cda8f811be1edb5b7baf9e433a894c58 / review/pr64-67 (worktree: selfforge-review-pr64-67) / 已验证源码 c3b311ff674c6912218122967fee27f4d471deb0；后续对账仅文档
已完成验收与证据 ID：SF-WS-06-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；对账文档归 Codex；真 Docker 完整测试无跳过；浏览器 smoke 使用 API 替身，不冒充外部模型端到端运行
PR / 准确 head CI / merge SHA（有则填）：PR #64 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570（组合 head；各源 PR 见审查记录） / c7229c9dec1a923a01fb50d777ed53036c2a4c65
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 准确结论回填 PR #67 交付描述
下一步：后续真实模型测量需另行明确执行任务并遵守已冻结策略与预算，当前未运行该测量。
```

### SF-EVAL-09

**目标：** 为 CLI `eval-suite` 与 `eval-validate` 接入 `--policy PATH` 参数（与 `--manifest` 互斥），在预检与评测启动前调用 `verify_eval_policy_bundle` 执行 fail-closed 强校验，并严格约束 CLI 参数不得与策略冲突（包括 `provider`、`model`、`base_url`、`fallback`、`repeats`、`max_steps`、`time_budget`、`test_timeout`、`sandbox`）；在报告中注入策略与资产 Provenance 元数据；并将 `eval-suite`、`eval-validate`、`eval-diff` 的 `--output` 写入统一接入 `_write_cli_output` 安全校验（防御符号链接、目录、FIFO 特殊文件与缺失父目录）。

**范围/入口：** 仅改动 `src/selfforge_agent/cli.py`（参数定义与 `eval-suite`、`eval-validate`、`eval-diff` 处理逻辑）、`src/selfforge_agent/runtime/eval_policy.py`（如增加策略 CLI 校验与元数据注入 helper）及专项测试 `tests/test_eval_policy_cli.py`；不执行付费模型调用（纯离线 / mock / 本地 Docker 预检），不改动 Web 工作台或通用 Coding 循环。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-EVAL-09-A1 | `selfforge-agent eval-validate --policy PATH`：调用 `verify_eval_policy_bundle`，若策略损坏/哈希篡改/资产缺失则 fail-closed 退出且不启动执行；有效策略自动提取 manifest 与 image profile 运行 `validate_suite`，输出报告中包含 `policy_path`、`policy_sha256`、`suite_id`、`manifest_sha256` 与 `assets_inventory_sha256` | CLI 子进程与真实 policy bundle 测试（正反例） | E5（本轮复验） |
| SF-EVAL-09-A2 | `selfforge-agent eval-suite --policy PATH`：自动对齐策略环境（`sandbox="docker"`）、预算（`repeats`、`max_steps`、`time_budget`、`test_timeout`）及供应商配置；若传入冲突参数（如 `--sandbox host`、越界 `--repeats`、冲突 `--provider` 等）则在构造模型前 fail-closed 拦截并给出明确原因；报告中包含完整策略 provenance | CLI 参数门禁单元测试与错误路径断言 | E6（本轮复验） |
| SF-EVAL-09-A3 | `eval-suite`、`eval-validate`、`eval-diff` 的 `--output PATH` 统一复用 `_write_cli_output`：对符号链接目标、目录/FIFO 非常规文件、缺失父目录均提前 fail-closed 拒绝且不破坏既有文件；常规合法目标正常导出 UTF-8 JSON / 文本 | 三入口 symlink/FIFO/directory fail-closed 测试 | E7（本轮复验） |
| SF-EVAL-09-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试通过，CI 全绿 | 本地验证命令矩阵 + CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-EVAL-09-A1 | `tests/test_eval_policy_cli.py::test_eval_validate_policy_flag_validation_and_provenance` | CLI 子进程与真实 policy bundle 测试（测试损坏策略/哈希篡改/非法资产 fail-closed 拦截，以及合法 policy 的 manifest 自动加载与 provenance 注入）；[PR #65](https://github.com/liuzhouquan/selfforge-agent/pull/65) | 源码 SHA `dc2487851c3f58444995b6746e20a018d7789a2c`；passed；CI [Run #43343180408](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43343180408) success |
| E2 | SF-EVAL-09-A2 | `tests/test_eval_policy_cli.py::test_eval_suite_policy_enforcement_and_conflicts` | CLI 单元测试（测试 `--policy` 与 `--manifest` 互斥、冲突 `--sandbox`/`--repeats`/`--max-steps`/`--provider` fail-closed 拒绝，以及合法策略继承默认预算与 provenance 注入）；[PR #65](https://github.com/liuzhouquan/selfforge-agent/pull/65) | 源码 SHA `dc2487851c3f58444995b6746e20a018d7789a2c`；passed；CI [Run #43343180408](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43343180408) success |
| E3 | SF-EVAL-09-A3 | `tests/test_eval_policy_cli.py::test_eval_cli_output_guards_across_eval_commands` | `eval-validate`、`eval-suite`、`eval-diff` 三入口共测 `--output` 对符号链接、目录、FIFO 特殊文件与缺失父目录的 fail-closed 拦截以及常规文件正常写入；[PR #65](https://github.com/liuzhouquan/selfforge-agent/pull/65) | 源码 SHA `dc2487851c3f58444995b6746e20a018d7789a2c`；passed；CI [Run #43343180408](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43343180408) success |
| E4 | SF-EVAL-09-A4 | `tests/test_eval_policy_cli.py::test_eval_suite_policy_enforcement_and_conflicts` | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q -m "not integration"` 与 GitHub Actions CI（包含 Docker 集成测试） | 源码 SHA `dc2487851c3f58444995b6746e20a018d7789a2c`；本地 `679 passed, 15 deselected`；CI [Run #43343180408](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43343180408) success |
| E5 | SF-EVAL-09-A1 | `tests/test_eval_policy_cli.py::test_eval_validate_policy_flag_validation_and_provenance`、`tests/test_eval_policy_cli.py::test_verified_input_bytes_survive_later_file_changes` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E6 | SF-EVAL-09-A2 | `tests/test_eval_policy_cli.py::test_policy_routes_credentials_and_propagates_frozen_inputs`、`tests/test_eval_policy_cli.py::test_context_budget_reaches_runtime_agent`、`tests/test_eval_policy_cli.py::test_policy_rejects_explicit_fallback_conflict`、`tests/test_eval_policy_cli.py::test_policy_does_not_treat_default_image_as_explicit_override`、`tests/test_eval_policy_cli.py::test_policy_rejects_mismatched_endpoint_before_evaluation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E7 | SF-EVAL-09-A3 | `tests/test_eval_policy_cli.py::test_eval_cli_output_guards_across_eval_commands` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E8 | SF-EVAL-09-A4 | `tests/test_eval_policy_cli.py::test_eval_suite_policy_enforcement_and_conflicts` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |

执行记录（Owner/状态以上表为准）：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`feat/sf-eval-09`；Worktree=`selfforge-sf-eval-09`；PR=[#65](https://github.com/liuzhouquan/selfforge-agent/pull/65)。实现 `--policy` 强校验、预算门禁与安全导出（源码提交 `dc2487851c3f58444995b6746e20a018d7789a2c`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43343180408) 全绿）；本状态提交仅更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的计数不相加。

执行层必要接入包含 `runtime/benchmark.py`：验证后的 manifest/holdout/reference 字节直达预检，策略上下文预算直达 CodingAgent；供应商凭据沿用其真实路由，端点冲突提前拒绝；来源元数据不输出策略绝对私有路径。未在本审查运行付费模型测量。

审查整合记录：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`review/pr64-67`；PR=#65；Merge=`6a703e7ca0695ac5505246d0aad137d6afcc361f`；Source=`c3b311ff674c6912218122967fee27f4d471deb0`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：60a1b386cda8f811be1edb5b7baf9e433a894c58 / review/pr64-67 (worktree: selfforge-review-pr64-67) / 已验证源码 c3b311ff674c6912218122967fee27f4d471deb0；后续对账仅文档
已完成验收与证据 ID：SF-EVAL-09-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；对账文档归 Codex；真 Docker 完整测试无跳过；浏览器 smoke 使用 API 替身，不冒充外部模型端到端运行
PR / 准确 head CI / merge SHA（有则填）：PR #65 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570（组合 head；各源 PR 见审查记录） / 6a703e7ca0695ac5505246d0aad137d6afcc361f
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 准确结论回填 PR #67 交付描述
下一步：后续真实模型测量需另行明确执行任务并遵守已冻结策略与预算，当前未运行该测量。
```

### SF-ENV-07

**目标：** 为 CLI `sandbox` 子命令（`detect`、`plan`、`audit`、`freshness` / `check`、`export-lock`）统一接入结构化输出（`--json`）与安全落盘校验（`_write_cli_output` 与 `_validate_cli_output_target`），修复 `audit`、`freshness`、`plan`、`export-lock` 原有无防护覆盖符号链接目标、FIFO 挂起与向非普通文件/非存在父目录写入的漏洞，并为 `sandbox detect` 与 `sandbox plan` 补齐 `--json` 与 `--output` 结构化导出能力。

**范围/入口：** 仅改动 `src/selfforge_agent/cli.py`（`sandbox` 参数配置与子命令执行逻辑）、`src/selfforge_agent/runtime/environment.py`（`export_hash_locked_requirements` 结合目标安全校验）及专项测试 `tests/test_sandbox_cli_export.py`；保持既有默认终端文本输出 100% 向后兼容；不执行任意代码或网络安装，不引入新外部依赖。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-ENV-07-A1 | `selfforge-agent sandbox detect`：默认保持既有易读文本输出；当传入 `--json` 时以结构化 JSON 格式输出 `detect_repository_environment` 完整字典（含 `layout`、`test_framework`、`test_paths`、`dependency_files`、`lock_files`、`has_hash_lock` 等）；当传入 `--output PATH` 时通过 `_write_cli_output` 安全写入指定路径 | CLI `main()` 子命令与真实/虚拟 Git 仓库断言 | E5（本轮复验） |
| SF-ENV-07-A2 | `selfforge-agent sandbox plan`：支持 `--json` 输出结构化依赖准备计划，当传入 `--output PATH` 时复用 `_write_cli_output` 安全落盘，且未指定 `--output` 时通过 stdout 输出，保证终端与自动化管道兼容 | CLI 结构化计划导出单元与格式对照测试 | E6（本轮复验） |
| SF-ENV-07-A3 | `sandbox audit`、`sandbox freshness`（及 `sandbox check`）、`sandbox export-lock` 接入安全导出：对符号链接目标、目录、FIFO 特殊文件、缺失父目录均提前 fail-closed 拒绝（退出码 2 且不覆写/损坏目标），常规合法路径正常写入 UTF-8 文件且权限受控 | 负路径（symlink/directory/FIFO/missing-parent）注入测试与安全写盘校验 | E7（本轮复验） |
| SF-ENV-07-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试通过，CI 全绿 | 本地验证命令矩阵 + CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-ENV-07-A1 | `tests/test_sandbox_cli_export.py::test_sandbox_detect_json_and_output` | CLI 子进程与真实/虚拟 Git 仓库断言（测试默认文本兼容、`--json` 输出格式化环境结构体、`--output` 安全落盘）；[PR #66](https://github.com/liuzhouquan/selfforge-agent/pull/66) | 源码 SHA `fc95a1f0ee00caf860f0c754031c536765b2a3db`；passed；CI [Run #43359670447](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43359670447) success |
| E2 | SF-ENV-07-A2 | `tests/test_sandbox_cli_export.py::test_sandbox_plan_json_and_output` | CLI `sandbox plan` 单元测试（测试 `--json` 输出结构化计划、`--output` 安全写盘）；[PR #66](https://github.com/liuzhouquan/selfforge-agent/pull/66) | 源码 SHA `fc95a1f0ee00caf860f0c754031c536765b2a3db`；passed；CI [Run #43359670447](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43359670447) success |
| E3 | SF-ENV-07-A3 | `tests/test_sandbox_cli_export.py::test_sandbox_cli_outputs_fail_closed_against_symlinks_fifos_and_dirs` | `detect`、`plan`、`audit`、`freshness`、`check`、`export-lock` 六入口及 `export_hash_locked_requirements` 共测符号链接、目录、FIFO、缺失父目录 fail-closed 拦截与常规文件正常导出；[PR #66](https://github.com/liuzhouquan/selfforge-agent/pull/66) | 源码 SHA `fc95a1f0ee00caf860f0c754031c536765b2a3db`；passed；CI [Run #43359670447](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43359670447) success |
| E4 | SF-ENV-07-A4 | `tests/test_sandbox_cli_export.py::test_sandbox_detect_json_and_output` | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q -m "not integration"` 与 GitHub Actions CI（包含 Docker 集成测试） | 源码 SHA `fc95a1f0ee00caf860f0c754031c536765b2a3db`；本地 `680 passed, 15 deselected`；CI [Run #43359670447](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43359670447) success |
| E5 | SF-ENV-07-A1 | `tests/test_sandbox_cli_export.py::test_sandbox_detect_json_and_output` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E6 | SF-ENV-07-A2 | `tests/test_sandbox_cli_export.py::test_sandbox_plan_json_and_output`、`tests/test_sandbox_cli_export.py::test_sandbox_json_flag_always_produces_json_stdout` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E7 | SF-ENV-07-A3 | `tests/test_sandbox_cli_export.py::test_sandbox_cli_outputs_fail_closed_against_symlinks_fifos_and_dirs` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E8 | SF-ENV-07-A4 | `tests/test_sandbox_cli_export.py::test_sandbox_json_flag_always_produces_json_stdout` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |

执行记录（Owner/状态以上表为准）：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`feat/sf-env-07`；Worktree=`selfforge-sf-env-07`；PR=[#66](https://github.com/liuzhouquan/selfforge-agent/pull/66)。实现 `sandbox` 安全导出与 `--json` 结构化支持（源码提交 `fc95a1f0ee00caf860f0c754031c536765b2a3db`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43359670447) 全绿）；本状态提交仅更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的计数不相加。

带 `--json` 时 stdout 为纯 JSON，包括同时使用 `--output`；不带 flag 保留旧终端行为。`export-lock --json` stdout 为 `{"requirements": "..."}`，导出文件仍为原始哈希锁定 requirements 文本。

审查整合记录：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`review/pr64-67`；PR=#66；Merge=`839840acef5681f7b7ca6fb727d483b20a566998`；Source=`c3b311ff674c6912218122967fee27f4d471deb0`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：60a1b386cda8f811be1edb5b7baf9e433a894c58 / review/pr64-67 (worktree: selfforge-review-pr64-67) / 已验证源码 c3b311ff674c6912218122967fee27f4d471deb0；后续对账仅文档
已完成验收与证据 ID：SF-ENV-07-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；对账文档归 Codex；真 Docker 完整测试无跳过；浏览器 smoke 使用 API 替身，不冒充外部模型端到端运行
PR / 准确 head CI / merge SHA（有则填）：PR #66 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570（组合 head；各源 PR 见审查记录） / 839840acef5681f7b7ca6fb727d483b20a566998
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 准确结论回填 PR #67 交付描述
下一步：后续真实模型测量需另行明确执行任务并遵守已冻结策略与预算，当前未运行该测量。
```

### SF-TEST-03

**目标：** 在 `SessionStore` 中补齐针对 `{session_id}.regression.jsonl` 的持久化只读追溯能力（`regression_records`、`latest_regression_record`、`confirmed_regression_snapshot`）；在 Web API 中新增只读查询端点 `GET /api/code/workspaces/{session_id}/regression`，使已完成的回归验证无需重复消耗 Docker 预算即可幂等恢复与追溯；并在 `create_pull_request` 中自动绑定与当前提交指纹匹配的独立回归验证客观证据。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/session.py`（SessionStore 回归记录查询）、`src/selfforge_agent/api/app.py`（GET 回归端点与模型定义）、`src/selfforge_agent/runtime/delivery.py`（PR 交付证据绑定）及专项测试 `tests/test_durable_regression_session_and_delivery.py`；不改动通用 Coding 主循环，不引入外部模型调用。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-TEST-03-A1 | `SessionStore` 持久化追溯：支持解析有界 `{session_id}.regression.jsonl` 事件流，提供 `regression_records(limit)`、`latest_regression_record()` 与 `confirmed_regression_snapshot()`；损坏行/未提交尾部安全容错，严格过滤非法指纹，非 confirmed 或后续失败事件安全复位 | SessionStore 单元测试与事件流故障注入测试 | E5（本轮复验） |
| SF-TEST-03-A2 | `GET /api/code/workspaces/{session_id}/regression`：仅允许本机调用；存在有效验证记录时返回最近一次 `RegressionValidationReport` 结构体；无记录返回 404；非法仓库或无效会话 ID 返回 400 | FastAPI `TestClient` 正反对照与参数边界测试 | E6（本轮复验） |
| SF-TEST-03-A3 | `create_pull_request` 交付证据绑定：当会话具备与当前推送到远端的 `commit_sha` 指纹相匹配的已确认回归验证时，在 PR 描述中注入 `**Regression Validation**: Confirmed (old commit failed, repaired worktree passed)`；指纹不匹配或非 confirmed 状态时不虚增证实证据 | PR 交付证据链单元与真实 Git worktree 断言 | E7（本轮复验） |
| SF-TEST-03-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试通过，CI 全绿 | 本地验证命令矩阵 + CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-TEST-03-A1 | `tests/test_durable_regression_session_and_delivery.py::test_session_store_regression_records_and_snapshot` | `SessionStore` 持久化与事件流解析；故障注入、tail 解析、confirmed 状态判定与失败/拒绝后安全复位 | 源码 SHA `7abb10aa862a7b9a7e64db85939e8c9c53675a95`；passed；CI [Run #43363696337](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43363696337) success |
| E2 | SF-TEST-03-A2 | `tests/test_durable_regression_session_and_delivery.py::test_api_get_workspace_regression_report` | FastAPI `TestClient` 正反对照（404 / 200）、本地调用限制与模型序列化校验 | `7abb10aa862a7b9a7e64db85939e8c9c53675a95`；passed；CI [Run #43363696337](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43363696337) success |
| E3 | SF-TEST-03-A3 | `tests/test_durable_regression_session_and_delivery.py::test_create_pull_request_annotates_regression_evidence` | 真实 Git worktree 与 `create_pull_request` 交付流；验证无回归记录时不注入、指纹完全匹配时注入 Confirmed 标记 | `7abb10aa862a7b9a7e64db85939e8c9c53675a95`；passed；CI [Run #43363696337](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43363696337) success |
| E4 | SF-TEST-03-A4 | `tests/test_durable_regression_session_and_delivery.py::test_create_pull_request_annotates_regression_evidence` | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q -m "not integration"` 与 GitHub Actions CI（含 Docker 集成） | `7abb10aa862a7b9a7e64db85939e8c9c53675a95`；本地 679 passed, 15 deselected；CI [Run #43363696337](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43363696337) success |
| E5 | SF-TEST-03-A1 | `tests/test_regression_record_safety.py::test_invalid_confirmation_cannot_establish_evidence`、`tests/test_regression_record_safety.py::test_unreadable_latest_event_revokes_previous_confirmation`、`tests/test_regression_record_safety.py::test_records_are_sanitized_and_symlinks_are_not_read`、`tests/test_regression_record_safety.py::test_oversized_journal_cannot_reuse_a_confirmation`、`tests/test_regression_record_safety.py::test_legacy_confirmation_requires_consistent_flat_statuses` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E6 | SF-TEST-03-A2 | `tests/test_regression_record_safety.py::test_real_validation_roundtrips_complete_report_through_get`、`tests/test_regression_record_safety.py::test_get_rejects_fake_git_directory`、`tests/test_regression_record_safety.py::test_get_rejects_invalid_session_ids_without_server_error`、`tests/test_coding_regression_validation.py::test_real_docker_regression_validation_confirmed_and_counterexample` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E7 | SF-TEST-03-A3 | `tests/test_regression_record_safety.py::test_delivery_rejects_regression_from_another_base` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |
| E8 | SF-TEST-03-A4 | `tests/test_regression_record_safety.py::test_later_valid_record_recovers_after_corrupt_history` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 指向已预检的五源缓存；固定 v3 缓存通过 --offline；真 Docker；UI 另做 Chromium/API 替身 smoke；策略模型测试只用脚本模型/替身 | `c3b311ff674c6912218122967fee27f4d471deb0`；passed；完整组合 741 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570)；[审查记录](./review-pr64-67.md) |

执行记录（Owner/状态以上表为准）：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`feat/sf-test-03`；Worktree=`selfforge-sf-test-03`；PR=[#67](https://github.com/liuzhouquan/selfforge-agent/pull/67)。先运行 TDD 红灯复现（3 failed），随后实现 SessionStore 回归事件读取与状态机、GET 只读端点及 PR 交付证据链绑定；在源码提交 `7abb10aa862a7b9a7e64db85939e8c9c53675a95` 通过本地全量 679 项单测与 GitHub Actions CI `Run #43363696337`（含 Docker 集成）；本状态提交仅修改文档。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的计数不相加。

回归日志读取上限：总量 4 MiB、单行 16 KiB、10000 条；非普通文件/符号链接/忙锁/扫描不完整均不能建立当前证据。损坏或未提交的最新事件撤销确认，后续合法完整事件可重新建立。旧日志不编造未保存的运行计数，新的生产 writer 保存完整安全摘要。GET 是历史追溯；交付另行校验 session、初始 base 与实际推送提交的当前内容指纹。必要实现包含 `coding/regression_validation.py` 的生产写入接入。

审查整合记录：Base=`60a1b386cda8f811be1edb5b7baf9e433a894c58`；Branch=`review/pr64-67`；PR=#67；Merge=`59a03b8a91aaa18f1648ee9b761ab894fb845a09`；Source=`c3b311ff674c6912218122967fee27f4d471deb0`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：60a1b386cda8f811be1edb5b7baf9e433a894c58 / review/pr64-67 (worktree: selfforge-review-pr64-67) / 已验证源码 c3b311ff674c6912218122967fee27f4d471deb0；后续对账仅文档
已完成验收与证据 ID：SF-TEST-03-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交内容；对账文档归 Codex；真 Docker 完整测试无跳过；浏览器 smoke 使用 API 替身，不冒充外部模型端到端运行
PR / 准确 head CI / merge SHA（有则填）：PR #67 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43372329570（组合 head；各源 PR 见审查记录） / 59a03b8a91aaa18f1648ee9b761ab894fb845a09
仍在运行的本任务进程或 CI（无则写无）：本地进程无；最终 main CI 准确结论回填 PR #67 交付描述
下一步：后续真实模型测量需另行明确执行任务并遵守已冻结策略与预算，当前未运行该测量。
```

### SF-TEST-04

**目标：** 在 Web Coding 工作台前端（`src/selfforge_agent/api/static/app.js`）接入 `SF-TEST-03` 交付的 `GET /api/code/workspaces/{session_id}/regression` 只读端点：当工作台加载或切换至已具备有效独立回归验证记录的 Coding 会话时，自动幂等恢复最近一次回归验证状态与结果面板，无需重复消耗 60s Docker 预算；在无记录（404）时干净复位；在工作区发生 Revert、Commit 或移除时执行时效性联动（更新状态提示或隐藏面板）；并在并发请求时通过 `isCurrentRequest()` 防竞态污染。

**范围/入口：** 仅改动 `src/selfforge_agent/api/static/app.js`（`renderCodingJob` 状态挂钩与时效联动）及专项测试 `tests/test_coding_web_regression_ui.py`；不改动服务端 API 行为，不执行外部模型调用。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-TEST-04-A1 | 加载/切换具有已完成回归验证的独立工作区会话时，自动调用 `GET /api/code/workspaces/{session_id}/regression` 幂等恢复回归面板，呈现 status、base_status、repaired_status、耗时及指纹等结构化卡片，无需重新点击验证按钮 | 前端 DOM/API 联动单元测试 | E5（本轮复验） |
| SF-TEST-04-A2 | 404 无记录会话、直接模式（!branch）、本机 sandbox 会话或非法会话切换时，保持/复位回归面板为初始干净状态，不产生未捕获异常或控制台错误 | 前端 DOM/API 正反对照测试 | E6（本轮复验） |
| SF-TEST-04-A3 | 时效与并发联动控制：当会话执行 Commit、Revert 或移除工作区时，及时撤销或更新回归有效性提示；异步网络响应在快速切换会话时不发生跨会话状态串扰（`isCurrentRequest` 保护） | 会话切换与修改动作模拟测试 | E7（本轮复验） |
| SF-TEST-04-A4 | `ruff check .`、`python scripts/check_sdd.py`、`node --check src/selfforge_agent/api/static/app.js`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-TEST-04-A1 | `tests/test_coding_web_regression_ui.py::test_web_ui_restores_persisted_regression_report_on_job_render` | 前端 DOM/API 联动；验证加载会话时自动拉取持久化回归记录并渲染结构化卡片与状态 | 源码 SHA `e3a2fed6e776c4c6aa448b9d8abc69dad2edff76`；passed；CI [Run #43377209159](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43377209159) success |
| E2 | SF-TEST-04-A2 | `tests/test_coding_web_regression_ui.py::test_web_ui_durable_regression_404_resets_panel_cleanly`、`tests/test_coding_web_regression_ui.py::test_web_ui_direct_mode_skips_durable_regression_query_and_commit_invalidates` | 前端 DOM/API 正反对照；验证 404 无记录会话静默复位、直接模式（!branch）不发查询 | `e3a2fed6e776c4c6aa448b9d8abc69dad2edff76`；passed；CI [Run #43377209159](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43377209159) success |
| E3 | SF-TEST-04-A3 | `tests/test_coding_web_regression_ui.py::test_web_ui_direct_mode_skips_durable_regression_query_and_commit_invalidates`、`tests/test_coding_web_regression_ui.py::test_web_ui_revert_invalidates_stale_report_and_unrecorded_sandbox_disables_submit` | 工作区改动时效联动；验证 commit 与 revert 成功后主动复位失效的旧回归面板，防跨状态串扰 | `e3a2fed6e776c4c6aa448b9d8abc69dad2edff76`；passed；CI [Run #43377209159](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43377209159) success |
| E4 | SF-TEST-04-A4 | `tests/test_coding_web_regression_ui.py::test_web_ui_restores_persisted_regression_report_on_job_render` | `ruff check .`、`python scripts/check_sdd.py`、`node --check src/selfforge_agent/api/static/app.js`、`git diff --check`、`pytest -q -m "not integration"` 与 GitHub Actions CI | `e3a2fed6e776c4c6aa448b9d8abc69dad2edff76`；本地 729 passed, 15 deselected；CI [Run #43377209159](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43377209159) success |
| E5 | SF-TEST-04-A1 | `tests/test_coding_web_regression_ui.py::test_web_ui_restores_persisted_regression_report_on_job_render` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E6 | SF-TEST-04-A2 | `tests/test_coding_web_regression_ui.py::test_durable_restore_requires_explicit_docker`、`tests/test_coding_web_regression_ui.py::test_web_ui_durable_regression_404_resets_panel_cleanly` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E7 | SF-TEST-04-A3 | `tests/test_coding_web_regression_ui.py::test_invalidated_history_does_not_reload_on_same_job_poll`、`tests/test_coding_web_regression_ui.py::test_regression_responses_cannot_survive_invalidation`、`tests/test_coding_web_regression_ui.py::test_web_ui_direct_mode_skips_durable_regression_query_and_commit_invalidates` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E8 | SF-TEST-04-A4 | `tests/test_coding_web_regression_ui.py::test_web_ui_submits_regression_validation_and_renders_report_safely` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |

执行记录（Owner/状态以上表为准）：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`feat/sf-test-04`；Worktree=`selfforge-sf-test-04`；PR=[#68](https://github.com/liuzhouquan/selfforge-agent/pull/68)。先运行 TDD 红灯复现（1 failed），随后在 app.js 中实现 loadDurableRegressionReport、renderCodingJob 挂钩与 commit/revert 时效复位；在源码提交 `e3a2fed6e776c4c6aa448b9d8abc69dad2edff76` 通过本地全量 729 项单测与 GitHub Actions CI `Run #43377209159`（含全套 Docker 集成测试通过）；本状态提交仅修改文档。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。恢复仅用于明确 Docker 的停止态独立工作区；历史 badge/原因/卡片均标注历史性，旧摘要缺失不编造结果。修改失效后，同一上下文轮询不再重新恢复旧记录；新上下文可重新查询。

审查整合：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`review/pr68-73`；PR=#68；Merge=`68aae685e1515d54d3aa03dbd45acfa7fdae33f4`；最终受测 Source=`f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：10eff75cb3c5e35b2531e5dd2d9da17a894d2a92 / review/pr68-73 (worktree: selfforge-review-pr68-73) / 已验证源码 f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7；后续对账仅文档
已完成验收与证据 ID：SF-TEST-04-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 787 passed、无跳过；浏览器 smoke 使用 API 替身；未运行付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #68 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659（最终组合；各源 PR 见审查记录） / 68aae685e1515d54d3aa03dbd45acfa7fdae33f4
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #73 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动执行付费模型测量。
```

### SF-CLI-01

**目标：** 将 `selfforge-agent review`、`repair`、`generate-test` 子命令中的裸 `write_text` 统一迁移至 `_validate_cli_output_target` 和 `_write_cli_output`，提前防御符号链接劫持、目录/FIFO 等非法目标写入，防穿透崩溃并实现 fail-closed 拦截；同时为 `review`、`repair`、`generate-test` 补齐 `--json` 格式化纯 JSON stdout 导出支持（同时使用 `--json --output` 时标准输出保持合法 JSON，不带 flag 保持原有终端人类可读文本），与 `sandbox`/`trace`/`eval`/`workspaces` 规范完全对齐。

**范围/入口：** 仅改动 `src/selfforge_agent/cli.py` 及专项测试 `tests/test_cli_review_repair_export.py`；不改动 Review/Repair 底层核心逻辑与模型路由。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-CLI-01-A1 | `selfforge-agent review --json`、`repair --json` 与 `generate-test --json` 输出纯结构化 JSON，不混入人类交互文本；不带 `--json` 保持原有标准输出与退出码语义 | CLI 子进程与命令参数正反对照测试 | E5（本轮复验） |
| SF-CLI-01-A2 | `review --output`、`repair --output`、`repair --patch-output`、`generate-test --output` 与 `generate-test --test-output` 统一复用 `_validate_cli_output_target` 与 `_write_cli_output` 安全写入常规文件 | CLI 导出文件内容与完整序列化断言 | E6（本轮复验） |
| SF-CLI-01-A3 | 针对符号链接目标、目录、FIFO 等非常规文件以及缺失父目录的目标路径均提前 fail-closed 报错并以非零码退出，不破坏、覆写已有目标或跟随符号链接 | 负路径（symlink/directory/fifo/missing-parent）安全隔离测试 | E7（本轮复验） |
| SF-CLI-01-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-CLI-01-A1 | `tests/test_cli_review_repair_export.py::test_cli_review_json_flag_and_safe_output`、`tests/test_cli_review_repair_export.py::test_cli_repair_rejects_symlinks_and_supports_json_export`、`tests/test_cli_review_repair_export.py::test_cli_generate_test_rejects_symlinks_and_supports_json_export` | CLI review、repair 与 generate-test 独立子进程与 `--json` 命令行参数；验证无 flag 时人类可读终端文本、带 `--json` 输出纯结构化 JSON；[PR #69](https://github.com/liuzhouquan/selfforge-agent/pull/69) | 源码 SHA `b8fd21f2bb872fa82d77494a8bc983b63cc682c3`；passed；CI [Run #43386721038](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43386721038) success |
| E2 | SF-CLI-01-A2 | `tests/test_cli_review_repair_export.py::test_cli_review_json_flag_and_safe_output`、`tests/test_cli_review_repair_export.py::test_cli_repair_rejects_symlinks_and_supports_json_export`、`tests/test_cli_review_repair_export.py::test_cli_generate_test_rejects_symlinks_and_supports_json_export` | 统一迁移 `review --output`、`repair --output`、`repair --patch-output`、`generate-test --output`、`generate-test --test-output` 至 `_write_cli_output` 安全落盘；断言落盘文件完整内容与模型 dump 一致；[PR #69](https://github.com/liuzhouquan/selfforge-agent/pull/69) | 源码 SHA `b8fd21f2bb872fa82d77494a8bc983b63cc682c3`；passed；CI [Run #43386721038](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43386721038) success |
| E3 | SF-CLI-01-A3 | `tests/test_cli_review_repair_export.py::test_cli_review_rejects_symlinks_directories_and_missing_parents`、`tests/test_cli_review_repair_export.py::test_cli_repair_rejects_symlinks_and_supports_json_export`、`tests/test_cli_review_repair_export.py::test_cli_generate_test_rejects_symlinks_and_supports_json_export` | 针对预先存在的符号链接、目录目标、以及缺失父目录路径，提前于模型调用与重度执行前执行 `_validate_cli_output_target` fail-closed 拦截，断言退出码为 2 且原目标文件未被修改破坏；[PR #69](https://github.com/liuzhouquan/selfforge-agent/pull/69) | 源码 SHA `b8fd21f2bb872fa82d77494a8bc983b63cc682c3`；passed；CI [Run #43386721038](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43386721038) success |
| E4 | SF-CLI-01-A4 | `tests/test_cli_review_repair_export.py::test_cli_review_json_flag_and_safe_output` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q -m "not integration"`（730 passed）及 GitHub Actions CI（包含 Docker 集成测试） | 源码 SHA `b8fd21f2bb872fa82d77494a8bc983b63cc682c3`；passed；CI [Run #43386721038](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43386721038) success |
| E5 | SF-CLI-01-A1 | `tests/test_cli_review_repair_export.py::test_cli_generate_test_rejects_symlinks_and_supports_json_export`、`tests/test_cli_review_repair_export.py::test_cli_review_json_flag_and_safe_output` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E6 | SF-CLI-01-A2 | `tests/test_cli_review_repair_export.py::test_cli_repair_rejects_symlinks_and_supports_json_export` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E7 | SF-CLI-01-A3 | `tests/test_cli_review_repair_export.py::test_dual_exports_reject_aliases_before_model`、`tests/test_cli_review_repair_export.py::test_cli_review_rejects_symlinks_directories_and_missing_parents` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E8 | SF-CLI-01-A4 | `tests/test_cli_review_repair_export.py::test_cli_review_json_flag_and_safe_output` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |

执行记录（Owner/状态以上表为准）：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`feat/sf-cli-01`；Worktree=`selfforge-sf-cli-01`；PR=[#69](https://github.com/liuzhouquan/selfforge-agent/pull/69)。实现 review、repair 与 generate-test 子命令安全落盘防符链穿透与 `--json` 结构化导出对齐（源码提交 `b8fd21f2bb872fa82d77494a8bc983b63cc682c3`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43386721038) 全绿）；本状态提交更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。JSON 模式自动目标诊断送 stderr；双导出提前拒绝同路径/硬链接别名。共享导出目标及实际 fd 写入前拒绝多硬链接。安全 writer 是受控写入，不宣称原子替换。

审查整合：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`review/pr68-73`；PR=#69；Merge=`9e2b42a2e8a66dd1c3f272e3eb7ee088b8e62b69`；最终受测 Source=`f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：10eff75cb3c5e35b2531e5dd2d9da17a894d2a92 / review/pr68-73 (worktree: selfforge-review-pr68-73) / 已验证源码 f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7；后续对账仅文档
已完成验收与证据 ID：SF-CLI-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 787 passed、无跳过；浏览器 smoke 使用 API 替身；未运行付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #69 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659（最终组合；各源 PR 见审查记录） / 9e2b42a2e8a66dd1c3f272e3eb7ee088b8e62b69
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #73 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动执行付费模型测量。
```

### SF-WS-07

**目标：** 在 `selfforge-agent workspaces` CLI 中对齐全部 9 个动作（`list`、`diff`、`commit`、`pr`、`revert`、`remove`、`audit`、`prune`、`storage`）的结构化 `--json` 与安全 `--output` 导出能力。当指定 `--json` 时，标准输出纯合法 JSON 且无终端人类可读提示干扰（即使同时指定 `--output`）；当指定 `--output` 时，统一经 `_validate_cli_output_target` 提前进行 fail-closed 防符链穿透校验，并通过 `_write_cli_output` 安全落盘；不带 `--json` 保持原有标准输出格式与退出码语义；修复 `workspaces storage --json --output` 下标准输出被写入提示文本污染的问题。

**范围/入口：** 仅改动 `src/selfforge_agent/cli.py`（`workspaces` 处理段）及专项测试 `tests/test_workspaces_cli_export.py`；不改动 `CodingWorkspaceManager` 底层文件系统与 Git 核心逻辑。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-WS-07-A1 | `workspaces list --json`、`diff --json`、`commit --json`、`pr --json`、`revert --json`、`remove --json`、`audit --json`、`prune --json` 与 `storage --json` 均能输出纯合法 JSON 且无终端人类文本干扰；不带 `--json` 保持原有终端可读输出与退出码 | CLI 命令行参数与 stdout 正反对照测试 | E5（本轮复验） |
| SF-WS-07-A2 | `workspaces` 全部 9 个 action 支持 `--output` 导出，统一使用 `_write_cli_output` 安全写入常规文件，写盘内容与 `--json` 数据一致 | CLI 导出文件内容与完整序列化断言 | E6（本轮复验） |
| SF-WS-07-A3 | 当传入符号链接、目录、FIFO 等非法路径或缺失父目录时，`workspaces` 提前经 `_validate_cli_output_target` 进行 fail-closed 拦截并以退出码 2 退出，不破坏原有文件，不执行后续写操作；同时修复 `workspaces storage --json --output` 下标准输出被提示文本污染的缺陷 | 负路径（symlink/directory/fifo/missing-parent）安全隔离测试 | E7（本轮复验） |
| SF-WS-07-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-WS-07-A1 | `tests/test_workspaces_cli_export.py::test_workspaces_list_and_diff_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_commit_and_revert_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_pr_and_remove_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_audit_prune_and_storage_json_and_output` | CLI workspaces 全部 9 个 action 独立子进程与 `--json` 命令行参数；验证无 flag 时人类可读终端文本、带 `--json` 输出纯结构化 JSON；[PR #70](https://github.com/liuzhouquan/selfforge-agent/pull/70) | 源码 SHA `2551136c61b7162e7eb17ab4675e9323b0a49f5a`；passed；CI [Run #43397927183](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43397927183) success |
| E2 | SF-WS-07-A2 | `tests/test_workspaces_cli_export.py::test_workspaces_list_and_diff_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_commit_and_revert_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_pr_and_remove_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_audit_prune_and_storage_json_and_output` | 统一迁移 `workspaces` 全部 9 个 action 接入 `--output` 并通过 `_write_cli_output` 安全落盘；断言落盘文件完整内容与模型 dump 一致；[PR #70](https://github.com/liuzhouquan/selfforge-agent/pull/70) | 源码 SHA `2551136c61b7162e7eb17ab4675e9323b0a49f5a`；passed；CI [Run #43397927183](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43397927183) success |
| E3 | SF-WS-07-A3 | `tests/test_workspaces_cli_export.py::test_workspaces_rejects_symlinks_directories_and_missing_parents`、`tests/test_workspaces_cli_export.py::test_workspaces_audit_prune_and_storage_json_and_output` | 针对预先存在的符号链接、目录目标、以及缺失父目录路径，提前于任何工作区改动前执行 `_validate_cli_output_target` fail-closed 拦截，断言退出码为 2 且原目标文件未被修改破坏；同时验证 `workspaces storage --output <file> --json` 下 stdout 保持纯净合法 JSON 无提示污染；[PR #70](https://github.com/liuzhouquan/selfforge-agent/pull/70) | 源码 SHA `2551136c61b7162e7eb17ab4675e9323b0a49f5a`；passed；CI [Run #43397927183](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43397927183) success |
| E4 | SF-WS-07-A4 | `tests/test_workspaces_cli_export.py::test_workspaces_list_and_diff_json_and_output` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -v tests/test_workspace*.py tests/test_ws*.py`（103 passed, 1 skipped）及 GitHub Actions CI（包含 Docker 集成测试） | 源码 SHA `2551136c61b7162e7eb17ab4675e9323b0a49f5a`；passed；CI [Run #43397927183](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43397927183) success |
| E5 | SF-WS-07-A1 | `tests/test_workspaces_cli_export.py::test_workspaces_list_and_diff_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_audit_prune_and_storage_json_and_output` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E6 | SF-WS-07-A2 | `tests/test_workspaces_cli_export.py::test_workspaces_commit_and_revert_json_and_output`、`tests/test_workspaces_cli_export.py::test_workspaces_pr_and_remove_json_and_output` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E7 | SF-WS-07-A3 | `tests/test_workspaces_cli_export.py::test_workspace_mutation_rejects_report_inside_affected_tree`、`tests/test_workspaces_cli_export.py::test_commit_report_hardlink_is_rejected_before_commit`、`tests/test_workspaces_cli_export.py::test_workspaces_rejects_symlinks_directories_and_missing_parents` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E8 | SF-WS-07-A4 | `tests/test_workspaces_cli_export.py::test_workspaces_list_and_diff_json_and_output` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |

执行记录（Owner/状态以上表为准）：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`feat/sf-ws-07`；Worktree=`selfforge-sf-ws-07`；PR=[#70](https://github.com/liuzhouquan/selfforge-agent/pull/70)。实现 workspaces 9 个动作结构化 `--json` 与安全 `--output` 导出对齐（源码提交 `2551136c61b7162e7eb17ab4675e9323b0a49f5a`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43397927183) 全绿）；本状态提交更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。mutation 报告必须置于受影响工作区外，prune 报告置于管理根目录外；多硬链接目标在动作前及 fd 写入前拒绝，防止报告覆写源码。

审查整合：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`review/pr68-73`；PR=#70；Merge=`87fe852183df180df0fb87835d2875c980026d24`；最终受测 Source=`f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：10eff75cb3c5e35b2531e5dd2d9da17a894d2a92 / review/pr68-73 (worktree: selfforge-review-pr68-73) / 已验证源码 f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7；后续对账仅文档
已完成验收与证据 ID：SF-WS-07-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 787 passed、无跳过；浏览器 smoke 使用 API 替身；未运行付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #70 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659（最终组合；各源 PR 见审查记录） / 87fe852183df180df0fb87835d2875c980026d24
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #73 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动执行付费模型测量。
```

### SF-RUN-07

**目标：** 在 `ToolRegistry` 中对齐 fail-closed 防御标准与搜索预算硬约束。在 `read_file` 中显式校验普通文件，防止 FIFO 导致进程永久挂起或目录抛出非受控异常；在 `search_code` 中严格执行 `MAX_SEARCH_BYTES` (8 MB) 扫描预算门禁并在超限时即时终止磁盘遍历，在结果尾部追加明确的截断提示（`[search limit reached: byte budget exceeded]`），同时安全容忍单文件 `OSError`（如无权访问或断链）与校验空 glob；在 `edit_file` 中增强对已存在 FIFO/目录与断链的显式拒绝；并在 `run_tests` 中拒绝非文件非目录的特殊文件（如 FIFO）。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/tools.py`（ToolRegistry `_read`、`_edit`、`_search`、`_test`）及专项测试 `tests/test_runtime_tool_safety.py`；不改动底层 Docker/Host 执行器与主循环逻辑。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-RUN-07-A1 | `read_file` 拒绝读取非普通文件（FIFO、目录、套接字等），遇到 FIFO 时不发生阻塞并显式报错 `File does not exist or is not a regular file`；遇到不存在的文件同样受控报错；正常普通文件全量与分段读取不受影响 | `tests/test_runtime_tool_safety.py` 单元与负路径隔离测试 | E5（本轮复验） |
| SF-RUN-07-A2 | `search_code` 在扫描字节总和达到或超过 `MAX_SEARCH_BYTES` (8MB) 时即刻终止遍历，并在返回文本尾部追加截断通知 `[search limit reached: byte budget exceeded]`；文件遍历计数达到 `2000` 时同样终止并追加通知 `[search limit reached: file count limit exceeded]`；文件遇到 `OSError`（如无权限读取）时安全跳过而不导致整个搜索崩溃；非法空 pattern 或空 glob 显式抛出受控错误 | `tests/test_runtime_tool_safety.py` 搜索预算门禁与容错测试 | E6（本轮复验） |
| SF-RUN-07-A3 | `edit_file` 对已存在的非普通文件（FIFO、目录）显式抛出 `Target exists and is not a regular file`；对目标路径本身为断开符号链接或父级含符号链接的情况严格拒绝并报错；`run_tests` 拒绝目标路径为非普通文件且非目录的特殊文件（如 FIFO） | `tests/test_runtime_tool_safety.py` 特殊文件与符号链接拦截测试 | E7（本轮复验） |
| SF-RUN-07-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-RUN-07-A1 | `tests/test_runtime_tool_safety.py::test_read_file_rejects_fifo_and_directory_without_blocking` | ToolRegistry `read_file` 针对 FIFO、目录及不存在文件实施 fail-closed 非普通文件防御，验证不挂起阻塞并返回受控错误；[PR #71](https://github.com/liuzhouquan/selfforge-agent/pull/71) | 源码 SHA `29811dce176570015c36f57206643308c92ed36f`；passed；CI [Run #43419734875](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43419734875) success |
| E2 | SF-RUN-07-A2 | `tests/test_runtime_tool_safety.py::test_search_code_enforces_byte_budget_and_truncation_notice`、`tests/test_runtime_tool_safety.py::test_search_code_handles_oserror_and_invalid_globs` | ToolRegistry `search_code` 执行 8 MB `MAX_SEARCH_BYTES` 预算中断，结果追加截断通知；测试单文件 `OSError` 容错跳过与非法 glob 校验；[PR #71](https://github.com/liuzhouquan/selfforge-agent/pull/71) | 源码 SHA `29811dce176570015c36f57206643308c92ed36f`；passed；CI [Run #43419734875](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43419734875) success |
| E3 | SF-RUN-07-A3 | `tests/test_runtime_tool_safety.py::test_edit_file_rejects_fifo_directory_and_broken_symlink`、`tests/test_runtime_tool_safety.py::test_run_tests_rejects_fifo_target` | ToolRegistry `edit_file` 对已存在的非普通文件（FIFO、目录）及断开符号链接拦截；`run_tests` 对非法特殊文件类型拦截；[PR #71](https://github.com/liuzhouquan/selfforge-agent/pull/71) | 源码 SHA `29811dce176570015c36f57206643308c92ed36f`；passed；CI [Run #43419734875](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43419734875) success |
| E4 | SF-RUN-07-A4 | `tests/test_runtime_tool_safety.py::test_read_file_rejects_fifo_and_directory_without_blocking` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -v tests/test_runtime*.py`（92 passed）及 GitHub Actions CI（包含 Docker 集成测试）；[PR #71](https://github.com/liuzhouquan/selfforge-agent/pull/71) | 源码 SHA `29811dce176570015c36f57206643308c92ed36f`；passed；CI [Run #43419734875](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43419734875) success |
| E5 | SF-RUN-07-A1 | `tests/test_runtime_tool_safety.py::test_read_file_rejects_fifo_and_directory_without_blocking`、`tests/test_runtime_tool_safety.py::test_read_limit_survives_growth_at_open` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E6 | SF-RUN-07-A2 | `tests/test_runtime_tool_safety.py::test_search_code_enforces_byte_budget_and_truncation_notice`、`tests/test_runtime_tool_safety.py::test_search_budget_counts_growth_after_stat`、`tests/test_runtime_tool_safety.py::test_search_code_handles_oserror_and_invalid_globs` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E7 | SF-RUN-07-A3 | `tests/test_runtime_tool_safety.py::test_edit_file_rejects_fifo_directory_and_broken_symlink`、`tests/test_runtime_tool_safety.py::test_run_tests_rejects_fifo_target` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E8 | SF-RUN-07-A4 | `tests/test_runtime_tool_safety.py::test_search_code_enforces_byte_budget_and_truncation_notice` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |

执行记录（Owner/状态以上表为准）：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`feat/sf-run-07`；Worktree=`selfforge-sf-run-07`；PR=[#71](https://github.com/liuzhouquan/selfforge-agent/pull/71)。在 `ToolRegistry` 中实现搜索预算中断与截断通知、文件读取非普通文件防御与编辑/测试边界防御（源码提交 `29811dce176570015c36f57206643308c92ed36f`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43419734875) 全绿）；本状态提交更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。读取使用锚定目录描述符、no-follow/nonblocking 普通文件打开及实际有界 byte read；whole/ranged/search 预算不能仅靠 stat 估算。

审查整合：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`review/pr68-73`；PR=#71；Merge=`60cce4d6cd2f16cb1266ad1f1c546ba69671de3b`；最终受测 Source=`f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：10eff75cb3c5e35b2531e5dd2d9da17a894d2a92 / review/pr68-73 (worktree: selfforge-review-pr68-73) / 已验证源码 f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7；后续对账仅文档
已完成验收与证据 ID：SF-RUN-07-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 787 passed、无跳过；浏览器 smoke 使用 API 替身；未运行付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #71 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659（最终组合；各源 PR 见审查记录） / 60cce4d6cd2f16cb1266ad1f1c546ba69671de3b
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #73 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动执行付费模型测量。
```

### SF-WS-08

**目标：** 在 `workspace_diff` 与 `delivery` 中贯彻 fail-closed 与无特权安全隔离原则。在 `workspace_diff` 中，对未跟踪文件（untracked）遍历其所有父级目录，若任何一级为符号链接或解析后脱离工作区/指向受保护路径，则安全省略（标记 `[Untracked non-text, symlink, or large file omitted]`），防止通过外部符号链接目录泄露工作区外部敏感数据；对未跟踪文件读取时捕获 `(UnicodeError, OSError)`（包括 `PermissionError`），输出 `[Untracked unreadable file omitted]` 而非崩溃。在 `delivery.py` 的 `commit_workspace` 中过滤未跟踪的非普通文件（FIFO、设备、套接字等）以及指向工作区外的符号链接；在 `revert_workspace` 中，清理未跟踪项时确保断开的符号链接和目录符号链接能够通过 `target.unlink(missing_ok=True)` 彻底清理，防止残留。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/workspace_diff.py`、`src/selfforge_agent/runtime/delivery.py` 及专项测试 `tests/test_workspace_diff_safety.py`；不改动 `CodingWorkspace` 核心会话生命周期或 Git 命令层。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-WS-08-A1 | `workspace_diff` 遇到处于符号链接目录下的未跟踪文件，或解析后脱离工作区/命中受保护路径的未跟踪文件，输出 `[Untracked non-text, symlink, or large file omitted]`；不泄露目标文件内容，不发生未受控异常 | `tests/test_workspace_diff_safety.py` 符号链接与边界测试 | E5（本轮复验） |
| SF-WS-08-A2 | `workspace_diff` 读取未跟踪文件时若遇到不可读权限（`PermissionError`）或其他 `OSError`，安全降级输出 `[Untracked unreadable file omitted]`；遇到非 UTF-8 编码文件输出 `[Untracked non-UTF-8 file omitted]`；普通正常未跟踪文件输出标准 unified diff | `tests/test_workspace_diff_safety.py` 不可读文件与编码容错测试 | E6（本轮复验） |
| SF-WS-08-A3 | `commit_workspace` 过滤未跟踪的特殊设备/FIFO/套接字及指向工作区外的符号链接，不将其纳入暂存区或提交中；`revert_workspace` 能够彻底移除未跟踪的断链符号链接及指向外部目录的符号链接 | `tests/test_workspace_diff_safety.py` delivery 提交与清理隔离测试 | E7（本轮复验） |
| SF-WS-08-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-WS-08-A1 | `tests/test_workspace_diff_safety.py::test_workspace_diff_omits_untracked_files_under_symlinked_directory` | 本地 `pytest -v tests/test_workspace_diff_safety.py`；GitHub CI `Unit tests` | `5757ef1779e78b7b2d2dbded2a551f9062b17238`；passed；CI [Run #43439068333](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43439068333) |
| E2 | SF-WS-08-A2 | `tests/test_workspace_diff_safety.py::test_workspace_diff_handles_unreadable_and_oserror_files` | 本地 `pytest -v tests/test_workspace_diff_safety.py`；GitHub CI `Unit tests` | `5757ef1779e78b7b2d2dbded2a551f9062b17238`；passed；CI [Run #43439068333](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43439068333) |
| E3 | SF-WS-08-A3 | `tests/test_workspace_diff_safety.py::test_revert_workspace_removes_untracked_broken_and_directory_symlinks`、`tests/test_workspace_diff_safety.py::test_commit_workspace_filters_untracked_fifos` | 本地 `pytest -v tests/test_workspace_diff_safety.py`；GitHub CI `Unit tests` | `5757ef1779e78b7b2d2dbded2a551f9062b17238`；passed；CI [Run #43439068333](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43439068333) |
| E4 | SF-WS-08-A4 | `tests/test_workspace_diff_safety.py::test_workspace_diff_omits_untracked_files_under_symlinked_directory` | 本地 `ruff check .`、`python scripts/check_sdd.py`、全量单元测试 730 passed；GitHub CI Lint / SDD contract check / Unit tests 730 passed / Docker integration tests 15 passed | `5757ef1779e78b7b2d2dbded2a551f9062b17238`；passed；CI [Run #43439068333](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43439068333) 全绿 |
| E5 | SF-WS-08-A1 | `tests/test_workspace_diff_safety.py::test_workspace_diff_omits_untracked_files_under_symlinked_directory` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E6 | SF-WS-08-A2 | `tests/test_workspace_diff_safety.py::test_workspace_diff_handles_unreadable_and_oserror_files` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E7 | SF-WS-08-A3 | `tests/test_workspace_diff_safety.py::test_commit_rejects_prestaged_external_symlink`、`tests/test_workspace_diff_safety.py::test_commit_filters_looping_symlink_without_losing_normal_changes`、`tests/test_workspace_diff_safety.py::test_commit_workspace_filters_untracked_fifos`、`tests/test_workspace_diff_safety.py::test_revert_workspace_removes_untracked_broken_and_directory_symlinks` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E8 | SF-WS-08-A4 | `tests/test_workspace_diff_safety.py::test_commit_workspace_filters_untracked_fifos` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |

执行记录（Owner/状态以上表为准）：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`feat/sf-ws-08`；Worktree=`selfforge-sf-ws-08`；PR=[#72](https://github.com/liuzhouquan/selfforge-agent/pull/72)；Source=`5757ef1779e78b7b2d2dbded2a551f9062b17238`。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。危险预暂存项明确拒绝并保留用户 index；检查 staged symlink blob，不能只看当前文件。循环 symlink 安全省略，不阻断普通修复提交。

审查整合：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`review/pr68-73`；PR=#72；Merge=`1f1518a4a30e5d16c3ff369ecf05c5759ba39b87`；最终受测 Source=`f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：10eff75cb3c5e35b2531e5dd2d9da17a894d2a92 / review/pr68-73 (worktree: selfforge-review-pr68-73) / 已验证源码 f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7；后续对账仅文档
已完成验收与证据 ID：SF-WS-08-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 787 passed、无跳过；浏览器 smoke 使用 API 替身；未运行付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #72 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659（最终组合；各源 PR 见审查记录） / 1f1518a4a30e5d16c3ff369ecf05c5759ba39b87
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #73 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动执行付费模型测量。
```

### SF-VERIF-01

**目标：** 在 `selfforge_agent.verification` 核心验证模块中贯彻 fail-closed 与无特权安全隔离原则。在 `junit.py` 的 `parse_junit` 中增强对畸形 XML 的受控异常收敛与 DoS 防御，捕获 `(ET.ParseError, ValueError, RecursionError, TypeError)`，防止深层标签嵌套导致进程崩溃，并显式校验用例数量上限 `MAX_TEST_COUNT` (100,000)，阻止超大计数攻击；在 `runner.py` 的 `_test_target` 中严格校验 `timeout` 为有限正数且不超过 3600 秒（阻断 `NaN`/`inf`/非正数），校验仓库必须为已存在的真实目录，校验测试目标路径非空且不包含受保护路径（如 `.git`、`.env` 等），严格拒绝非普通文件（如 FIFO、设备文件等）以防执行器在 `open()` 上永久挂起；在 `TestRunner` 中通过 `_read_junit_file` 采用 `O_NOFOLLOW | O_NONBLOCK` 安全读取测试生成的 JUnit 工件，杜绝符号链接穿透与特殊文件阻塞。

**范围/入口：** 仅改动 `src/selfforge_agent/verification/junit.py`、`src/selfforge_agent/verification/runner.py` 及专项测试 `tests/test_verification_runner_safety.py`；不改动外部执行器生命周期或上层 Coding/Review 调度。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-VERIF-01-A1 | `parse_junit` 解析深层嵌套 XML 时捕获 `RecursionError`/`TypeError`，安全降级返回包含 `error` 提示的 `JUnitEvidence` 而不发生非受控异常；用例计数超出上限（100,000）或包含畸形超大计数时显式报错拒绝 | `tests/test_verification_runner_safety.py` 递归与用例计数边界测试 | E5（本轮复验） |
| SF-VERIF-01-A2 | `_test_target` 校验 `timeout`，遇到 `NaN`、`inf`、非正数或超过 3600 秒时严格抛出 `ValueError`；遇到不存在或非目录的仓库路径、空测试路径或受保护组件（`.git`、`.env` 等）时显式抛出 `ValueError` | `tests/test_verification_runner_safety.py` 目标参数与边界防护测试 | E6（本轮复验） |
| SF-VERIF-01-A3 | `_test_target` 及 `TestRunner.run` 遇到 FIFO 等非普通文件时显式拒绝并报错 `test_path must be a regular file or directory`，不发生进程挂起；`_read_junit_file` 拒绝读取符号链接、断链、目录或 FIFO 工件 | `tests/test_verification_runner_safety.py` 特殊文件与符号链接工件隔离测试 | E7（本轮复验） |
| SF-VERIF-01-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-VERIF-01-A1 | `tests/test_verification_runner_safety.py::test_junit_parser_handles_deep_recursion_gracefully`、`tests/test_verification_runner_safety.py::test_junit_parser_rejects_excessive_test_counts` | 本地 `pytest -v tests/test_verification_runner_safety.py`；GitHub CI `Unit tests` | `d80f97a000fc98b0498fb5ea23746ca6cc0cb386`；passed；CI [Run #43536759419](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43536759419) |
| E2 | SF-VERIF-01-A2 | `tests/test_verification_runner_safety.py::test_test_target_rejects_non_finite_and_out_of_bounds_timeouts`、`tests/test_verification_runner_safety.py::test_test_target_rejects_missing_or_non_dir_repository_and_blank_test_path` | 本地 `pytest -v tests/test_verification_runner_safety.py`；GitHub CI `Unit tests` | `d80f97a000fc98b0498fb5ea23746ca6cc0cb386`；passed；CI [Run #43536759419](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43536759419) |
| E3 | SF-VERIF-01-A3 | `tests/test_verification_runner_safety.py::test_test_target_rejects_non_regular_test_files_such_as_fifos`、`tests/test_verification_runner_safety.py::test_read_junit_file_rejects_symlinks_fifos_and_directories` | 本地 `pytest -v tests/test_verification_runner_safety.py`；GitHub CI `Unit tests` | `d80f97a000fc98b0498fb5ea23746ca6cc0cb386`；passed；CI [Run #43536759419](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43536759419) |
| E4 | SF-VERIF-01-A4 | `tests/test_verification_runner_safety.py::test_read_junit_file_rejects_symlinks_fifos_and_directories` | 本地 `ruff check .`、`python scripts/check_sdd.py`、全量单元测试 732 passed；GitHub CI Lint / SDD contract check / Unit tests 732 passed / Docker integration tests 15 passed | `d80f97a000fc98b0498fb5ea23746ca6cc0cb386`；passed；CI [Run #43536759419](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43536759419) 全绿 |
| E5 | SF-VERIF-01-A1 | `tests/test_verification_runner_safety.py::test_junit_total_count_is_bounded_across_suites`、`tests/test_verification_runner_safety.py::test_junit_parser_handles_deep_recursion_gracefully`、`tests/test_verification_runner_safety.py::test_junit_parser_rejects_excessive_test_counts` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E6 | SF-VERIF-01-A2 | `tests/test_verification_runner_safety.py::test_runner_rejects_alias_into_protected_directory`、`tests/test_verification_runner_safety.py::test_test_target_rejects_non_finite_and_out_of_bounds_timeouts` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E7 | SF-VERIF-01-A3 | `tests/test_verification_runner_safety.py::test_test_target_rejects_non_regular_test_files_such_as_fifos`、`tests/test_verification_runner_safety.py::test_read_junit_file_rejects_symlinks_fifos_and_directories` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |
| E8 | SF-VERIF-01-A4 | `tests/test_verification_runner_safety.py::test_junit_total_count_is_bounded_across_suites` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；UI Chromium 使用明确 API 替身；Ruff/SDD/Node/空白检查通过 | `f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`；passed；完整组合 787 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659)；[审查记录](./review-pr68-73.md) |

执行记录（Owner/状态以上表为准）：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`feat/sf-verif-01`；Worktree=`selfforge-sf-verif-01`；PR=[#73](https://github.com/liuzhouquan/selfforge-agent/pull/73)；Source=`d80f97a000fc98b0498fb5ea23746ca6cc0cb386`。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。JUnit 100,000 用例上限同时适用于所有 suite 的累计计数；测试目标解析后再次检查仓库相对受保护组件，不让别名绕过。

审查整合：Base=`10eff75cb3c5e35b2531e5dd2d9da17a894d2a92`；Branch=`review/pr68-73`；PR=#73；Merge=`af31537e72f39aeccf98fb9149edb51787d4bcaa`；最终受测 Source=`f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：10eff75cb3c5e35b2531e5dd2d9da17a894d2a92 / review/pr68-73 (worktree: selfforge-review-pr68-73) / 已验证源码 f2296b4511e5b0e2e8d4acf98c8d23fec48e19d7；后续对账仅文档
已完成验收与证据 ID：SF-VERIF-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 787 passed、无跳过；浏览器 smoke 使用 API 替身；未运行付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #73 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43552476659（最终组合；各源 PR 见审查记录） / af31537e72f39aeccf98fb9149edb51787d4bcaa
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #73 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动执行付费模型测量。
```

### SF-ENV-08

**目标：** 在依赖输入解析与离线镜像构建准备阶段贯彻 fail-closed 与安全规范化准则。在 `lockfiles.py` 中强化环境与平台条件标记的识别（`markers`、`marker`、`resolution-markers`、`platform`、`sys_platform`、`os_name` 等），非字符串标记抛出 `Invalid package marker in lock file`，任何非通配（`*`）的平台或环境条件显式抛出 `Conditional markers require a target-aware export; cannot flatten this lock`，杜绝将目标特异性依赖静默扁平化为跨平台通用导出；统一在数据入口实施 PEP 503 包名规范化（`re.sub(r"[-_.]+", "-", name).lower()`），确保下游构建器与校验器名称完全对齐；在 `images.py` 中通过 `_read` 采用 `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` 与 `fstat(fd)` 判定，防止符号链接穿透、目录及 FIFO 特殊文件引起的阻塞挂起或 TOCTOU 竞争。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/lockfiles.py`、`src/selfforge_agent/runtime/images.py` 及专项测试 `tests/test_lockfile_security_hardening.py`；不破坏既有离线准备流程与构建接口。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-ENV-08-A1 | Poetry 与 Pipfile 锁文件中声明了 `platform`、`sys_platform` 或 `os_name` 限制时，导出严格抛出 `ValueError("Conditional markers require a target-aware export; cannot flatten this lock")`；非字符串标记抛出 `ValueError("Invalid package marker in lock file")` | `tests/test_lockfile_security_hardening.py` 平台与条件标记拦截测试 | E5（本轮复验） |
| SF-ENV-08-A2 | 结构化锁文件（Poetry/Pipfile）与 requirements.txt 导出时，包名统一按 PEP 503 规范化（全小写、分隔符连字符规范化）；`parse_locked_requirements` 产出规范化包名 | `tests/test_lockfile_security_hardening.py` 包名规范化测试 | E6（本轮复验） |
| SF-ENV-08-A3 | `images._read` 使用 `O_NOFOLLOW \| O_NONBLOCK` 打开并校验 `S_ISREG`，遇 FIFO、目录、断链或符号链接立刻 fail-closed 抛出 `ValueError`，杜绝读特殊文件挂起；`scan_wheelhouse` 安全隔离特殊文件 | `tests/test_lockfile_security_hardening.py` 特殊文件与符号链接防挂起测试 | E7（本轮复验） |
| SF-ENV-08-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-ENV-08-A1 | `tests/test_lockfile_security_hardening.py::test_poetry_rejects_platform_conditional_markers`、`tests/test_lockfile_security_hardening.py::test_pipfile_rejects_platform_and_sys_platform`、`tests/test_lockfile_security_hardening.py::test_lockfile_rejects_non_string_markers` | 本地 `pytest -v tests/test_lockfile_security_hardening.py`；GitHub CI `Unit tests` | `7cba88a5a7b5193b69a085c468066eb1e0dbdba6`；passed；CI [Run #43571516639](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43571516639) |
| E2 | SF-ENV-08-A2 | `tests/test_lockfile_security_hardening.py::test_lockfile_pep503_package_name_normalization` | 本地 `pytest -v tests/test_lockfile_security_hardening.py`；GitHub CI `Unit tests` | `7cba88a5a7b5193b69a085c468066eb1e0dbdba6`；passed；CI [Run #43571516639](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43571516639) |
| E3 | SF-ENV-08-A3 | `tests/test_lockfile_security_hardening.py::test_images_read_rejects_fifos_without_blocking`、`tests/test_lockfile_security_hardening.py::test_images_read_rejects_symlinks_and_dirs`、`tests/test_lockfile_security_hardening.py::test_scan_wheelhouse_with_fifo_and_symlink` | 本地 `pytest -v tests/test_lockfile_security_hardening.py`；GitHub CI `Unit tests` | `7cba88a5a7b5193b69a085c468066eb1e0dbdba6`；passed；CI [Run #43571516639](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43571516639) |
| E4 | SF-ENV-08-A4 | `tests/test_lockfile_security_hardening.py::test_images_read_normal_and_limit` | 本地 `ruff check .`、`python scripts/check_sdd.py`、全量单元测试 780 passed；GitHub CI Lint / SDD contract check / Unit tests 780 passed / Docker integration tests 15 passed | `7cba88a5a7b5193b69a085c468066eb1e0dbdba6`；passed；CI [Run #43571516639](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43571516639) 全绿 |
| E5 | SF-ENV-08-A1 | `tests/test_lockfile_security_hardening.py::test_poetry_rejects_platform_conditional_markers`、`tests/test_lockfile_security_hardening.py::test_pipfile_rejects_platform_and_sys_platform`、`tests/test_lockfile_security_hardening.py::test_lockfile_rejects_non_string_markers` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E6 | SF-ENV-08-A2 | `tests/test_lockfile_security_hardening.py::test_lockfile_pep503_package_name_normalization` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E7 | SF-ENV-08-A3 | `tests/test_lockfile_security_hardening.py::test_images_read_rejects_fifos_without_blocking`、`tests/test_lockfile_security_hardening.py::test_images_read_rejects_symlinks_and_dirs`、`tests/test_lockfile_security_hardening.py::test_scan_wheelhouse_with_fifo_and_symlink` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E8 | SF-ENV-08-A4 | `tests/test_lockfile_security_hardening.py::test_images_read_normal_and_limit` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |

原分支历史执行记录（当前状态以下方审查交接为准）：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`feat/sf-env-08`；Worktree=`selfforge-sf-env-08`；PR=[#74](https://github.com/liuzhouquan/selfforge-agent/pull/74)；Source=`7cba88a5a7b5193b69a085c468066eb1e0dbdba6`。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的数量不累加。两轴未发现确定阻断，保留原实现和原准确 head；规范名称、平台条件及 no-follow 非阻塞读取通过相关兼容验证。

审查整合：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`review/pr74-78`；PR=#74；Merge=`b14b297a429d5e78a8347347e1705a8528add509`；最终受测 Source=`dc6ff236e46c59e7e3fb7ed772947093ca3cb698`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：68e56fb3ffb61439b00a90a81586d983af33a601 / review/pr74-78 (worktree: selfforge-review-pr74-78) / 已验证源码 dc6ff236e46c59e7e3fb7ed772947093ca3cb698；后续对账仅文档
已完成验收与证据 ID：SF-ENV-08-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 878 passed、无跳过；模型只用脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #74 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896（最终组合；各源 PR 见审查记录） / b14b297a429d5e78a8347347e1705a8528add509
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #78 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-EVAL-10

**目标：** 在基准测试与评测执行器中贯彻 fail-closed 与防挂起隔离准则。在 `benchmark._run_once` 中强化未绑定会话保护，初始化 `session = None` 与全量度量变量，捕获环境或检出异常并受控映射为 `harness_error:<ExcName>`，杜绝未绑定 `session` 导致的 `UnboundLocalError`；在 `load_suite` 与 `_preflight` 中实施有界非阻塞常规文件安全读取，设定清单文件上限（2MB）与 holdout 上限（1MB），对 FIFO、特殊文件及符号链接坚决拒绝；在 `_verification_copy` 中引入特殊文件忽略过滤器，安全剔除 FIFO 命名管道、套接字与设备节点，杜绝 `copytree` 在外部遗留文件上挂起；在 `_install_holdout` 中通过 `O_CREAT | O_EXCL | O_NOFOLLOW` 实行原子安装并校验目录符号链接，防止测试文件覆写与符链穿透；在 `_tool_trace` 中增加事件字典类型检查，提升遥测安全性。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/benchmark.py` 及专项测试 `tests/test_benchmark_hardening.py`；不破坏既有评测指标、清单格式与测试执行流程。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-EVAL-10-A1 | `_run_once` 在 `_checkout` 或初始化失败时受控捕获并返回 `harness_error`，不抛出 `UnboundLocalError: session`，度量与成本安全回退 | `tests/test_benchmark_hardening.py` 未初始化检出异常防御测试 | E5（本轮复验） |
| SF-EVAL-10-A2 | `load_suite` 与 `_preflight` 读取清单与 holdout 时，对 FIFO、目录、特殊文件或符号链接 fail-closed 抛出 `ValueError`，且严格限制清单（2MB）与测试（1MB）文件大小 | `tests/test_benchmark_hardening.py` 非阻塞安全读取与上限测试 | E6（本轮复验） |
| SF-EVAL-10-A3 | `_verification_copy` 遇到工作区遗留的 FIFO 等特殊文件时自动安全忽略并完成常规拷贝，不阻塞进程；`_install_holdout` 遇到目标符号链接原子拒绝 | `tests/test_benchmark_hardening.py` 特殊文件防挂起与原子安装测试 | E7（本轮复验） |
| SF-EVAL-10-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-EVAL-10-A1 | `tests/test_benchmark_hardening.py::test_run_once_handles_checkout_failure_without_unbound_session` | 本地 `pytest -v tests/test_benchmark_hardening.py`；GitHub Actions `Unit tests` | `5001b25a344ca33de2c02fdebfbdf416dea361c6`；passed；CI [Run #43580248549](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43580248549) |
| E2 | SF-EVAL-10-A2 | `tests/test_benchmark_hardening.py::test_load_suite_rejects_fifo_manifest`、`tests/test_benchmark_hardening.py::test_load_suite_rejects_symlink_manifest`、`tests/test_benchmark_hardening.py::test_load_suite_enforces_manifest_size_limit` | 本地 `pytest -v tests/test_benchmark_hardening.py`；GitHub Actions `Unit tests` | `5001b25a344ca33de2c02fdebfbdf416dea361c6`；passed；CI [Run #43580248549](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43580248549) |
| E3 | SF-EVAL-10-A3 | `tests/test_benchmark_hardening.py::test_verification_copy_safely_skips_fifos`、`tests/test_benchmark_hardening.py::test_install_holdout_rejects_symlink_target` | 本地 `pytest -v tests/test_benchmark_hardening.py`；GitHub Actions `Unit tests` | `5001b25a344ca33de2c02fdebfbdf416dea361c6`；passed；CI [Run #43580248549](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43580248549) |
| E4 | SF-EVAL-10-A4 | `tests/test_benchmark_hardening.py::test_tool_trace_handles_malformed_events_gracefully` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q`（779 passed）；GitHub Actions Lint / SDD contract check / Unit tests (779 passed) / Docker integration tests (15 passed) | `5001b25a344ca33de2c02fdebfbdf416dea361c6`；passed；CI [Run #43580248549](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43580248549) 全绿 |
| E5 | SF-EVAL-10-A1 | `tests/test_benchmark_hardening.py::test_run_once_handles_checkout_failure_without_unbound_session`、`tests/test_benchmark_hardening.py::test_metrics_error_after_session_creation_returns_harness_outcome`、`tests/test_benchmark_hardening.py::test_cost_is_captured_before_temporary_session_cleanup` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E6 | SF-EVAL-10-A2 | `tests/test_benchmark_hardening.py::test_load_suite_rejects_fifo_manifest`、`tests/test_benchmark_hardening.py::test_load_suite_enforces_manifest_size_limit`、`tests/test_benchmark_hardening.py::test_frozen_inputs_obey_the_same_size_limits` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E7 | SF-EVAL-10-A3 | `tests/test_benchmark_hardening.py::test_holdout_install_writes_complete_frozen_bytes`、`tests/test_benchmark_hardening.py::test_holdout_parent_swap_cannot_write_outside`、`tests/test_benchmark_hardening.py::test_verification_copy_safely_skips_fifos` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E8 | SF-EVAL-10-A4 | `tests/test_benchmark_hardening.py::test_tool_trace_handles_malformed_events_gracefully` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |

原分支历史执行记录（当前状态以下方审查交接为准）：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`feat/sf-eval-10`；Worktree=`selfforge-sf-eval-10`；PR=[#75](https://github.com/liuzhouquan/selfforge-agent/pull/75)；Source=`5001b25a344ca33de2c02fdebfbdf416dea361c6`。实现 benchmark 执行器与清单/holdout 读取防挂起、未绑定会话保护与原子工件安装（源码提交 `5001b25a344ca33de2c02fdebfbdf416dea361c6`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43580248549) 全绿）；本状态提交仅更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的数量不累加。holdout 安装使用父目录 fd 和完整字节写入；冻结输入也受相同大小上限约束；损坏遥测只返回分类 harness_error；有效成本在会话清理前快照，脚本测试费率不是实际计费。

审查整合：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`review/pr74-78`；PR=#75；Merge=`446c94eb76520118dae889d2c8b5b2f3093c69e8`；最终受测 Source=`dc6ff236e46c59e7e3fb7ed772947093ca3cb698`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：68e56fb3ffb61439b00a90a81586d983af33a601 / review/pr74-78 (worktree: selfforge-review-pr74-78) / 已验证源码 dc6ff236e46c59e7e3fb7ed772947093ca3cb698；后续对账仅文档
已完成验收与证据 ID：SF-EVAL-10-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 878 passed、无跳过；模型只用脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #75 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896（最终组合；各源 PR 见审查记录） / 446c94eb76520118dae889d2c8b5b2f3093c69e8
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #78 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-TEST-05

**目标：** 在差分测试生成与因果推断执行流中贯彻 fail-closed 与无特权安全隔离准则。在 `test_generation.py` 中引入 `_write_test_artifact`，以 `O_CREAT | O_EXCL | O_NOFOLLOW` 模式原子安装候选测试工件并强校验目录符号链接，严防目标符号链接穿透与文件覆写漏洞；在 `_observation` 中采用 `O_NOFOLLOW | O_NONBLOCK` 非阻塞安全读取常规文件，对符号链接与 FIFO 管道安全跳过，防止进程永久挂起；在 `_parse_test` 中捕获 `(RecursionError, MemoryError, TypeError)` 等畸形异常，严防深层嵌套 AST 导致服务崩溃，并禁止顶层非声明可执行语句以防模块导入时执行恶意副作用；在 `causal_investigation.py` 的 `investigate_causality` 中对不可信测试输出实施有界切片防护（单流 64KB，总长 128KB），并强校验 `target.line_number` 为有限正数；在 `risk_prioritization.py` 中强校验 `top_k` 为合法正整数（1–100），消除负切片漏洞。

**范围/入口：** 仅改动 `src/selfforge_agent/coding/test_generation.py`、`src/selfforge_agent/coding/causal_investigation.py`、`src/selfforge_agent/coding/risk_prioritization.py` 及专项测试 `tests/test_test_generation_safety.py`；保持既有差分评估、模型交互契约与回归测试接口完全兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-TEST-05-A1 | `_write_test_artifact` 及 `DifferentialTestGenerator.run` 安装测试工件时，遇到目标符号链接、已存在文件或逃逸父目录时显式拒绝，并以原子排他方式写盘 | `tests/test_test_generation_safety.py` 测试工件防符链与原子写盘测试 | E5（本轮复验） |
| SF-TEST-05-A2 | `_parse_test` 解析模型测试代码时受控捕获 `RecursionError`/`MemoryError`/`TypeError`，并显式拒绝顶层非声明可执行语句与无断言函数 | `tests/test_test_generation_safety.py` AST 安全边界与递归防御测试 | E6（本轮复验） |
| SF-TEST-05-A3 | `_observation` 安全忽略符号链接与 FIFO 特殊文件，不阻塞进程；`investigate_causality` 对超大日志有界切片并在非法行号时安全降级；`prioritize_risks` 校验 `top_k` | `tests/test_test_generation_safety.py` 非阻塞观察与因果推断边界测试 | E7（本轮复验） |
| SF-TEST-05-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-TEST-05-A1 | `tests/test_test_generation_safety.py::test_write_test_artifact_rejects_symlink_and_existing_files`、`tests/test_test_generation_safety.py::test_write_test_artifact_rejects_escaping_parent_directory`、`tests/test_test_generation_safety.py::test_write_test_artifact_successfully_creates_file` | 本地 `pytest -v tests/test_test_generation_safety.py`；GitHub Actions `Unit tests` | `9ac02f43bb2fffcb3383a4fbefad2cb70f2f3f9e`；passed；CI [Run #43584658362](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43584658362) |
| E2 | SF-TEST-05-A2 | `tests/test_test_generation_safety.py::test_parse_test_rejects_top_level_executable_statements`、`tests/test_test_generation_safety.py::test_parse_test_catches_uncontrolled_exceptions_gracefully` | 本地 `pytest -v tests/test_test_generation_safety.py`；GitHub Actions `Unit tests` | `9ac02f43bb2fffcb3383a4fbefad2cb70f2f3f9e`；passed；CI [Run #43584658362](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43584658362) |
| E3 | SF-TEST-05-A3 | `tests/test_test_generation_safety.py::test_observation_skips_symlinks_and_fifos`、`tests/test_test_generation_safety.py::test_investigate_causality_bounds_massive_failure_output`、`tests/test_test_generation_safety.py::test_investigate_causality_rejects_invalid_line_number`、`tests/test_test_generation_safety.py::test_prioritize_risks_validates_top_k` | 本地 `pytest -v tests/test_test_generation_safety.py`；GitHub Actions `Unit tests` | `9ac02f43bb2fffcb3383a4fbefad2cb70f2f3f9e`；passed；CI [Run #43584658362](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43584658362) |
| E4 | SF-TEST-05-A4 | `tests/test_test_generation_safety.py::test_remaining_budget_rejects_non_finite_deadline` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q`（789 passed）；GitHub Actions Lint / SDD contract check / Unit tests (789 passed) / Docker integration tests (15 passed) | `9ac02f43bb2fffcb3383a4fbefad2cb70f2f3f9e`；passed；CI [Run #43584658362](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43584658362) 全绿 |
| E5 | SF-TEST-05-A1 | `tests/test_test_generation_safety.py::test_write_test_artifact_rejects_symlink_and_existing_files`、`tests/test_test_generation_safety.py::test_artifact_parent_swap_cannot_write_outside` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E6 | SF-TEST-05-A2 | `tests/test_test_generation_safety.py::test_parse_rejects_explicit_execution_hidden_in_declarations`、`tests/test_test_generation_safety.py::test_parse_preserves_literal_constants_and_pytest_decorators`、`tests/test_test_generation_safety.py::test_parse_test_catches_uncontrolled_exceptions_gracefully` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E7 | SF-TEST-05-A3 | `tests/test_test_generation_safety.py::test_observation_cannot_reveal_protected_source_through_directory_alias`、`tests/test_test_generation_safety.py::test_observation_skips_looping_parent_symlink`、`tests/test_test_generation_safety.py::test_causality_handles_arbitrary_positive_integer_without_float_conversion`、`tests/test_test_generation_safety.py::test_risk_limit_rejects_boolean`、`tests/test_test_generation_safety.py::test_investigate_causality_bounds_massive_failure_output` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E8 | SF-TEST-05-A4 | `tests/test_test_generation_safety.py::test_remaining_budget_rejects_non_finite_deadline` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |

原分支历史执行记录（当前状态以下方审查交接为准）：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`feat/sf-test-05`；Worktree=`selfforge-sf-test-05`；PR=[#76](https://github.com/liuzhouquan/selfforge-agent/pull/76)；Source=`9ac02f43bb2fffcb3383a4fbefad2cb70f2f3f9e`。实现差分测试生成工件隔离、AST 递归异常防御与有界因果推断（源码提交 `9ac02f43bb2fffcb3383a4fbefad2cb70f2f3f9e`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43584658362) 全绿）；本状态提交仅更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的数量不累加。声明筛查覆盖赋值、默认值、类体和装饰器内的显式执行，保留字面量和合理 pytest 声明；它不是 Python 沙箱。观察读取/工件安装逐级 fd no-follow，循环父路径安全略过；因果文本先限前缀再编码。

审查整合：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`review/pr74-78`；PR=#76；Merge=`1ba9512f76cec1c9496a2bfa6cf75b53b2b6a53e`；最终受测 Source=`dc6ff236e46c59e7e3fb7ed772947093ca3cb698`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：68e56fb3ffb61439b00a90a81586d983af33a601 / review/pr74-78 (worktree: selfforge-review-pr74-78) / 已验证源码 dc6ff236e46c59e7e3fb7ed772947093ca3cb698；后续对账仅文档
已完成验收与证据 ID：SF-TEST-05-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 878 passed、无跳过；模型只用脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #76 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896（最终组合；各源 PR 见审查记录） / 1ba9512f76cec1c9496a2bfa6cf75b53b2b6a53e
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #78 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-API-01

**目标：** 在 Web Coding API 与后台作业管理模块中贯彻输入防御（Fail-Closed）、跨层规范化与未受控异常收敛原则。在 `app.py` 中引入请求体大小限制中间件（`enforce_payload_limit`），对超过 2MB 的请求直接拒绝并返回 HTTP 413，对非法/负数 `Content-Length` 返回 HTTP 400；为所有 `{session_id}` 路径参数路由（`remove`/`revert`/`diff`/`commit`/`pr`/`validate-regression`/`regression`）增加统一字符集与长度校验 `_validate_session_id`（仅允许 `[A-Za-z0-9_-]{1,64}`），阻断路径遍历攻击并显式返回 HTTP 400；为仓库路径路由增加 `_validate_repository_dir` 预检防御并对负数 `quota_bytes` 显式校验拦截；将回归报告解析异常从 HTTP 500 收敛映射为受控的 HTTP 422；在 `coding_jobs.py` 中加固 `DurableCancel`，拒绝符号链接标记并使用 `O_NOFOLLOW` 打开；在 `_load_record` 与 `_write_record` 中增加符号链接排斥、FIFO/设备文件非阻塞校验与 64KB 读取上限；在 `list_jobs` 中安全容错损坏断链；在 `CodingJobManager.__init__` 中校验正整数限制，杜绝 NaN/inf 逃逸。

**范围/入口：** 改动 `src/selfforge_agent/api/app.py`、`src/selfforge_agent/api/coding_jobs.py`，并新增专项测试 `tests/test_api_security.py`；不破坏既有前端交互与 Web Coding 工作台功能。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-API-01-A1 | API 接收请求体时校验 Content-Length，超过 2MB 时直接拦截并返回 HTTP 413（`Request body too large`）；负数或非整数 Content-Length 返回 HTTP 400 | `tests/test_api_security.py` 请求体大小限制与非法头测试 | E5（本轮复验） |
| SF-API-01-A2 | `{session_id}` 路由参数校验仅允许合法字符（`[A-Za-z0-9_-]{1,64}`），遇到路径遍历（如 `..`）、特殊字符或超长时显式返回 HTTP 400 | `tests/test_api_security.py` 会话路径遍历与语法防御测试 | E6（本轮复验） |
| SF-API-01-A3 | `DurableCancel` 拒绝符号链接取消标记；`_load_record` 与 `_write_record` 防御符号链接穿透、FIFO 挂起与超过 64KB 超大记录；`list_jobs` 容错断链；`CodingJobManager` 校验正整数配置 | `tests/test_api_security.py` 取消标记与作业记录防御测试 | E7（本轮复验） |
| SF-API-01-A4 | 仓库路径预检拒绝非仓库/非目录并拦截负数 `quota_bytes`；损坏回归记录受控映射为 HTTP 422 而非 500；`ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-API-01-A1 | `tests/test_api_security.py::test_payload_limit_rejects_oversized_requests`、`tests/test_api_security.py::test_payload_limit_rejects_malformed_content_length` | 本地 `pytest -v tests/test_api_security.py`；GitHub Actions `Unit tests` | `0a681cbf068a0a9907c0823c91df087b2f42a0fe`；passed；CI [Run #43589885389](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43589885389) |
| E2 | SF-API-01-A2 | `tests/test_api_security.py::test_session_id_validation_rejects_path_traversal_and_malformed` | 本地 `pytest -v tests/test_api_security.py`；GitHub Actions `Unit tests` | `0a681cbf068a0a9907c0823c91df087b2f42a0fe`；passed；CI [Run #43589885389](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43589885389) |
| E3 | SF-API-01-A3 | `tests/test_api_security.py::test_durable_cancel_rejects_symlink`、`tests/test_api_security.py::test_load_record_rejects_symlinks_fifos_and_oversized_files`、`tests/test_api_security.py::test_write_record_rejects_symlink_target`、`tests/test_api_security.py::test_list_jobs_tolerates_broken_symlinks_and_corrupt_files`、`tests/test_api_security.py::test_coding_job_manager_rejects_invalid_limits` | 本地 `pytest -v tests/test_api_security.py`；GitHub Actions `Unit tests` | `0a681cbf068a0a9907c0823c91df087b2f42a0fe`；passed；CI [Run #43589885389](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43589885389) |
| E4 | SF-API-01-A4 | `tests/test_api_security.py::test_corrupted_regression_record_returns_422`、`tests/test_api_security.py::test_storage_audit_rejects_negative_quota`、`tests/test_api_security.py::test_repository_endpoints_reject_invalid_or_nonexistent_repo` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q`（783 passed）；GitHub Actions Lint / SDD contract check / Unit tests (783 passed) / Docker integration tests (15 passed) | `0a681cbf068a0a9907c0823c91df087b2f42a0fe`；passed；CI [Run #43589885389](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43589885389) 全绿 |
| E5 | SF-API-01-A1 | `tests/test_api_security.py::test_actual_request_bytes_are_limited_even_without_honest_length`、`tests/test_api_security.py::test_payload_limit_rejects_malformed_content_length` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E6 | SF-API-01-A2 | `tests/test_api_security.py::test_session_id_validation_rejects_path_traversal_and_malformed` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E7 | SF-API-01-A3 | `tests/test_api_security.py::test_fifo_cancel_marker_is_rejected_without_blocking`、`tests/test_api_security.py::test_list_jobs_skips_record_with_non_string_state`、`tests/test_api_security.py::test_job_writer_does_not_publish_unreadably_large_record`、`tests/test_api_security.py::test_load_record_rejects_symlinks_fifos_and_oversized_files` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E8 | SF-API-01-A4 | `tests/test_api_security.py::test_real_regression_journal_reports_corruption_without_reusing_evidence`、`tests/test_api_security.py::test_valid_record_after_corrupt_history_recovers_api_report`、`tests/test_api_security.py::test_fake_git_marker_does_not_establish_repository`、`tests/test_api_security.py::test_storage_audit_rejects_negative_quota` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |

原分支历史执行记录（当前状态以下方审查交接为准）：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`feat/sf-api-01`；Worktree=`selfforge-sf-api-01`；PR=[#77](https://github.com/liuzhouquan/selfforge-agent/pull/77)；Source=`0a681cbf068a0a9907c0823c91df087b2f42a0fe`。实现 Web Coding API 请求体上限拦截、会话路径穿越防御与作业管理防符链读取（源码提交 `0a681cbf068a0a9907c0823c91df087b2f42a0fe`，[R1 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43589885389) 全绿）；本状态提交仅更新文档，最终 head `H` CI 待回填。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的数量不累加。ASGI 层限制实际请求字节；取消标记非阻塞且验证普通文件；作业读写同为 64 KiB。真实坏日志的必要共享接入为 runtime/session.py：同次扫描区分确定损坏，strict→422；缺失、空、过滤和不完整读取沿用404，默认确认撤销逻辑不变，合法后续记录可恢复。

审查整合：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`review/pr74-78`；PR=#77；Merge=`199bac4bb397d22ea3cb9e408090ae01f63539e5`；最终受测 Source=`dc6ff236e46c59e7e3fb7ed772947093ca3cb698`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：68e56fb3ffb61439b00a90a81586d983af33a601 / review/pr74-78 (worktree: selfforge-review-pr74-78) / 已验证源码 dc6ff236e46c59e7e3fb7ed772947093ca3cb698；后续对账仅文档
已完成验收与证据 ID：SF-API-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 878 passed、无跳过；模型只用脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #77 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896（最终组合；各源 PR 见审查记录） / 199bac4bb397d22ea3cb9e408090ae01f63539e5
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #78 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-RUN-08

**目标：** 在 `selfforge_agent.runtime` 核心执行器与失败工件模块中贯彻 fail-closed 与安全隔离防御。在 `failure_artifacts.py` 中严格校验 `write_failure` 的用例名称、重复次数（0 至 100,000）与根目录有效性，采用 `O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW` 杜绝符号链接劫持；`prepare_artifacts` 严格拒绝符号链接目录并阻止与被评测仓库冲突；新增 `read_failure` 安全读取诊断工件，严格校验文件名格式并防止路径穿越，以 `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` 打开并校验 `S_ISREG`，拒绝符号链接、FIFO 管道与特殊设备，限制读取上限 128 KiB 并校验 JSON 结构与版本号；在 `execution.py` 中，`HostExecutor` 与 `DockerExecutor` 构造时强制校验 `root` 为已存在非符号链接目录，执行时严格校验 `timeout` 为有限正数（阻断 `NaN`/`inf`/非正数绕过），校验 `command` 非空、不含空字符 `\x00` 且不超过 64 KiB；`resolve_docker_image` 校验有限正超时；`run_with_artifact` 对工件文件名执行白名单校验。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/failure_artifacts.py`、`src/selfforge_agent/runtime/execution.py` 及专项测试 `tests/test_runtime_execution_safety.py`；不改动上层 Coding/Review/Benchmark 模型调用逻辑。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-RUN-08-A1 | `write_failure` 严格校验 `case` 名称（非空、无路径穿越/分隔符、只含 `[A-Za-z0-9_-]`、不超过 128 字符）、`repeat` 索引（0 至 100,000 间整数、拒绝布尔/浮点/负数/越界）及 `reason` 长度；`root` 必须为已存在非符号链接目录；写盘采用 `O_WRONLY`、`O_CREAT`、`O_EXCL`、`O_NOFOLLOW` 标志原子写入，杜绝符号链接劫持与覆盖 | `tests/test_runtime_execution_safety.py` 路径穿越、非法索引与符号链接隔离测试 | E5（本轮复验） |
| SF-RUN-08-A2 | 新增 `read_failure` 安全读取测试失败诊断工件，严格校验文件名格式并防止路径穿越；以 `O_RDONLY`、`O_NOFOLLOW`、`O_NONBLOCK` 打开并校验 `S_ISREG`，拒绝符号链接、FIFO 管道与特殊设备以防挂起；限制读取上限 128 KiB 并校验 JSON 结构与版本号 | `tests/test_runtime_execution_safety.py` 诊断工件安全读取、特殊文件与损坏工件防御测试 | E6（本轮复验） |
| SF-RUN-08-A3 | `HostExecutor` 与 `DockerExecutor` 构造时强制校验 `root` 为已存在非符号链接目录；执行时严格校验 `timeout` 为有限正数（阻断 `NaN`/`inf`/非正数绕过），校验 `command` 非空、不含空字符 `\x00` 且不超过 64 KiB；`resolve_docker_image` 校验有限正超时；`run_with_artifact` 对工件文件名执行白名单校验 | `tests/test_runtime_execution_safety.py` 执行器超时、命令有效性、工作区根目录与工件命名测试 | E7（本轮复验） |
| SF-RUN-08-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-RUN-08-A1 | `tests/test_runtime_execution_safety.py::test_write_failure_rejects_path_traversal_in_case_name`、`tests/test_runtime_execution_safety.py::test_write_failure_rejects_invalid_repeat`、`tests/test_runtime_execution_safety.py::test_write_failure_rejects_symlink_root_and_target`、`tests/test_runtime_execution_safety.py::test_prepare_artifacts_rejects_symlinks_and_repo_conflicts` | 本地 `pytest -v tests/test_runtime_execution_safety.py`；GitHub Actions CI `Unit tests` | `1d03cd3739a53ae7c7928af1001dbcb02d3f1c99`；passed；CI [R1 43593364230](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43593364230) |
| E2 | SF-RUN-08-A2 | `tests/test_runtime_execution_safety.py::test_read_failure_safely_reads_valid_artifact`、`tests/test_runtime_execution_safety.py::test_read_failure_rejects_symlinks_fifos_and_path_traversal` | 本地 `pytest -v tests/test_runtime_execution_safety.py`；GitHub Actions CI `Unit tests` | `1d03cd3739a53ae7c7928af1001dbcb02d3f1c99`；passed；CI [R1 43593364230](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43593364230) |
| E3 | SF-RUN-08-A3 | `tests/test_runtime_execution_safety.py::test_executor_rejects_non_finite_and_non_positive_timeout`、`tests/test_runtime_execution_safety.py::test_executor_rejects_empty_and_null_byte_commands`、`tests/test_runtime_execution_safety.py::test_executor_rejects_symlink_or_missing_root`、`tests/test_runtime_execution_safety.py::test_docker_artifact_name_validation`、`tests/test_runtime_execution_safety.py::test_resolve_docker_image_rejects_non_finite_timeout` | 本地 `pytest -v tests/test_runtime_execution_safety.py`；GitHub Actions CI `Unit tests` | `1d03cd3739a53ae7c7928af1001dbcb02d3f1c99`；passed；CI [R1 43593364230](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43593364230) |
| E4 | SF-RUN-08-A4 | `tests/test_runtime_execution_safety.py::test_resolve_docker_image_rejects_non_finite_timeout` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、全量单元测试 783 passed；GitHub Actions CI Lint / SDD contract check / Unit tests 783 passed / Docker integration tests 15 passed | `1d03cd3739a53ae7c7928af1001dbcb02d3f1c99`；passed；CI [R1 43593364230](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43593364230) 全绿 |
| E5 | SF-RUN-08-A1 | `tests/test_runtime_execution_safety.py::test_failure_file_becomes_visible_only_after_complete_write`、`tests/test_runtime_execution_safety.py::test_write_failure_rejects_path_traversal_in_case_name`、`tests/test_runtime_execution_safety.py::test_write_failure_rejects_invalid_repeat` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E6 | SF-RUN-08-A2 | `tests/test_runtime_execution_safety.py::test_failure_reader_validates_complete_identity_and_summary`、`tests/test_runtime_execution_safety.py::test_failure_reader_closes_descriptor_when_stat_fails`、`tests/test_runtime_execution_safety.py::test_failure_reader_converts_parser_recursion_to_value_error`、`tests/test_runtime_execution_safety.py::test_read_failure_rejects_symlinks_fifos_and_path_traversal` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E7 | SF-RUN-08-A3 | `tests/test_runtime_execution_safety.py::test_executor_enforces_utf8_byte_command_limit`、`tests/test_runtime_execution_safety.py::test_executor_rejects_unusable_numeric_timeouts`、`tests/test_runtime_execution_safety.py::test_docker_artifact_name_validation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |
| E8 | SF-RUN-08-A4 | `tests/test_runtime_execution_safety.py::test_read_failure_safely_reads_valid_artifact` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；API 另做真实 chunked HTTP smoke；Ruff/SDD/空白通过 | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；passed；完整组合 878 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)；[审查记录](./review-pr74-78.md) |

原分支历史执行记录（当前状态以下方审查交接为准）：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`feat/sf-run-08`；Worktree=`selfforge-sf-run-08`；PR=[#78](https://github.com/liuzhouquan/selfforge-agent/pull/78)；Source=`1d03cd3739a53ae7c7928af1001dbcb02d3f1c99`。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的数量不累加。临时文件完整写入并同步后，通过锚定目录 fd 的 link 原子无覆盖发布；读取校验完整身份和 TestResult 摘要，所有错误路径关闭 fd。命令上限按 UTF-8 字节计算，非法数值在启动进程前拒绝。

审查整合：Base=`68e56fb3ffb61439b00a90a81586d983af33a601`；Branch=`review/pr74-78`；PR=#78；Merge=`843a3d5d88a08dd1f2b6ab8d1751f89ed30fa543`；最终受测 Source=`dc6ff236e46c59e7e3fb7ed772947093ca3cb698`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：68e56fb3ffb61439b00a90a81586d983af33a601 / review/pr74-78 (worktree: selfforge-review-pr74-78) / 已验证源码 dc6ff236e46c59e7e3fb7ed772947093ca3cb698；后续对账仅文档
已完成验收与证据 ID：SF-RUN-08-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 878 passed、无跳过；模型只用脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #78 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896（最终组合；各源 PR 见审查记录） / 843a3d5d88a08dd1f2b6ab8d1751f89ed30fa543
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填 PR #78 交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-PROV-02

**目标：** 在 `selfforge_agent.integrations.model_http` 与 `selfforge_agent.providers` 中贯彻 fail-closed 与安全隔离防御。在 `model_http.py` 的 `request_json` 中严格限制 URL Scheme 为 `https` 或本地回环 `http`（`is_loopback` IP、`localhost`、`localhost.localdomain`），严格拒绝 `file:`、`ftp:`、`gopher:` 等任意非安全协议，阻止非 HTTP 协议读取、非回环明文 HTTP 和跨源重定向；HTTPS 目标未做地址/DNS 隔离，不宣称全面 SSRF 防护；校验 `api_key` 格式，杜绝 `\r`、`\n`、`\x00` 等换行/空字符 CRLF 响应头注入；校验 `url` 主机名与端口号（1–65535）有效性；严格校验 `timeout` 为有限正数（大于 0 且不超过 300 秒，阻断 `NaN`/`inf`/非正数/布尔值绕过），校验 `max_bytes` 为正整数且不超过 16 MiB；在 `providers.py` 中，配置读取 `_settings` 采用 `O_RDONLY`、`O_NOFOLLOW`、`O_NONBLOCK` 打开并校验 `S_ISREG`，拒绝符号链接、FIFO 管道与特殊设备，限制读取上限 64 KiB；进程锁 `_settings_lock` 强制检查并采用 `O_NOFOLLOW` 排他建锁防符链穿透；`save_provider_settings` 校验父目录与路径非符号链接。

**范围/入口：** 仅改动 `src/selfforge_agent/integrations/model_http.py`、`src/selfforge_agent/providers.py` 及专项测试 `tests/test_model_http_security.py`；不改动外部模型提供商接口契约与会话模型调度逻辑。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-PROV-02-A1 | `request_json` 强制校验 URL Scheme 为 `https` 或本地回环 `http`，严格拒绝 `file:` 等非安全协议以阻止非 HTTP 协议读取；拒绝非回环明文 HTTP 和跨源重定向，不宣称目标地址/DNS 隔离；校验 `api_key` 格式杜绝 `\r`、`\n`、`\x00` CRLF 响应头注入；校验 `url` 主机名与端口号（1–65535）有效性 | `tests/test_model_http_security.py` 协议白名单、文件读取阻断、CRLF 注入与 URL 结构测试 | E5（本轮复验） |
| SF-PROV-02-A2 | `request_json` 严格校验 `timeout` 为有限正数且不超过 300 秒（阻断 `NaN`/`inf`/非正数/布尔值绕过），校验 `max_bytes` 为正整数且不超过 16 MiB（拒绝布尔值/浮点数）；在读取流中严格按剩余截止时间与字节预算收敛 | `tests/test_model_http_security.py` 超时与字节上限有界边界测试 | E6（本轮复验） |
| SF-PROV-02-A3 | `providers.py` 的配置读取 `_settings` 采用 `O_RDONLY`、`O_NOFOLLOW`、`O_NONBLOCK` 打开并校验 `S_ISREG`，拒绝符号链接、FIFO 管道与特殊设备，防止进程挂起与符链逃逸，限制单次读取上限 64 KiB；进程锁 `_settings_lock` 与临时写入强制采用 `O_NOFOLLOW` 排他建锁与无符链原子发布 | `tests/test_model_http_security.py` 符号链接、FIFO管道、文件锁与持久化隔离测试 | E7（本轮复验） |
| SF-PROV-02-A4 | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | 本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-PROV-02-A1 | `tests/test_model_http_security.py::test_request_json_rejects_non_http_schemes_and_prevents_file_leak`、`tests/test_model_http_security.py::test_request_json_rejects_crlf_and_null_bytes_in_api_key`、`tests/test_model_http_security.py::test_request_json_validates_url_hostname_and_port`、`tests/test_model_http_security.py::test_validate_base_url_port_and_control_chars` | 本地 `pytest -v tests/test_model_http_security.py` 与 GitHub Actions CI | 源码 SHA `1aa85f67776a3c7d2e985bcf4b37ea8d227abfb9`；passed；CI [R1 43605412502](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43605412502) success |
| E2 | SF-PROV-02-A2 | `tests/test_model_http_security.py::test_request_json_rejects_invalid_timeout_and_max_bytes` | 本地 `pytest -v tests/test_model_http_security.py` 与 GitHub Actions CI | 源码 SHA `1aa85f67776a3c7d2e985bcf4b37ea8d227abfb9`；passed；CI [R1 43605412502](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43605412502) success |
| E3 | SF-PROV-02-A3 | `tests/test_model_http_security.py::test_providers_settings_rejects_symlink_and_fifo`、`tests/test_model_http_security.py::test_providers_settings_lock_rejects_symlink`、`tests/test_model_http_security.py::test_providers_save_settings_rejects_symlink_parent` | 本地 `pytest -v tests/test_model_http_security.py` 与 GitHub Actions CI | 源码 SHA `1aa85f67776a3c7d2e985bcf4b37ea8d227abfb9`；passed；CI [R1 43605412502](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43605412502) success |
| E4 | SF-PROV-02-A4 | `tests/test_model_http_security.py::test_validate_base_url_port_and_control_chars` | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q -m "not integration"` 与 GitHub Actions CI（含 Docker 集成） | 源码 SHA `1aa85f67776a3c7d2e985bcf4b37ea8d227abfb9`；本地 871 passed, 15 deselected；CI [R1 43605412502](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43605412502) success |
| E5 | SF-PROV-02-A1 | `tests/test_model_http_security.py::test_request_json_rejects_non_http_schemes_and_prevents_file_leak`、`tests/test_model_http_security.py::test_request_json_rejects_crlf_and_null_bytes_in_api_key`、`tests/test_model_http_security.py::test_request_json_validates_url_hostname_and_port`、`tests/test_model_http_security.py::test_validate_base_url_port_and_control_chars`、`tests/test_model_http_security.py::test_cross_origin_redirect_never_receives_provider_credentials` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-PROV-02-A2 | `tests/test_model_http_security.py::test_request_json_rejects_invalid_timeout_and_max_bytes`、`tests/test_model_http_security.py::test_request_json_rejects_timeout_too_large_for_float_conversion`、`tests/test_model_http_security.py::test_redirect_chain_uses_one_absolute_deadline`、`tests/test_model_http_security.py::test_http_parser_recursion_is_a_controlled_provider_error` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-PROV-02-A3 | `tests/test_model_http_security.py::test_providers_settings_rejects_symlink_and_fifo`、`tests/test_model_http_security.py::test_providers_settings_lock_rejects_symlink`、`tests/test_model_http_security.py::test_providers_save_settings_rejects_symlink_parent`、`tests/test_model_http_security.py::test_oversized_provider_update_preserves_readable_previous_settings`、`tests/test_model_http_security.py::test_settings_read_error_does_not_close_reused_descriptor` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-PROV-02-A4 | `tests/test_model_http_security.py::test_validate_base_url_port_and_control_chars` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原分支历史执行记录（当前状态以下方审查交接为准）：Base=`142032483838dc85458022a843e9803b9bc8bf86`；Branch=`feat/sf-prov-02`；Worktree=`selfforge-sf-prov-02`；PR=[#79](https://github.com/liuzhouquan/selfforge-agent/pull/79)。先运行 TDD 红灯复现（8 failed），随后在 model_http.py 与 providers.py 中实现 URL scheme 限制与文件读取杜绝、CRLF 响应头注入防御、配置读取与文件锁防符链/FIFO 阻塞安全加固；在源码提交 `1aa85f67776a3c7d2e985bcf4b37ea8d227abfb9` 通过本地全量 871 项单测与 GitHub Actions CI `Run #43605412502`（含 Docker 集成）；本状态提交仅修改文档。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。同源 redirect 共用绝对截止时间，拒绝跨源，持久化读写同一字节边界；FD 所有权补修随最终 #90 组合交付。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#79；Merge=`036e67863d83a69b4bde3f5c3ffe3a34a171d54c`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-PROV-02-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #79 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 036e67863d83a69b4bde3f5c3ffe3a34a171d54c
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-INT-01

**目标：** 在 `selfforge_agent.integrations.github_pr` 与 `selfforge_agent.core.git_context` 中贯彻 fail-closed 与安全隔离防御。在 `github_pr.py` 的 `parse_pr_url` 中严格限制 URL Scheme 仅为 `https`，主机名严格为 `github.com`，杜绝凭据与显式端口注入；对 `owner` 与 `repo` 执行严格白名单校验（禁止 `-` 开头，owner 与 repository 分别校验并保留合法 `.github` 等仓库名、禁止 `.git` 结尾及保留路径 `.`/`..`），杜绝 CLI 选项注入与路径穿越；在 `checkout_pr` 中对 `workspace_root` 及其父目录执行严格符号链接/FIFO/非目录防御，禁止检出到符号链接或覆盖已有目录，异常回滚时采用安全防符链删除；在 `_run` 中严格校验 `timeout` 为有限正数（大于 0 且不超过 300 秒，阻断布尔值与 NaN/inf）并对命令标准输出/错误输出实施 8 MiB 有界截断防 OOM；在 `git_context.py` 中，`build_context` 与 `_git` 严格拒绝符号链接仓库与控制字符，统一 SHA 校验大小写规范化，`parse_hunks` 限制 Hunk/行数上限（5000 / 50000）并校验非负有界行号以杜绝畸形 diff DoS 攻击。

**范围/入口：** 复用必要共享 `src/selfforge_agent/core/process.py`；改动 `src/selfforge_agent/integrations/github_pr.py`、`src/selfforge_agent/core/git_context.py` 及专项测试 `tests/test_github_pr_security.py`；不改动外部接口行为契约。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-INT-01-A1 | `parse_pr_url` 严格校验 URL Scheme 为 `https`，禁止凭据、端口、空白与控制字符；严格校验 `owner` 与 `repo` 规范（阻断 `-` 前缀、精确点路径，保留合法点前缀仓库、`.git` 后缀及 `.`/`..` 注入），校验 PR 编号为正整数（1–2147483647） | `tests/test_github_pr_security.py` 参数注入、协议规范与边界测试 | E5（本轮复验） |
| SF-INT-01-A2 | `checkout_pr` 严格校验 `workspace_root` 及其父路径非符号链接/FIFO/非目录，杜绝符号链接逃逸；检出目录冲突时显式拒绝；在失败回滚时采用防符链安全清理 | `tests/test_github_pr_security.py` 符号链接根、父路径、目录冲突与回滚隔离测试 | E6（本轮复验） |
| SF-INT-01-A3 | `_run` 严格校验 `timeout` 为有限正数（大于 0 且不超过 300 秒，阻断布尔值与 NaN/inf），校验 `cwd` 非符号链接，实施 8 MiB 输出有界限制；`_api` 阻断 `-` 开头或含控制字符的非法端点 | `tests/test_github_pr_security.py` 命令超时、符号链接 cwd、API 端点有界测试 | E7（本轮复验） |
| SF-INT-01-A4 | `build_context` 与 `_git` 严格拒绝符号链接仓库与控制字符，统一 SHA 大小写规范化；`parse_hunks` 限制行数/Hunk 上限并校验非负有界行号；`ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | `tests/test_github_pr_security.py` 仓库符链、SHA 规范化、Hunk 边界测试及本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-INT-01-A1 | `tests/test_github_pr_security.py::test_parse_pr_url_rejects_argument_injection_and_invalid_chars` | 本地 `pytest -v tests/test_github_pr_security.py` 与 GitHub Actions CI | 源码 SHA `5b2bbc2ed9874ce5ea1b4f4f5c94fb118d3ef08c`；passed；CI [R1 43609921849](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43609921849) success |
| E2 | SF-INT-01-A2 | `tests/test_github_pr_security.py::test_checkout_pr_rejects_symlink_workspace_root_and_parent`、`tests/test_github_pr_security.py::test_checkout_pr_rejects_existing_destination_and_cleans_up` | 本地 `pytest -v tests/test_github_pr_security.py` 与 GitHub Actions CI | 源码 SHA `5b2bbc2ed9874ce5ea1b4f4f5c94fb118d3ef08c`；passed；CI [R1 43609921849](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43609921849) success |
| E3 | SF-INT-01-A3 | `tests/test_github_pr_security.py::test_run_validates_timeout_and_bounds_output`、`tests/test_github_pr_security.py::test_api_rejects_malicious_endpoints` | 本地 `pytest -v tests/test_github_pr_security.py` 与 GitHub Actions CI | 源码 SHA `5b2bbc2ed9874ce5ea1b4f4f5c94fb118d3ef08c`；passed；CI [R1 43609921849](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43609921849) success |
| E4 | SF-INT-01-A4 | `tests/test_github_pr_security.py::test_git_context_build_context_rejects_symlink_repository`、`tests/test_github_pr_security.py::test_git_context_normalizes_sha_case_and_rejects_null_bytes`、`tests/test_github_pr_security.py::test_parse_hunks_bounds_lines_and_rejects_null_bytes` | `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、`pytest -q -m "not integration"` 与 GitHub Actions CI（含 Docker 集成） | 源码 SHA `5b2bbc2ed9874ce5ea1b4f4f5c94fb118d3ef08c`；本地 passed；CI [R1 43609921849](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43609921849) success |
| E5 | SF-INT-01-A1 | `tests/test_github_pr_security.py::test_parse_pr_url_rejects_argument_injection_and_invalid_chars` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-INT-01-A2 | `tests/test_github_pr_security.py::test_checkout_pr_rejects_symlink_workspace_root_and_parent`、`tests/test_github_pr_security.py::test_checkout_pr_rejects_existing_destination_and_cleans_up` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-INT-01-A3 | `tests/test_github_pr_security.py::test_run_validates_timeout_and_bounds_output`、`tests/test_github_pr_security.py::test_api_rejects_malicious_endpoints`、`tests/test_github_pr_security.py::test_github_command_limit_stops_producer_before_followup_action` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-INT-01-A4 | `tests/test_github_pr_security.py::test_git_context_build_context_rejects_symlink_repository`、`tests/test_github_pr_security.py::test_git_context_normalizes_sha_case_and_rejects_null_bytes`、`tests/test_github_pr_security.py::test_parse_hunks_bounds_lines_and_rejects_null_bytes` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原分支历史执行记录（当前状态以下方审查交接为准）：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`feat/sf-int-01`；Worktree=`selfforge-sf-int-01`；PR=[#80](https://github.com/liuzhouquan/selfforge-agent/pull/80)。先运行 TDD 红灯复现（5 failed），随后在 github_pr.py 与 git_context.py 中实现严格 URL 校验、CLI 选项注入防御、工作区符号链接隔离、超时与输出有界防御、仓库防符链及 Hunk 解析上限防御；在源码提交 `5b2bbc2ed9874ce5ea1b4f4f5c94fb118d3ef08c` 通过本地专项与 GitHub Actions CI `Run #43609921849`（含 Docker 集成）；本状态提交仅修改文档。

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。GitHub 命名契约按 #82 兼容修订，不能把合法 `.github` 仓库当作攻击；共享 core/process.py 是实际输出门禁的必要前提。默认 8 MiB，显式上限最多 10 MiB；timeout 为 (0,300]。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#80；Merge=`97ec2989c1815a068983d8cdb96ef3acbf8ec32f`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-INT-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #80 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 97ec2989c1815a068983d8cdb96ef3acbf8ec32f
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-PRICE-01

**目标：** 在模型费率卡解析与货币成本估算模块（`src/selfforge_agent/runtime/pricing.py`）中贯彻输入防御（Fail-Closed）、跨层规范化与未受控异常收敛原则。严格校验 `TokenUsage` 的 `prompt_tokens` 与 `completion_tokens` 类型为严格整数 `int`，拒绝布尔值（`True`/`False`）、浮点数、字符串、负数以及单次请求超过 `1,000,000,000` 的异常数值；对环境变量 `SELFFORGE_PRICING_JSON` 执行反序列化深度防御，限制输入体积上限为 16 KiB 并杜绝空字符 `\x00`，严格要求根对象及 provider/model 映射层级均为字典结构，拒绝非字典与畸形配置；模型与 provider 标识符校验排斥空字符与控制字符（`\r`、`\n`、`\t`）；在 `ModelRates` 与 `MonetaryCost` 中强制规范化 ISO 4217 三位大写字母货币代码（`^[A-Z]{3}$`）；在 `sum_monetary_costs` 中强制执行跨币种一致性校验，拒绝在无汇率换算时混加不同货币（返回 `None`）；在 `format_monetary_cost` 中提供健壮防御，遇到 `None`、`NaN`、`inf`、负数或格式不规范对象时安全回退返回 `"uncalibrated"`。

**范围/入口：** 必要共享前提为 `src/selfforge_agent/providers.py` 的 usage 严格整数反序列化；改动 `src/selfforge_agent/runtime/pricing.py` 并新增专项测试 `tests/test_pricing_security.py`；保持与既有调用方（`pricing.py`、`test_pricing.py`）向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-PRICE-01-A1 | TokenUsage 的 `prompt_tokens` 与 `completion_tokens` 必须为严格非负整数且不超过 10 亿；传入布尔值（如 `True`）、浮点数、字符串、负数或超大数值时 fail-closed 返回 `None` | `tests/test_pricing_security.py` 严格整数、布尔防御、负数与溢出测试 | E5（本轮复验） |
| SF-PRICE-01-A2 | `ModelRates` 与 `MonetaryCost` 强制要求 ISO 4217 三位大写字母货币代码（`^[A-Z]{3}$`），对小写、空字符串、非三位字母或含特殊字符拒绝校验 | `tests/test_pricing_security.py` 货币代码正则格式测试 | E6（本轮复验） |
| SF-PRICE-01-A3 | `sum_monetary_costs` 校验输入全量为 `MonetaryCost` 且货币代码完全一致；遇到不同货币混加（如 USD + EUR）时拒绝盲目累加并返回 `None` | `tests/test_pricing_security.py` 跨币种混加防御测试 | E7（本轮复验） |
| SF-PRICE-01-A4 | `SELFFORGE_PRICING_JSON` 反序列化防范超过 16 KiB 超大输入、非字典根结构与畸形卡片；provider/model 参数校验排斥 `\x00` 与控制字符；`format_monetary_cost` 遇到 `None`、`NaN`、负数安全返回 `"uncalibrated"`；本地全量测试与静态检查全部通过 | `tests/test_pricing_security.py` 环境变量与格式化防御测试，本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-PRICE-01-A1 | `tests/test_pricing_security.py::test_pricing_rejects_boolean_and_non_int_token_usage`、`tests/test_pricing_security.py::test_pricing_rejects_extreme_and_overflow_token_counts` | 本地 `pytest -v tests/test_pricing_security.py`；GitHub Actions CI `Unit tests` | `4cc33285035b9b82a3519c6326f2367880581035`；passed；CI [R1 43616839463](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43616839463) |
| E2 | SF-PRICE-01-A2 | `tests/test_pricing_security.py::test_pricing_enforces_iso_currency_code` | 本地 `pytest -v tests/test_pricing_security.py`；GitHub Actions CI `Unit tests` | `4cc33285035b9b82a3519c6326f2367880581035`；passed；CI [R1 43616839463](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43616839463) |
| E3 | SF-PRICE-01-A3 | `tests/test_pricing_security.py::test_pricing_rejects_mixed_currency_in_sum_monetary_costs` | 本地 `pytest -v tests/test_pricing_security.py`；GitHub Actions CI `Unit tests` | `4cc33285035b9b82a3519c6326f2367880581035`；passed；CI [R1 43616839463](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43616839463) |
| E4 | SF-PRICE-01-A4 | `tests/test_pricing_security.py::test_pricing_rejects_malformed_and_oversized_env_json`、`tests/test_pricing_security.py::test_format_monetary_cost_handles_invalid_cost`、`tests/test_pricing.py::test_format_monetary_cost` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、单元测试（869 passed）；GitHub Actions CI Lint / SDD contract check / Unit tests (869 passed) / Docker integration tests (15 passed) | `4cc33285035b9b82a3519c6326f2367880581035`；passed；CI [R1 43616839463](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43616839463) 全绿 |
| E5 | SF-PRICE-01-A1 | `tests/test_pricing_security.py::test_pricing_rejects_boolean_and_non_int_token_usage`、`tests/test_pricing_security.py::test_pricing_rejects_extreme_and_overflow_token_counts`、`tests/test_pricing_security.py::test_malformed_usage_history_cannot_establish_calibrated_cost` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-PRICE-01-A2 | `tests/test_pricing_security.py::test_pricing_enforces_iso_currency_code`、`tests/test_pricing_security.py::test_rate_card_limit_counts_utf8_bytes` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-PRICE-01-A3 | `tests/test_pricing_security.py::test_pricing_rejects_mixed_currency_in_sum_monetary_costs`、`tests/test_pricing_security.py::test_non_usd_rate_card_cannot_establish_usd_budget_evidence`、`tests/test_pricing_security.py::test_non_usd_explicit_same_currency_sum_and_format_remain_available` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-PRICE-01-A4 | `tests/test_pricing_security.py::test_pricing_rejects_malformed_and_oversized_env_json`、`tests/test_pricing_security.py::test_format_monetary_cost_handles_invalid_cost`、`tests/test_pricing.py::test_format_monetary_cost`、`tests/test_pricing_security.py::test_formatter_rejects_invalid_currency_on_unvalidated_objects`、`tests/test_pricing_security.py::test_formatter_handles_integer_too_large_for_float_conversion` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。生产美元成本消费者只接受 USD rate card；显式非 USD 同币种合计保留货币标签、不带 $。严格 usage 历史反序列化随 #84 共享修复交付；配置费率估算不是实际账单。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#81；Merge=`18b8021ed147ce9778fd483a8c1ae5c4fd499849`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-PRICE-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #81 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 18b8021ed147ce9778fd483a8c1ae5c4fd499849
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-GH-01

**目标：** 在 GitHub PR 读取与检出集成模块（`src/selfforge_agent/integrations/github_pr.py`）中贯彻输入防御（Fail-Closed）、跨层规范化与未受控异常收敛原则。严格校验 `parse_pr_url` 中的 owner 与 repository 命名规则，杜绝连字符开头（防范 `gh repo clone` 选项注入）、点路径穿越（`..` / `.` 阻断 API 路径穿越）与非法字符；在 `load_pr` 中对变更文件路径执行路径穿越（`..`）、绝对路径及控制字符/空字符校验，并将文件状态严格映射并收敛至有效 Git 状态代码集合（`{"A", "D", "M", "R", "C"}`）；在 `checkout_pr` 中对 `workspace_root` 强制执行常规目录与符号链接排斥检查，杜绝符号链接根目录逃逸；在 `_run` 与 `_api` 中增加有限超时与最大 10 MiB 命令输出有界防御，并拦截以 `-` 开头的非法 API 端点。

**范围/入口：** 改动 `src/selfforge_agent/integrations/github_pr.py` 并新增专项测试 `tests/test_github_pr_reader_security.py`；保持与既有调用方（`app.py`、`test_github_web.py`）向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-GH-01-A1 | `parse_pr_url` 严格校验 GitHub PR URL 结构、owner 与 repository 格式（排斥连字符前缀、路径穿越点及控制字符），校验 PR 编号为正整数；遇到非法输入 fail-closed 抛出 `GitHubPRError` | `tests/test_github_pr_reader_security.py` URL 结构、选项防注入与格式防御测试 | E5（本轮复验） |
| SF-GH-01-A2 | `load_pr` 严格校验变更文件路径，排斥绝对路径、路径穿越（`..`）与控制字符；将变更文件状态严格收敛至标准代码（`A`/`D`/`M`/`R`/`C`）并拒绝非法状态；排斥畸形或缺失字段的元数据 | `tests/test_github_pr_reader_security.py` 文件路径防穿越、状态收敛与元数据防御测试 | E6（本轮复验） |
| SF-GH-01-A3 | `checkout_pr` 强制校验 `workspace_root` 为非符号链接目录（允许创建缺失的正常根目录）（拒绝符号链接逃逸或非目录），确保目标检出路径不发生预占冲突与符号链接越权；失败清理不跟随符号链接 | `tests/test_github_pr_reader_security.py` 工作区根目录符号链接与常规文件隔离测试 | E7（本轮复验） |
| SF-GH-01-A4 | `_run` 校验有限正超时并限制默认8 MiB、显式最大命令输出不超过10 MiB；`_api` 拦截选项注入端点；本地全量测试与静态检查全部通过 | `tests/test_github_pr_reader_security.py` 命令有界防御测试，本地命令矩阵与 CI | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-GH-01-A1 | `tests/test_github_pr_reader_security.py::test_parse_pr_url_rejects_dangerous_owner_and_repo_names` | 本地 `pytest -v tests/test_github_pr_reader_security.py`；GitHub Actions CI `Unit tests` | `e2454a4b6eb43fd49245820d48c944885c85bd17`；passed；CI [R1 43660950665](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43660950665) |
| E2 | SF-GH-01-A2 | `tests/test_github_pr_reader_security.py::test_load_pr_rejects_path_traversal_and_malformed_filenames`、`tests/test_github_pr_reader_security.py::test_load_pr_normalizes_changed_file_statuses`、`tests/test_github_pr_reader_security.py::test_load_pr_rejects_malformed_metadata` | 本地 `pytest -v tests/test_github_pr_reader_security.py`；GitHub Actions CI `Unit tests` | `e2454a4b6eb43fd49245820d48c944885c85bd17`；passed；CI [R1 43660950665](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43660950665) |
| E3 | SF-GH-01-A3 | `tests/test_github_pr_reader_security.py::test_checkout_pr_rejects_symlink_workspace_root` | 本地 `pytest -v tests/test_github_pr_reader_security.py`；GitHub Actions CI `Unit tests` | `e2454a4b6eb43fd49245820d48c944885c85bd17`；passed；CI [R1 43660950665](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43660950665) |
| E4 | SF-GH-01-A4 | `tests/test_github_pr_reader_security.py::test_command_execution_bounded_output_and_timeout`、`tests/test_github_web.py::test_pr_url_is_restricted_to_github` | 本地 `ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、单元测试（869 passed）；GitHub Actions CI Lint / SDD contract check / Unit tests (869 passed) / Docker integration tests (15 passed) | `e2454a4b6eb43fd49245820d48c944885c85bd17`；passed；CI [R1 43660950665](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43660950665) 全绿 |
| E5 | SF-GH-01-A1 | `tests/test_github_pr_reader_security.py::test_parse_pr_url_rejects_dangerous_owner_and_repo_names`、`tests/test_github_pr_reader_security.py::test_valid_repository_names_remain_accepted` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-GH-01-A2 | `tests/test_github_pr_reader_security.py::test_load_pr_rejects_path_traversal_and_malformed_filenames`、`tests/test_github_pr_reader_security.py::test_load_pr_normalizes_changed_file_statuses`、`tests/test_github_pr_reader_security.py::test_load_pr_rejects_malformed_metadata`、`tests/test_github_pr_reader_security.py::test_load_pr_preserves_exact_whitespace_in_git_filename`、`tests/test_github_pr_reader_security.py::test_load_pr_rejects_metadata_that_would_be_silently_coerced`、`tests/test_github_pr_reader_security.py::test_changed_paths_cannot_hide_traversal_or_controls_by_normalization` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-GH-01-A3 | `tests/test_github_pr_reader_security.py::test_checkout_pr_rejects_symlink_workspace_root` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-GH-01-A4 | `tests/test_github_pr_reader_security.py::test_command_execution_bounded_output_and_timeout`、`tests/test_github_web.py::test_pr_url_is_restricted_to_github` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。保留 #80/#82 两组测试；合法命名、真实文件名和严格元数据兼容修复。输出默认8 MiB、允许显式最多10 MiB；可创建缺失的正常 workspace_root，既有祖先仍拒绝符链/非目录。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#82；Merge=`094d074a1e3df08340c61bf7ff9339de67cb21c0`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-GH-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #82 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 094d074a1e3df08340c61bf7ff9339de67cb21c0
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-CORE-01

**目标：** 在 `selfforge_agent.core.git_context` 中贯彻输入防御、受控收敛与资源有界防御。`build_context` 严格校验仓库路径 `repo_root`，通过 `os.lstat` 阻断软链接仓库根目录与不存在/非目录路径；对 `git diff --name-status -z` 的输出安全解包，严防截断或畸形输出触发未受控异常，对变更文件路径执行安全性校验（拒绝空字节 `\x00`、控制字符、绝对路径与 `..` 路径穿越段）；在 `_git` 底层执行中强制校验 `timeout` 为有限正数，并限制 git 命令输出大小（默认最大 10 MiB `MAX_GIT_OUTPUT_BYTES`）；在 `parse_hunks` 中对补丁文本大小（最大 10 MiB `MAX_PATCH_BYTES`）、最大 hunk 数量（5,000 `MAX_HUNKS`）、最大行数（50,000 `MAX_DIFF_LINES`）及行号数值（最大 10,000,000 `MAX_LINE_NUMBER`）施加有界防御，防范解析 DoS 与内存耗尽。

**范围/入口：** 仅改动 `src/selfforge_agent/core/git_context.py` 及安全测试 `tests/test_git_context_security.py`；保持既有外部接口（`build_context`、`parse_hunks`、`GitContextError`）完全兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-CORE-01-A1 | `build_context` 传入软链接仓库根目录或非目录时，在解析前通过 `os.lstat` 识别并抛出受控 `GitContextError`，阻断符号链接跨界 | `tests/test_git_context_security.py` 软链接仓库根目录防御测试 | E5（本轮复验） |
| SF-CORE-01-A2 | `git diff --name-status -z` 输出截断或畸形分片时受控报错，且变更文件路径含空字节、控制字符、绝对路径或 `..` 路径穿越时显式拒绝，抛出受控 `GitContextError` | `tests/test_git_context_security.py` 畸形差异流与路径穿越防御测试 | E6（本轮复验） |
| SF-CORE-01-A3 | `parse_hunks` 对超限补丁文本（>10 MiB）、超限 hunk 数（>5,000）、超限行数（>50,000）或天文数字行号（>10,000,000）施加有界防御，解析超限时抛出受控 `GitContextError` | `tests/test_git_context_security.py` 补丁大小与行号有界防御测试 | E7（本轮复验） |
| SF-CORE-01-A4 | `_git` 强制校验有限正数 `timeout`，并对超大输出执行 10 MiB 上限截断保护；`ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | `tests/test_git_context_security.py`、`tests/test_git_review.py` 与全套静态/单元测试命令 | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-CORE-01-A1 | `tests/test_git_context_security.py::test_build_context_rejects_symlink_repository` | 本地 `pytest -v tests/test_git_context_security.py`；GitHub Actions CI `Unit tests` | `c470ab928dca44c099714bb275263015780db9ab`；passed；CI [R1 43663583620](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43663583620) |
| E2 | SF-CORE-01-A2 | `tests/test_git_context_security.py::test_build_context_rejects_truncated_and_malformed_diff_tokens`、`tests/test_git_context_security.py::test_build_context_rejects_path_traversal_in_diff_paths` | 本地 `pytest -v tests/test_git_context_security.py`；GitHub Actions CI `Unit tests` | `c470ab928dca44c099714bb275263015780db9ab`；passed；CI [R1 43663583620](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43663583620) |
| E3 | SF-CORE-01-A3 | `tests/test_git_context_security.py::test_parse_hunks_bounds_patch_size_and_line_numbers` | 本地 `pytest -v tests/test_git_context_security.py`；GitHub Actions CI `Unit tests` | `c470ab928dca44c099714bb275263015780db9ab`；passed；CI [R1 43663583620](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43663583620) |
| E4 | SF-CORE-01-A4 | `tests/test_git_context_security.py::test_git_execution_bounds_output_and_validates_timeout`、`tests/test_git_review.py::test_diff_hunks_and_heuristic`、`tests/test_git_review.py::test_invalid_repository_and_commit`、`tests/test_git_review.py::test_finding_validation` | 本地 `pytest -v`、`ruff check .`、`python scripts/check_sdd.py`；GitHub Actions CI `Unit tests` 与 `Docker integration tests` | `c470ab928dca44c099714bb275263015780db9ab`；passed；CI [R1 43663583620](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43663583620) |
| E5 | SF-CORE-01-A1 | `tests/test_git_context_security.py::test_build_context_rejects_symlink_repository` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-CORE-01-A2 | `tests/test_git_context_security.py::test_build_context_rejects_truncated_and_malformed_diff_tokens`、`tests/test_git_context_security.py::test_build_context_rejects_path_traversal_in_diff_paths`、`tests/test_git_context_security.py::test_build_context_keeps_exact_legal_git_path` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-CORE-01-A3 | `tests/test_git_context_security.py::test_parse_hunks_bounds_patch_size_and_line_numbers`、`tests/test_git_context_security.py::test_hunk_increment_cannot_exceed_line_number_boundary` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-CORE-01-A4 | `tests/test_git_context_security.py::test_git_execution_bounds_output_and_validates_timeout`、`tests/test_git_review.py::test_diff_hunks_and_heuristic`、`tests/test_git_review.py::test_invalid_repository_and_commit`、`tests/test_git_review.py::test_finding_validation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。保留 #80 更严格 5000 hunks / 50000 lines；Git literal pathspec、真实文件名和增量行号都受验证。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#83；Merge=`827cd214726ab52944a13c55758c200f25398e81`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-CORE-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #83 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 827cd214726ab52944a13c55758c200f25398e81
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-REV-01

**目标：** 在 `selfforge_agent.review` 与 `selfforge_agent.service` 中贯彻 fail-closed、输入防御与资源有界防御。`BaselineReviewer` 严格校验 `PRContext` 类型，对扫描文件数（最大 200）、单文件 hunk 数（最大 500）、行内容长度（最大 10,000 字符）与总 findings 数量（最大 500）施加有界防御，避免超大 diff 或超长行导致正则回溯与内存溢出；对 `line.new_line` 数值施加合法正整数校验（1 至 10,000,000），对空空白行安全提供 `"<blank added line>"` 兜底 evidence 以免破坏 `Finding` 校验，并对重复 findings 自动去重；`ProviderReviewer` 严格校验 `provider` 配置（非空 API Key、合法 HTTP/HTTPS base_url）与 `timeout` 有限正数（排除 `NaN`/`inf`/非正数/布尔绕过），收集 prompt 代码时限制最大文件数（100）、最大行数（200）与单行字符上限（1000），深度防御模型返回 JSON 的 findings 数量（最大 8）与属性约束，并将返回文件/行号严格限定在 diff 的真实 `added` 行集合内；`ReviewService` 强制校验有限正超时、防止 `test_path` 路径穿越与控制字符注入，并受控记录审查异常至 `report.errors`。

**范围/入口：** 必要共享前提为 `src/selfforge_agent/providers.py` 的 usage 严格整数反序列化； 仅改动 `src/selfforge_agent/review/baseline.py`、`src/selfforge_agent/review/deepseek.py`、`src/selfforge_agent/service.py` 及专项测试 `tests/test_review_engine_security.py`；保持外部调用接口（`BaselineReviewer.review`、`ProviderReviewer.review`、`ProviderReviewer.review_with_usage`、`ReviewService.review`、`ReviewService.review_context`）完全向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-REV-01-A1 | `BaselineReviewer` 传入非法 `context` 时抛出受控 `TypeError`，扫描时对文件数（200）、hunk 数（500）、单行字符（10,000）与 findings 上限（500）施加有界截断，杜绝 DoS | `tests/test_review_engine_security.py` 规则基线输入类型与有界扫描测试 | E5（本轮复验） |
| SF-REV-01-A2 | `BaselineReviewer` 针对空白新增行安全兜底生成非空 evidence（`"<blank added line>"`），对合法行号（1 至 10,000,000）执行校验，并对相同 `(file_path, line_number, category)` 自动去重 | `tests/test_review_engine_security.py` 空白行兜底与重复结果去重测试 | E6（本轮复验） |
| SF-REV-01-A3 | `ProviderReviewer` 构造时严格校验有效 Provider 配置与有限正超时（拦截 `NaN`/`inf`/布尔等绕过），Prompt 收集时对文件数、行数与单行长度有界截断，对模型返回结果强制执行属性有界校验、上限约束（最多 8 项）与 diff `added` 行严格归属校验 | `tests/test_review_engine_security.py` 模型审查配置校验、输入有界与输出白名单防御测试 | E7（本轮复验） |
| SF-REV-01-A4 | `ReviewService` 强制校验有限正超时、阻断 `test_path` 路径穿越段与控制字符，受控记录审查器异常至 `report.errors`；`ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | `tests/test_review_engine_security.py`、`tests/test_git_review.py` 与全套静态/单元测试命令 | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-REV-01-A1 | `tests/test_review_engine_security.py::test_baseline_reviewer_rejects_invalid_context`、`tests/test_review_engine_security.py::test_baseline_reviewer_bounds_line_length_and_findings` | 本地 `pytest -v tests/test_review_engine_security.py`；GitHub Actions CI `Unit tests` | `8df31241608d1dbdbb95966d3c890e3324240269`；passed；CI [R1 43667895730](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43667895730) |
| E2 | SF-REV-01-A2 | `tests/test_review_engine_security.py::test_baseline_reviewer_handles_blank_evidence_and_deduplicates` | 本地 `pytest -v tests/test_review_engine_security.py`；GitHub Actions CI `Unit tests` | `8df31241608d1dbdbb95966d3c890e3324240269`；passed；CI [R1 43667895730](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43667895730) |
| E3 | SF-REV-01-A3 | `tests/test_review_engine_security.py::test_provider_reviewer_validates_timeout_and_config`、`tests/test_review_engine_security.py::test_provider_reviewer_bounds_prompt_and_defends_output` | 本地 `pytest -v tests/test_review_engine_security.py`；GitHub Actions CI `Unit tests` | `8df31241608d1dbdbb95966d3c890e3324240269`；passed；CI [R1 43667895730](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43667895730) |
| E4 | SF-REV-01-A4 | `tests/test_review_engine_security.py::test_review_service_input_defense_and_error_handling`、`tests/test_git_review.py::test_diff_hunks_and_heuristic`、`tests/test_provider_compatibility.py::test_claude_review_and_repair_omit_unsupported_json_object`、`tests/test_github_web.py::test_deepseek_findings_must_point_to_added_lines` | 本地 `pytest -v`、`ruff check .`、`python scripts/check_sdd.py`；GitHub Actions CI `Unit tests` 与 `Docker integration tests` | `8df31241608d1dbdbb95966d3c890e3324240269`；passed；CI [R1 43667895730](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43667895730) |
| E5 | SF-REV-01-A1 | `tests/test_review_engine_security.py::test_baseline_reviewer_rejects_invalid_context`、`tests/test_review_engine_security.py::test_baseline_reviewer_bounds_line_length_and_findings` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-REV-01-A2 | `tests/test_review_engine_security.py::test_baseline_reviewer_handles_blank_evidence_and_deduplicates` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-REV-01-A3 | `tests/test_review_engine_security.py::test_provider_reviewer_validates_timeout_and_config`、`tests/test_review_engine_security.py::test_provider_reviewer_bounds_prompt_and_defends_output`、`tests/test_review_engine_security.py::test_malformed_choice_shapes_produce_error_reports`、`tests/test_review_engine_security.py::test_provider_prompt_limit_counts_utf8_bytes` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-REV-01-A4 | `tests/test_review_engine_security.py::test_review_service_input_defense_and_error_handling`、`tests/test_git_review.py::test_diff_hunks_and_heuristic`、`tests/test_provider_compatibility.py::test_claude_review_and_repair_omit_unsupported_json_object`、`tests/test_github_web.py::test_deepseek_findings_must_point_to_added_lines`、`tests/test_review_engine_security.py::test_review_service_records_attribute_failure` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。先验证 response/choice 结构再读 usage/content；prompt按UTF-8字节限额。providers.TokenUsage严格整数为跨层成本证据的必要共享前提。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#84；Merge=`7f7a951f71dbd1b0092532c395b8343dd575e8c5`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-REV-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #84 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 7f7a951f71dbd1b0092532c395b8343dd575e8c5
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-CTRL-01

**目标：** 在 `selfforge_agent.runtime.control` 核心运行时控制流与瞬态指导模块中贯彻 fail-closed、输入防御与资源有界防御。`_canonical_args` 严格校验工具参数类型与尺寸（限制最大 64 KiB `MAX_ARG_BYTES`），清洗空字节 `\x00` 与控制字符，容错深层嵌套结构（`RecursionError`/`ValueError`）防范内存与解析 DoS；`detect_tool_loop` 与 `detect_edit_failure_loop` 严格校验 `messages` 为列表并过滤非字典元素，施加最大消息扫描窗口（1,000 条 `MAX_MESSAGES_SCAN`）与前向查找上限（100 条），对 `min_repeats` 与 `min_failures` 规范化为合法正整数，对失败编辑路径执行 POSIX 路径规范化（折叠 `.` 和 `..`）与字符过滤，并限制并发跟踪路径上限（256 条 `MAX_TRACKED_PATHS`）；`evaluate_context_pressure` 校验非布尔非负整数 token 指标与合法有限浮点水位（0 至 1.0）；`budget_notice` 强制校验有限非负 `remaining_seconds`（阻断 `NaN`/`inf`/负数/布尔绕过），对通知内容中的工具名称（最大 64 字符）与路径（最大 256 字符）施加有界长度约束，阻断长字符串注入与上下文超限。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/control.py` 及专项安全测试 `tests/test_runtime_control_security.py`；保持外部调用接口（`detect_tool_loop`、`detect_edit_failure_loop`、`evaluate_context_pressure`、`budget_notice`）完全向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-CTRL-01-A1 | `_canonical_args` 传入超大字符串（>64 KiB）、含空字节/控制字符或深层嵌套递归结构时，安全截断与受控容错，阻断 DoS 与解析崩溃 | `tests/test_runtime_control_security.py` 工具参数有界截断、字符清洗与递归容错测试 | E5（本轮复验） |
| SF-CTRL-01-A2 | `detect_tool_loop` 传入非列表/非字典元素或非法重复阈值（`bool`/非正数）时安全受控，对大历史施加 1,000 条有界扫描并忽略畸形工具调用 | `tests/test_runtime_control_security.py` 工具循环检测输入类型、阈值容错与有界扫描测试 | E6（本轮复验） |
| SF-CTRL-01-A3 | `detect_edit_failure_loop` 规范化编辑路径穿越分段（折叠 `foo/../app.py` 与 `./app.py`）、过滤空字节路径并对跟踪路径数（256）施加有界约束 | `tests/test_runtime_control_security.py` 编辑失败路径规范化、空字节过滤与状态有界测试 | E7（本轮复验） |
| SF-CTRL-01-A4 | `evaluate_context_pressure` 严格校验非负整数与合法水位；`budget_notice` 校验有限非负秒数（排除 `NaN`/`inf`/布尔绕过）并对路径/工具名有界截断；`ruff check .`、`python scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | `tests/test_runtime_control_security.py`、`tests/test_runtime_budget.py` 与全套静态/单元测试命令 | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-CTRL-01-A1 | `tests/test_runtime_control_security.py::test_canonical_args_defense` | 本地 `pytest -v tests/test_runtime_control_security.py`；GitHub Actions CI `Unit tests` | `5a420854af2421f953f2179750c5926ad34a3b15`；passed；CI [R1 43670652358](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43670652358) |
| E2 | SF-CTRL-01-A2 | `tests/test_runtime_control_security.py::test_detect_tool_loop_defense`、`tests/test_runtime_tool_loop.py::test_detect_tool_loop_identical_consecutive_calls` | 本地 `pytest -v tests/test_runtime_control_security.py`；GitHub Actions CI `Unit tests` | `5a420854af2421f953f2179750c5926ad34a3b15`；passed；CI [R1 43670652358](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43670652358) |
| E3 | SF-CTRL-01-A3 | `tests/test_runtime_control_security.py::test_detect_edit_failure_loop_defense`、`tests/test_runtime_edit_guidance.py::test_detect_edit_failure_loop_triggers_after_consecutive_failures` | 本地 `pytest -v tests/test_runtime_control_security.py`；GitHub Actions CI `Unit tests` | `5a420854af2421f953f2179750c5926ad34a3b15`；passed；CI [R1 43670652358](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43670652358) |
| E4 | SF-CTRL-01-A4 | `tests/test_runtime_control_security.py::test_evaluate_context_pressure_defense`、`tests/test_runtime_control_security.py::test_budget_notice_defense`、`tests/test_runtime_budget.py::*`、`tests/test_runtime_token_guidance.py::*` | 本地 `pytest -v`、`ruff check .`、`python scripts/check_sdd.py`；GitHub Actions CI `Unit tests` 与 `Docker integration tests` | `5a420854af2421f953f2179750c5926ad34a3b15`；passed；CI [R1 43670652358](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43670652358) |
| E5 | SF-CTRL-01-A1 | `tests/test_runtime_control_security.py::test_canonical_args_defense`、`tests/test_runtime_control_security.py::test_oversized_parameter_values_are_not_consumed_as_edit_paths` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-CTRL-01-A2 | `tests/test_runtime_control_security.py::test_detect_tool_loop_defense`、`tests/test_runtime_tool_loop.py::test_detect_tool_loop_identical_consecutive_calls`、`tests/test_runtime_control_security.py::test_both_loop_detectors_scan_only_the_tail_window` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-CTRL-01-A3 | `tests/test_runtime_control_security.py::test_detect_edit_failure_loop_defense`、`tests/test_runtime_edit_guidance.py::test_detect_edit_failure_loop_triggers_after_consecutive_failures`、`tests/test_runtime_control_security.py::test_malformed_edit_call_id_is_ignored_without_hashing` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-CTRL-01-A4 | `tests/test_runtime_control_security.py::test_evaluate_context_pressure_defense`、`tests/test_runtime_control_security.py::test_budget_notice_defense`、`tests/test_runtime_budget.py::*`、`tests/test_runtime_token_guidance.py::*`、`tests/test_runtime_control_security.py::test_unrepresentable_pressure_and_time_are_controlled` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。扫描先截历史尾部；参数预检和编码上限同时约束下游实际消费值。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#85；Merge=`149b127713472b682d5f57fb2724ebe3aa1f38e9`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-CTRL-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #85 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 149b127713472b682d5f57fb2724ebe3aa1f38e9
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-MODEL-01

**目标：** 在 `selfforge_agent.coding.model_client` 编码模型客户端中贯彻 fail-closed、输入防御与资源有界防御。`OpenAICompatibleCodingModel` 与 `FallbackCodingModel` 对 `timeout` 强校验有限正数（阻断 `NaN`/`inf`/`<=0`/`bool`/非数值类型）；`base_url` 严格校验方案（仅允许 HTTPS 或本地 HTTP `localhost`/`127.0.0.1`/`::1`），拒绝凭据（username/password）、查询参数（query）与片段（fragment），非法或空远程 API key fail-closed 报错；`propose` 严格防御不可信输入，校验 `observation` 必须为 `RepairObservation` 实例（阻断非预期对象/字典/畸形输入），并对观测序列化有效负载施加尺寸上限（最大 2 MiB `MAX_OBSERVATION_PAYLOAD_BYTES`），防范超限有效负载引发内存与网络 DoS；模型输出解析捕获截断状态（`finish_reason="length"`）并映射为受控 `ModelError`，自动容错 LLM 返回的 Markdown 代码块（````json ... ````）清洗提取有效 JSON；对解析出的 `CodeEdit` 施加严格边界校验：`path` 拒绝空字节 `\0`、绝对路径与 `..` 路径穿越分段且限制长度（<=1024 字符 `MAX_PATH_CHARS`），`old_text`、`new_text`（<=1,000,000 字符 `MAX_TEXT_CHARS`）与 `rationale`（<=100,000 字符 `MAX_RATIONALE_CHARS`）施加有界长度约束；异常统一映射为受控 `ModelError`，绝不泄露未受控 500/异常。

**范围/入口：** 仅改动 `src/selfforge_agent/coding/model_client.py` 及专项安全测试 `tests/test_coding_model_client_security.py`；保持外部调用接口（`OpenAICompatibleCodingModel`、`FallbackCodingModel`、`CodingModel`）完全向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-MODEL-01-A1 | `OpenAICompatibleCodingModel` 与 `FallbackCodingModel` 对 `timeout` 强校验有限正数（阻断 `NaN`/`inf`/`<=0`/`bool`/非数值类型）；`base_url` 严格校验方案（仅允许 HTTPS 或本地 HTTP `localhost`/`127.0.0.1`/`::1`），拒绝凭据（username/password）、查询参数（query）与片段（fragment），非法或空远程 API key fail-closed 报错 | `tests/test_coding_model_client_security.py` 超时校验、Base URL 凭据与远程密钥校验测试 | E5（本轮复验） |
| SF-MODEL-01-A2 | `propose` 严格防御不可信输入，校验 `observation` 必须为 `RepairObservation` 实例（阻断非预期对象/字典/畸形输入）；对观测序列化有效负载施加尺寸上限（最大 2 MiB），防止超限有效负载引发内存与网络 DoS | `tests/test_coding_model_client_security.py` 观测输入类型校验与有界有效负载测试 | E6（本轮复验） |
| SF-MODEL-01-A3 | 对模型响应截断状态（`finish_reason="length"`）显式映射为受控 `ModelError`；容错 LLM 返回的 Markdown 代码块（````json ... ````），自动剥离标记提取有效 JSON；对非字典、缺失 choice/message 以及畸形输出统一映射为受控 `ModelError`，绝不泄露未受控 500/异常 | `tests/test_coding_model_client_security.py` 截断响应、Markdown 代码块清洗与畸形输出映射测试 | E7（本轮复验） |
| SF-MODEL-01-A4 | 对解析出的 `CodeEdit` 施加严格边界校验：`path` 拒绝空字节 `\0`、绝对路径与 `..` 路径穿越分段且限制长度（<=1024 字符）；`old_text`、`new_text`、`rationale` 施加长度约束；静态检查 `ruff check .`、文档检查 `scripts/check_sdd.py`、`git diff --check`、专项测试与全量非集成测试全部通过 | `tests/test_coding_model_client_security.py`、`tests/test_repair_agent.py` 与全套静态/单元测试命令 | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-MODEL-01-A1 | `tests/test_coding_model_client_security.py::test_coding_model_timeout_validation`、`tests/test_coding_model_client_security.py::test_coding_model_base_url_and_credentials_validation` | 本地 `pytest -v tests/test_coding_model_client_security.py`；GitHub Actions CI `Unit tests` | `1aef15179027db19bf4b3094da968be6014064b8`；passed；CI [R1 43708232832](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43708232832) |
| E2 | SF-MODEL-01-A2 | `tests/test_coding_model_client_security.py::test_coding_model_observation_input_defense` | 本地 `pytest -v tests/test_coding_model_client_security.py`；GitHub Actions CI `Unit tests` | `1aef15179027db19bf4b3094da968be6014064b8`；passed；CI [R1 43708232832](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43708232832) |
| E3 | SF-MODEL-01-A3 | `tests/test_coding_model_client_security.py::test_coding_model_handles_finish_reason_length`、`tests/test_coding_model_client_security.py::test_coding_model_strips_markdown_code_fences`、`tests/test_coding_model_client_security.py::test_coding_model_extracts_json_with_surrounding_commentary`、`tests/test_coding_model_client_security.py::test_coding_model_malformed_response_handling` | 本地 `pytest -v tests/test_coding_model_client_security.py`；GitHub Actions CI `Unit tests` | `1aef15179027db19bf4b3094da968be6014064b8`；passed；CI [R1 43708232832](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43708232832) |
| E4 | SF-MODEL-01-A4 | `tests/test_coding_model_client_security.py::test_coding_model_defensive_bounds_on_code_edit`、`tests/test_coding_model_client_security.py::test_fallback_coding_model_fail_closed_validation`、`tests/test_repair_agent.py::test_model_client_parses_validated_edit`、`tests/test_repair_agent.py::test_model_client_rejects_invalid_json`、`tests/test_repair_agent.py::test_coding_model_switches_provider_after_failure` | 本地 `pytest -v`、`ruff check .`、`python scripts/check_sdd.py`；GitHub Actions CI `Unit tests` 与 `Docker integration tests` | `1aef15179027db19bf4b3094da968be6014064b8`；passed；CI [R1 43708232832](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43708232832) |
| E5 | SF-MODEL-01-A1 | `tests/test_coding_model_client_security.py::test_coding_model_timeout_validation`、`tests/test_coding_model_client_security.py::test_coding_model_base_url_and_credentials_validation`、`tests/test_coding_model_client_security.py::test_constructor_reuses_transport_url_validation` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-MODEL-01-A2 | `tests/test_coding_model_client_security.py::test_coding_model_observation_input_defense` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-MODEL-01-A3 | `tests/test_coding_model_client_security.py::test_coding_model_handles_finish_reason_length`、`tests/test_coding_model_client_security.py::test_coding_model_strips_markdown_code_fences`、`tests/test_coding_model_client_security.py::test_coding_model_extracts_json_with_surrounding_commentary`、`tests/test_coding_model_client_security.py::test_coding_model_malformed_response_handling`、`tests/test_coding_model_client_security.py::test_non_object_response_is_validated_before_usage`、`tests/test_coding_model_client_security.py::test_json_string_values_containing_fences_are_not_treated_as_outer_markdown` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-MODEL-01-A4 | `tests/test_coding_model_client_security.py::test_coding_model_defensive_bounds_on_code_edit`、`tests/test_coding_model_client_security.py::test_fallback_coding_model_fail_closed_validation`、`tests/test_repair_agent.py::test_model_client_parses_validated_edit`、`tests/test_repair_agent.py::test_model_client_rejects_invalid_json`、`tests/test_repair_agent.py::test_coding_model_switches_provider_after_failure`、`tests/test_coding_model_client_security.py::test_edit_path_cannot_resolve_to_directory_or_trailing_parent` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。非对象响应先验证再读usage；JSON字符串内合法backticks不剥离；URL复用校验器，路径拒绝根目录与所有父级分段。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#86；Merge=`ce5a22a51243482adf743033c1719afe555a2e64`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-MODEL-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #86 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / ce5a22a51243482adf743033c1719afe555a2e64
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-WT-01

**目标：** 在 `selfforge_agent.coding.worktree` 核心 Git 工作区与临时分支执行模块中贯彻 fail-closed、输入防御与资源有界防御。`git` 与 `git_bounded` 严格校验 `repo` 参数必须为已存在的非符号链接目录，拒绝空路径、符号链接目录或非 Git 仓库；`args` 严格校验非空字符串序列，拒绝空参数、包含空字节 `\0` 的参数或超大单参数（限制最大 64 KiB `MAX_ARG_CHARS`），执行命令使用非 shell 的 argv 列表调用；`git` 施加最大输出缓冲约束（最大 10 MiB `MAX_GIT_OUTPUT_BYTES`），防范失控命令输出造成内存耗尽崩溃；`deadline` 严格校验有限浮点数（排除 `NaN`、`inf`、`bool` 及非数值类型），当剩余预算耗尽（`<= 0`）时立即 fail-closed 抛出 `TimeoutError`，阻断超时绕过与负超时挂起；`git_bounded` 严格校验 `max_bytes` 为正整数（拒绝 `bool`、`<=0`、非整数类型及超上限 50 MiB `MAX_ALLOWED_BOUNDED_BYTES`），并在达到限制时平稳终止（SIGTERM/SIGKILL）底层子进程，杜绝进程句柄泄漏；`detached_worktree` 对 `revision` 严格校验合法非空 Git 修订标识（拒绝空字符串、空字节 `\0`，严禁以 `-` 开头防止 Git 选项注入），并使用 `--` 选项终止符安全传递修订；`destination` 严格校验非符号链接，拒绝目标位于当前仓库根目录或 `.git` 元数据目录内部，阻断路径穿越破坏主仓库结构；为检出操作合理预留清理预算（至少 0.25 秒），若剩余总预算不足 0.25 秒直接 fail-closed 拒绝；上下文退出时优先调用 `git worktree remove --force`，若遇到不可读残余或元数据冲突安全执行 `git worktree prune` 修剪游离元数据，且保证清理过程中的异常绝不掩盖上下文主体的原始异常。

**范围/入口：** 复用必要共享 `src/selfforge_agent/core/process.py`； 仅改动 `src/selfforge_agent/coding/worktree.py` 及专项安全测试 `tests/test_coding_worktree_security.py`；保持外部调用接口（`git`、`git_bounded`、`detached_worktree`、`WorktreeError`）完全向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-WT-01-A1 | `git` 与 `git_bounded` 严格校验 `repo` 为合法非符号链接目录，拒绝空参数、空字节 `\0` 与超长参数（>64 KiB），命令输出施加 10 MiB 内存保护 | `tests/test_coding_worktree_security.py` 仓库路径、参数安全与空字节/超长参数校验测试 | E5（本轮复验） |
| SF-WT-01-A2 | `git` 与 `git_bounded` 对 `deadline` 严格校验有限浮点数并在预算耗尽时抛出 `TimeoutError`；`git_bounded` 校验 `max_bytes` 正整数并在截断时平稳终止子进程 | `tests/test_coding_worktree_security.py` 截止时间校验、预算耗尽防御与有界读取截断测试 | E6（本轮复验） |
| SF-WT-01-A3 | `detached_worktree` 严格校验 `revision`（拒绝空串、空字节与 `-` 前缀注入）并使用 `--` 传递；`destination` 拒绝符号链接、仓库根目录或 `.git` 内部路径；预留 0.25s 清理预算 | `tests/test_coding_worktree_security.py` 修订注入防御、目标路径隔离与检出预算预留测试 | E7（本轮复验） |
| SF-WT-01-A4 | `detached_worktree` 正常清理临时目录并在异常退出时保留调用者主体异常；支持 `worktree prune` 元数据清理容错；全量回归与静态合规通过 | `tests/test_coding_worktree_security.py`、`tests/test_workspace.py`、`tests/test_repair_agent.py` 与全套静态/单元测试命令 | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-WT-01-A1 | `tests/test_coding_worktree_security.py::test_git_command_argument_and_path_defense` | 本地 `pytest -v tests/test_coding_worktree_security.py`；GitHub Actions CI `Unit tests` | `8a55356c52e3a5c0e16d7ecfdf189aaf73e3af7e`；passed；CI [R1 43711818243](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43711818243) |
| E2 | SF-WT-01-A2 | `tests/test_coding_worktree_security.py::test_git_bounded_input_and_timeout_defense` | 本地 `pytest -v tests/test_coding_worktree_security.py`；GitHub Actions CI `Unit tests` | `8a55356c52e3a5c0e16d7ecfdf189aaf73e3af7e`；passed；CI [R1 43711818243](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43711818243) |
| E3 | SF-WT-01-A3 | `tests/test_coding_worktree_security.py::test_detached_worktree_revision_and_destination_defense` | 本地 `pytest -v tests/test_coding_worktree_security.py`；GitHub Actions CI `Unit tests` | `8a55356c52e3a5c0e16d7ecfdf189aaf73e3af7e`；passed；CI [R1 43711818243](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43711818243) |
| E4 | SF-WT-01-A4 | `tests/test_coding_worktree_security.py::test_detached_worktree_lifecycle_and_cleanup_fault_tolerance`、`tests/test_workspace.py::test_bounded_git_preserves_command_failure`、`tests/test_workspace.py::test_bounded_git_handles_utf8_cut_at_byte_limit`、`tests/test_repair_agent.py::test_verified_repair_keeps_original_tree_untouched` | 本地 `pytest -v`、`ruff check .`、`python scripts/check_sdd.py`；GitHub Actions CI `Unit tests` 与 `Docker integration tests` | `8a55356c52e3a5c0e16d7ecfdf189aaf73e3af7e`；passed；CI [R1 43711818243](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43711818243) |
| E5 | SF-WT-01-A1 | `tests/test_coding_worktree_security.py::test_git_command_argument_and_path_defense`、`tests/test_coding_worktree_security.py::test_git_stops_output_producer_at_limit` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-WT-01-A2 | `tests/test_coding_worktree_security.py::test_git_bounded_input_and_timeout_defense` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-WT-01-A3 | `tests/test_coding_worktree_security.py::test_detached_worktree_revision_and_destination_defense` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-WT-01-A4 | `tests/test_coding_worktree_security.py::test_detached_worktree_lifecycle_and_cleanup_fault_tolerance`、`tests/test_workspace.py::test_bounded_git_preserves_command_failure`、`tests/test_workspace.py::test_bounded_git_handles_utf8_cut_at_byte_limit`、`tests/test_repair_agent.py::test_verified_repair_keeps_original_tree_untouched`、`tests/test_coding_worktree_security.py::test_cleanup_failure_is_visible_after_successful_body` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。复用共享有界进程捕获，stdout/stderr分别限制；正常主体后的清理失败上抛，有主体错误时保留原错误。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#87；Merge=`7910ad956ed8b0fae3f244b4feffb3c26dcf6a03`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-WT-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #87 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 7910ad956ed8b0fae3f244b4feffb3c26dcf6a03
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-RISK-01

**目标：** 在 `selfforge_agent.coding.risk_prioritization` PR 风险优先级排序模块与 `PrioritizedRisk` 契约模型中贯彻 fail-closed、输入防御与资源有界防御。`prioritize_risks` 严格校验 `context` 参数必须为 `PRContext` 实例，拒绝 `None`、字典、字符串等非受控类型（抛出受控 `TypeError`，避免未受控 `AttributeError` 泄漏）；`findings` 参数严格校验必须为 `None` 或 `Finding` 实例列表，拒绝非列表类型及包含非 `Finding` 元素的序列；对 `context.files` 施加最大扫描文件上限（1,000 文件 `MAX_SCAN_FILES`），对全量累积新增代码行施加内存与扫描行数上限（10,000 行 `MAX_ADDED_LINES`），对审查发现施加扫描上限（1,000 条 `MAX_FINDINGS`），阻断超大 PR 或失控 Diff 导致的拒绝服务（DoS）；`PrioritizedRisk` 契约模型严格加固字段长度边界（`file_path <= 500`、`line_number >= 1`、`severity <= 50`、`category <= 100`、`description <= 1000`、`evidence <= 2000`、`rationale <= 500`），禁止任何字符串字段包含空字节 `\0`，强制 `score` 为有限数值（拒绝 `NaN` / `inf`），并在新增行证据超出 2,000 字符时安全截断，确保 `to_risk_target()` 转换为 `RiskTarget` 时绝对安全且不会触发 Pydantic `ValidationError`；保持 `top_k` 严格 fail-closed 校验（1 至 100 整数，拒绝布尔值与非整型），并保持确定性排序规则（按分值降序、文件升序、行号升序、分类升序），以及连续整数排序排名（1..k）。

**范围/入口：** 仅改动 `src/selfforge_agent/coding/risk_prioritization.py` 及专项安全测试 `tests/test_risk_prioritization_security.py`；保持外部调用接口（`PrioritizedRisk`、`prioritize_risks`）完全向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-RISK-01-A1 | `prioritize_risks` 严格校验 `context` 为 `PRContext` 实例、`findings` 为 `Finding` 列表或 `None`，非法类型均以 `TypeError` 受控拒绝 | `tests/test_risk_prioritization_security.py` 上下文与审查发现参数类型校验测试 | E5（本轮复验） |
| SF-RISK-01-A2 | `PrioritizedRisk` 严格加固字符串长度边界、拒绝空字节 `\0` 与非有限 `score`，确保 `to_risk_target()` 安全转换不抛 `ValidationError` | `tests/test_risk_prioritization_security.py` 模型契约边界、空字节防御、分值有限性与目标转换安全测试 | E6（本轮复验） |
| SF-RISK-01-A3 | 对 `context.files`（1,000）、累积新增行（10,000）、总扫描行（10,000）及 hunk（1,000）与发现列表（1,000）施加有界防御，超长行证据安全截断，非 Python 文件与畸形路径安全跳过 | `tests/test_risk_prioritization_security.py` 扫描上限、证据截断与路径防御测试 | E7（本轮复验） |
| SF-RISK-01-A4 | `top_k` 严格保持 1–100 整数 fail-closed 校验；排序结果满足确定性决胜规则与连续排名；全量非集成测试与静态检查通过 | `tests/test_risk_prioritization_security.py`、`tests/test_risk_prioritization.py`、`tests/test_test_generation.py` 与静态/单元测试命令 | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-RISK-01-A1 | `tests/test_risk_prioritization_security.py::test_prioritize_risks_rejects_invalid_context_type`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_rejects_invalid_findings_type` | 本地 `pytest -v tests/test_risk_prioritization_security.py`；GitHub Actions CI `Unit tests` | `ece71341fc08da5d1e12dd865a92b3177d4ce559`；passed；CI [R1 43721461904](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43721461904) |
| E2 | SF-RISK-01-A2 | `tests/test_risk_prioritization_security.py::test_prioritized_risk_boundary_and_null_bytes`、`tests/test_risk_prioritization_security.py::test_prioritized_risk_rejects_non_finite_score`、`tests/test_risk_prioritization_security.py::test_prioritized_risk_to_risk_target_safety` | 本地 `pytest -v tests/test_risk_prioritization_security.py`；GitHub Actions CI `Unit tests` | `ece71341fc08da5d1e12dd865a92b3177d4ce559`；passed；CI [R1 43721461904](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43721461904) |
| E3 | SF-RISK-01-A3 | `tests/test_risk_prioritization_security.py::test_prioritize_risks_truncates_oversized_evidence_for_risk_target`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_bounds_scan_files_and_added_lines`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_handles_findings_with_null_bytes_or_oversized_fields`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_ignores_non_python_and_malformed_paths` | 本地 `pytest -v tests/test_risk_prioritization_security.py`；GitHub Actions CI `Unit tests` | `ece71341fc08da5d1e12dd865a92b3177d4ce559`；passed；CI [R1 43721461904](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43721461904) |
| E4 | SF-RISK-01-A4 | `tests/test_risk_prioritization_security.py::test_prioritize_risks_top_k_validation`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_deterministic_tie_breaking`、`tests/test_risk_prioritization.py::test_prioritize_risks_orders_by_severity_and_category`、`tests/test_test_generation.py::test_generated_test_proves_base_head_difference`、`tests/test_test_generation_safety.py::test_prioritize_risks_validates_top_k` | 本地 `pytest -v`、`ruff check .`、`python scripts/check_sdd.py`；GitHub Actions CI `Unit tests` 与 `Docker integration tests` | `ece71341fc08da5d1e12dd865a92b3177d4ce559`；passed；CI [R1 43721461904](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43721461904) |
| E5 | SF-RISK-01-A1 | `tests/test_risk_prioritization_security.py::test_prioritize_risks_rejects_invalid_context_type`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_rejects_invalid_findings_type` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-RISK-01-A2 | `tests/test_risk_prioritization_security.py::test_prioritized_risk_boundary_and_null_bytes`、`tests/test_risk_prioritization_security.py::test_prioritized_risk_rejects_non_finite_score`、`tests/test_risk_prioritization_security.py::test_prioritized_risk_to_risk_target_safety` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-RISK-01-A3 | `tests/test_risk_prioritization_security.py::test_prioritize_risks_truncates_oversized_evidence_for_risk_target`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_bounds_scan_files_and_added_lines`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_handles_findings_with_null_bytes_or_oversized_fields`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_ignores_non_python_and_malformed_paths`、`tests/test_risk_prioritization_security.py::test_default_reviewer_receives_only_bounded_scan_context` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-RISK-01-A4 | `tests/test_risk_prioritization_security.py::test_prioritize_risks_top_k_validation`、`tests/test_risk_prioritization_security.py::test_prioritize_risks_deterministic_tie_breaking`、`tests/test_risk_prioritization.py::test_prioritize_risks_orders_by_severity_and_category`、`tests/test_test_generation.py::test_generated_test_proves_base_head_difference`、`tests/test_test_generation_safety.py::test_prioritize_risks_validates_top_k` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。先构建有界context再调用baseline，不对完整扫描结果事后截断；补充1000 hunks与10000总扫描行门禁，调用者不被修改。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#88；Merge=`36280f7ac28e6cfb08339d34bb978a84ae1fc56a`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-RISK-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #88 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 36280f7ac28e6cfb08339d34bb978a84ae1fc56a
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-FPR-01

**目标：** 在 `selfforge_agent.runtime.fingerprint` 工作区快照指纹哈希模块中贯彻 fail-closed、输入防御与资源有界防御。`workspace_fingerprint` 严格校验 `root` 参数类型必须为 `Path` 或 `str`（自动规范化为 `Path`），拒绝 `None`、数值、列表等非法类型并抛出受控 `TypeError`，杜绝未捕获 `AttributeError` 泄漏；严格拒绝包含空字节 `\0` 的路径并抛出受控 `ValueError`；对 `root` 路径清洗末尾斜杠（`/`），彻底阻断通过末尾斜杠强制目录解析绕过 `O_NOFOLLOW` 的符号链接根目录漏洞，当 `root` 为符号链接时严格 fail-closed 抛出 `WorkspaceSnapshotError`；严格校验 `deadline` 参数为有限数值（排除 `bool`、`NaN`、`inf` 及非数值类型并抛出受控 `TypeError` / `ValueError`），阻断 `NaN` 绕过超时判定导致挂起；超时（`deadline <= now`）严格抛出 `TimeoutError("Workspace fingerprint timed out")`；严格校验 `cancel` 参数为 `threading.Event` 实例或 `None`（拒绝非法类型抛出 `TypeError`），并在取消事件置位时立即响应中断；在有界深度遍历（`MAX_SNAPSHOT_DEPTH = 128`）基础上增加祖先路径设备与 inode 追踪（`ancestor_dirs`），一旦检测到目录递归自循环（bind mount / 循环链接）立即 fail-closed 抛出 `WorkspaceSnapshotError("Workspace fingerprint detected directory cycle")`；严格阻断非普通文件（FIFO、字符/块设备、套接字等）以及并发替换为非普通文件的挂起风险，非阻塞读取（`O_NONBLOCK`）并拒绝负数文件大小（`st_size < 0`），防范字节统计溢出与挂起 DoS。

**范围/入口：** 仅改动 `src/selfforge_agent/runtime/fingerprint.py` 及专项安全测试 `tests/test_workspace_fingerprint_security.py`；保持外部调用接口（`workspace_fingerprint`、`WorkspaceSnapshotError`、`MAX_SNAPSHOT_DEPTH`）完全向后兼容。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-FPR-01-A1 | `workspace_fingerprint` 严格校验 `root` 为 `Path` 或 `str`，拒绝非法类型（`TypeError`）与空字节（`ValueError`），彻底阻断末尾斜杠符号链接根目录绕过 | `tests/test_workspace_fingerprint_security.py` 路径类型、空字节与末尾斜杠符号链接根目录防御测试 | E5（本轮复验） |
| SF-FPR-01-A2 | `deadline` 严格校验有限数值（拦截 `NaN`/`inf`/布尔），超时严格抛出 `TimeoutError`；`cancel` 严格校验 `Event` 实例并在置位时立即响应中断 | `tests/test_workspace_fingerprint_security.py` 截止时间有限性与取消事件类型状态机测试 | E6（本轮复验） |
| SF-FPR-01-A3 | 祖先目录设备与 inode 集合追踪防御目录自循环（bind mount / 循环链接）；非普通文件与负数文件大小严格 fail-closed 拒绝 | `tests/test_workspace_fingerprint_security.py` 目录递归自循环与负数文件大小防御测试 | E7（本轮复验） |
| SF-FPR-01-A4 | 保证内容哈希确定性、排除项过滤、并发修改感知与向后兼容性；全量回归套件与静态合规通过 | `tests/test_workspace_fingerprint_security.py`、`tests/test_workspace_fingerprint.py`、`tests/test_workspace_delivery.py` 与静态/单元测试命令 | E8（本轮复验） |

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1 | SF-FPR-01-A1 | `tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_rejects_invalid_root_type`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_supports_str_and_rejects_null_bytes`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_rejects_symlink_root_even_with_trailing_slash` | 本地 `pytest -v tests/test_workspace_fingerprint_security.py`；GitHub Actions CI `Unit tests` | `7e24533354b9fbf17f4310dd8f71fd7195f09461`；passed；CI [R1 43735305134](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43735305134) |
| E2 | SF-FPR-01-A2 | `tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_validates_deadline`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_validates_cancel_type` | 本地 `pytest -v tests/test_workspace_fingerprint_security.py`；GitHub Actions CI `Unit tests` | `7e24533354b9fbf17f4310dd8f71fd7195f09461`；passed；CI [R1 43735305134](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43735305134) |
| E3 | SF-FPR-01-A3 | `tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_detects_directory_cycle`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_rejects_negative_file_size` | 本地 `pytest -v tests/test_workspace_fingerprint_security.py`；GitHub Actions CI `Unit tests` | `7e24533354b9fbf17f4310dd8f71fd7195f09461`；passed；CI [R1 43735305134](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43735305134) |
| E4 | SF-FPR-01-A4 | `tests/test_workspace_fingerprint.py::test_snapshot_is_content_based_and_bound_to_workspace`、`tests/test_workspace_fingerprint.py::test_private_and_generated_files_are_excluded`、`tests/test_workspace_evidence.py::test_unreadable_snapshot_revokes_saved_evidence`、`tests/test_workspace_delivery.py::test_commit_workspace_success_and_protected_file_exclusion` | 本地 `pytest -v`、`ruff check .`、`python scripts/check_sdd.py`；GitHub Actions CI `Unit tests` 与 `Docker integration tests` | `7e24533354b9fbf17f4310dd8f71fd7195f09461`；passed；CI [R1 43735305134](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43735305134) |
| E5 | SF-FPR-01-A1 | `tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_rejects_invalid_root_type`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_supports_str_and_rejects_null_bytes`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_rejects_symlink_root_even_with_trailing_slash` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-FPR-01-A2 | `tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_validates_deadline`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_validates_cancel_type` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-FPR-01-A3 | `tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_detects_directory_cycle`、`tests/test_workspace_fingerprint_security.py::test_workspace_fingerprint_rejects_negative_file_size` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-FPR-01-A4 | `tests/test_workspace_fingerprint.py::test_snapshot_is_content_based_and_bound_to_workspace`、`tests/test_workspace_fingerprint.py::test_private_and_generated_files_are_excluded`、`tests/test_workspace_evidence.py::test_unreadable_snapshot_revokes_saved_evidence`、`tests/test_workspace_delivery.py::test_commit_workspace_success_and_protected_file_exclusion`、`tests/test_workspace_fingerprint_security.py::test_root_replaced_during_scan_invalidates_fingerprint` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。遍历前后检查根路径身份，真实根替换导致失败闭合；仍非原子快照。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#89；Merge=`fa7b698087d7c7a803de7dbcb839d12d3bd4889a`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-FPR-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #89 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / fa7b698087d7c7a803de7dbcb839d12d3bd4889a
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-CTX-01

**目标：** 加固模型上下文管理器（ContextManager）与启发式 Token 估算流，强化非信任消息结构与边界参数防御，消除空用户序列 `ValueError` 崩溃与非受控属性异常，健全严苛预算下的迭代减半压缩机制。

**范围/入口：** `src/selfforge_agent/runtime/context.py`（`estimate_tokens`、`estimate_message_tokens`、`_recent_interactions`、`ContextManager`）。不引入模型特定 Tokenizer 依赖，沿用统一启发式估算；复用 `tests/test_context.py`、`tests/test_context_precision.py`，新增专项安全测试 `tests/test_context_security.py`。

**行为：** `estimate_tokens` 对 `None` 安全返回 0，非 `str` 严格 fail-closed 抛出 `TypeError`；`estimate_message_tokens` 严格校验输入为 `dict`，容错非 dict 工具调用、畸形 function 字典、非 JSON 参数与多模态内容列表，防御未受控异常；`_recent_interactions` 在历史会话缺少 user 消息时安全返回 `[]`，阻止 `max()` 空序列 `ValueError`，过滤非 dict 消息与非法 tool_calls 结构，严格保证 assistant/tool 块完整配对；`ContextManager.__init__` 严格校验 `token_budget` 为 `>= 500` 的整数，拒绝 `bool`、`float`、`NaN`、`inf`、负数与字符串；`compact` 严格校验 `reserve_tokens`（正预算剩余）与 `preserve_recent_turns`（非负整数），严格校验 `messages` 为字典列表；压缩过程中在锚点指令与近期交互后，将允许摘要上限减半直至 0（消除 `allowed > 50` 截断盲区），支持在严苛剩余预算下优雅紧缩；旧摘要前缀合并去重，保护所有系统指令与全部用户任务。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-CTX-01-A1 | `estimate_tokens` 与 `estimate_message_tokens` 输入防御与畸形容错：`None` 返回 0，非 `str`/非 `dict` 抛出 `TypeError`，容错畸形 `tool_calls` 与非 JSON 参数 | `tests/test_context_security.py::test_estimate_tokens_input_validation`、`tests/test_context_security.py::test_estimate_message_tokens_fail_closed` | E5（本轮复验） |
| SF-CTX-01-A2 | `_recent_interactions` 边界安全与空序列防御：缺少 user 指令安全返回 `[]`，阻止 `max()` 空序列异常，非 dict 消息与非法 tool_calls 安全过滤并保持完整配对 | `tests/test_context_security.py::test_recent_interactions_without_user_message_returns_empty`、`tests/test_context_security.py::test_recent_interactions_with_malformed_messages_returns_empty`、`tests/test_context_security.py::test_recent_interactions_invalid_limits` | E6（本轮复验） |
| SF-CTX-01-A3 | `ContextManager` 预算类型守门与有界参数防御：构造函数严格校验 `token_budget` 为 `>= 500` 整数，拒绝 `bool`/`float`/`NaN`；`compact` 严格校验 `messages`、`reserve_tokens` 与 `preserve_recent_turns` | `tests/test_context_security.py::test_context_manager_init_type_defense`、`tests/test_context_security.py::test_context_manager_estimate_input_defense`、`tests/test_context_security.py::test_context_manager_compact_input_defense` | E7（本轮复验） |
| SF-CTX-01-A4 | 紧凑预算迭代减半与历史摘要结构化保护：压缩过程中允许摘要上限减半直至 0，消除 `allowed > 50` 截断盲区；旧摘要前缀合并去重，保护系统指令与全部用户任务 | `tests/test_context_security.py::test_compact_iterative_halving_under_tight_budget`、`tests/test_context_security.py::test_compact_handles_malformed_intermediate_messages`、`tests/test_context.py::test_compaction_keeps_every_user_instruction_across_resume` | E8（本轮复验） |

| 记录 | 验收 ID | 验证代码节点 / 检查项 | 命令 / 证据源 | 验证结果 / 提交 SHA |
| --- | --- | --- | --- | --- |
| E1 | SF-CTX-01-A1 | `tests/test_context_security.py::test_estimate_tokens_input_validation`、`tests/test_context_security.py::test_estimate_message_tokens_fail_closed` | `pytest -v tests/test_context_security.py`；GitHub Actions CI `Unit tests` | `b948f3b0948fd41e1b68ac3087c491715fc9373f`；passed；CI [R1 43738473135](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43738473135) |
| E2 | SF-CTX-01-A2 | `tests/test_context_security.py::test_recent_interactions_without_user_message_returns_empty`、`tests/test_context_security.py::test_recent_interactions_with_malformed_messages_returns_empty`、`tests/test_context_security.py::test_recent_interactions_invalid_limits` | `pytest -v tests/test_context_security.py`；GitHub Actions CI `Unit tests` | `b948f3b0948fd41e1b68ac3087c491715fc9373f`；passed；CI [R1 43738473135](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43738473135) |
| E3 | SF-CTX-01-A3 | `tests/test_context_security.py::test_context_manager_init_type_defense`、`tests/test_context_security.py::test_context_manager_estimate_input_defense`、`tests/test_context_security.py::test_context_manager_compact_input_defense` | `pytest -v tests/test_context_security.py`；GitHub Actions CI `Unit tests` | `b948f3b0948fd41e1b68ac3087c491715fc9373f`；passed；CI [R1 43738473135](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43738473135) |
| E4 | SF-CTX-01-A4 | `tests/test_context_security.py::test_compact_iterative_halving_under_tight_budget`、`tests/test_context_security.py::test_compact_handles_malformed_intermediate_messages`、`tests/test_context.py::test_compaction_keeps_every_user_instruction_across_resume` | `pytest -v tests/test_context_security.py tests/test_context.py`；GitHub Actions CI `Unit tests` | `b948f3b0948fd41e1b68ac3087c491715fc9373f`；passed；CI [R1 43738473135](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43738473135) |
| E5 | SF-CTX-01-A1 | `tests/test_context_security.py::test_estimate_tokens_input_validation`、`tests/test_context_security.py::test_estimate_message_tokens_fail_closed` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E6 | SF-CTX-01-A2 | `tests/test_context_security.py::test_recent_interactions_without_user_message_returns_empty`、`tests/test_context_security.py::test_recent_interactions_with_malformed_messages_returns_empty`、`tests/test_context_security.py::test_recent_interactions_invalid_limits`、`tests/test_context_security.py::test_recent_native_call_rejects_invalid_function_shape`、`tests/test_context_security.py::test_compaction_preserves_complete_native_multicall_with_reversed_replies` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E7 | SF-CTX-01-A3 | `tests/test_context_security.py::test_context_manager_init_type_defense`、`tests/test_context_security.py::test_context_manager_estimate_input_defense`、`tests/test_context_security.py::test_context_manager_compact_input_defense` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |
| E8 | SF-CTX-01-A4 | `tests/test_context_security.py::test_compact_iterative_halving_under_tight_budget`、`tests/test_context_security.py::test_compact_handles_malformed_intermediate_messages`、`tests/test_context.py::test_compaction_keeps_every_user_instruction_across_resume`、`tests/test_context_security.py::test_compaction_preserves_every_system_constraint_and_user_task` | `SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`；`SELFFORGE_EVAL_SOURCE_ROOT` 为通过 v3 offline 预检的五源缓存；真 Docker；Ruff/SDD/空白通过 | `25dd53cc31317bdf6a3612f7ca3ed3388487e097`；passed；完整组合 1036 passed、无跳过；[准确组合 head CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572)；[审查记录](./review-pr79-90.md) |

原 E1–E4 保留历史来源，当前验收使用 E5–E8；不同版本的测试数量不相加。保留全部普通system约束与user任务，原生多调用和反序replies成组保留；畸形function结构拒绝，token仍是启发式。

审查整合：Base=`14203249e58444780422567ce98606217ea1ba36`；Branch=`review/pr79-90`；PR=#90；Merge=`224a147e6abe6810edafb6bab0bb91f2dd89b663`；最终受测 Source=`25dd53cc31317bdf6a3612f7ca3ed3388487e097`。

```text
Owner / 状态：Codex（审查整合） / Merged
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：14203249e58444780422567ce98606217ea1ba36 / review/pr79-90 (worktree: selfforge-review-pr79-90) / 已验证源码 25dd53cc31317bdf6a3612f7ca3ed3388487e097；后续对账仅文档
已完成验收与证据 ID：SF-CTX-01-A1..A4（E5–E8）
未完成 / 环境限制 / 未提交内容归属：源码无未提交；真 Docker 完整组合 1036 passed、无跳过；模型只用本地服务/脚本/替身；没有付费模型测量
PR / 准确 head CI / merge SHA（有则填）：PR #90 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43793394572（最终组合；各源 PR 见审查记录） / 224a147e6abe6810edafb6bab0bb91f2dd89b663
仍在运行的本任务进程或 CI（无则写无）：本地任务进程无；最终 main CI 准确结论回填十二个 PR 的交付描述
下一步：后续开发先核对远端与开放 PR，按当前用户任务选择未认领事项；不要自动运行付费模型测量。
```

### SF-DELIV-01

**目标：** 加固工作区交付工作流（`commit_workspace`、`revert_workspace`、`create_pull_request`），防止工作区/会话注入、Git 配置 CRLF 注入、非普通文件（FIFO/设备）暂存、未跟踪软链接穿透删除、选项参数注入与证据错误绑定。

**范围/入口：** `src/selfforge_agent/runtime/delivery.py`（`commit_workspace`、`revert_workspace`、`create_pull_request`、`_run_gh`、`_verified_commit`、`_verified_regression`）。复用 `tests/test_delivery_safety.py`、`tests/test_workspace_delivery.py`、`tests/test_delivery_revert_and_lock_export.py`，新增专项安全测试 `tests/test_delivery_security.py`。

**行为：** `_validate_workspace` 校验 `workspace` 实例类型与常规目录结构（拒绝软链接工作区与损坏路径），校验 `session_id` 与分支名合法性；`commit_workspace` 严格校验 `message` 为非空字符串、有界长度（`MAX_COMMIT_MESSAGE_LENGTH`）与无空字节，严格校验 `author_name` 与 `author_email` 杜绝换行符（CRLF）与空字节（阻止 git config 注入），验证 40 位 16 进制提交 SHA 格式，过滤并拒绝提交 FIFO/设备等非普通文件；`revert_workspace` 严格校验工作区与会话类型，保证在修改磁盘前先持久化撤销会话验证状态，删除未跟踪条目时若为符号链接仅 `unlink` 软链接自身、杜绝穿透删除目标文件；`create_pull_request` 严格校验标题与描述边界（无换行/无空字节/长度上限），对 `base_branch` 与 `remote` 实施命名合法性守门（严禁以 `-` 开头防止命令行选项注入），校验 `draft` 为严格布尔类型；`_run_gh` 严格校验有限正超时（`0 < timeout <= 300`）、有效工作目录、空字节防御与有界输出读取（`MAX_GH_OUTPUT_BYTES`）；`_verified_commit` 与 `_verified_regression` 严格对齐 `session.session_id == workspace.session_id` 与 40 位 16 进制提交 SHA，实现 fail-closed 证据绑定。

| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |
| --- | --- | --- | --- |
| SF-DELIV-01-A1 | `commit_workspace` 参数守门与 Git 配置注入防御：非 CodingWorkspace 实例或非法 message 抛出异常，拒绝空字节与超长消息，严格拒绝 author_name/author_email 中包含换行符（CRLF）与空字节 | `tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_non_workspace_instance`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_non_string_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_empty_or_whitespace_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_null_byte_in_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_oversized_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_crlf_or_null_in_author_name`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_crlf_or_null_in_author_email`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_invalid_author_type` | E1 |
| SF-DELIV-01-A2 | `commit_workspace` 暂存路径安全与文件系统边界守门：软链接工作区路径拒绝，FIFO/设备等非普通文件忽略不予暂存/提交，产出的 commit_sha 严格为规范 40 位 16 进制小写格式 | `tests/test_delivery_security.py::TestCommitWorkspaceStagingAndFilesystem::test_commit_rejects_symlinked_workspace_path`、`tests/test_delivery_security.py::TestCommitWorkspaceStagingAndFilesystem::test_commit_ignores_or_rejects_fifo`、`tests/test_delivery_security.py::TestCommitWorkspaceStagingAndFilesystem::test_commit_validates_commit_sha_format` | E2 |
| SF-DELIV-01-A3 | `revert_workspace` 回滚安全与会话状态撤销原子性：非 CodingWorkspace 或非法 SessionStore 抛出异常，软链接工作区拒绝，磁盘写入前持久化撤销会话验证状态，未跟踪符号链接仅删除链接本身不穿透破坏目标文件 | `tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_rejects_non_workspace_instance`、`tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_rejects_invalid_session_type`、`tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_rejects_symlinked_workspace_path`、`tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_does_not_delete_target_of_untracked_symlink` | E3 |
| SF-DELIV-01-A4 | `create_pull_request`、`_run_gh` 执行与证据绑定加固：标题与描述有界/无空字节/无换行校验，base_branch 与 remote 严禁以 `-` 开头杜绝选项参数注入，draft 严格布尔校验，`_run_gh` 严格有限正超时与工作目录检查，`_verified_commit` 严格校验 session_id 与 commit_sha 格式 | `tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_non_workspace_instance`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_non_string_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_empty_or_whitespace_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_crlf_or_null_in_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_oversized_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_oversized_or_null_body`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_invalid_draft_type`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_option_injection_in_base_branch`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_option_injection_in_remote`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_run_gh_rejects_invalid_or_non_positive_timeout`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_run_gh_rejects_invalid_cwd`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_verified_commit_rejects_mismatched_session_id`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_verified_commit_rejects_invalid_commit_sha` | E4 |

| 记录 | 验收 ID | 验证代码节点 / 检查项 | 命令 / 证据源 | 验证结果 / 提交 SHA |
| --- | --- | --- | --- | --- |
| E1 | SF-DELIV-01-A1 | `tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_non_workspace_instance`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_non_string_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_empty_or_whitespace_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_null_byte_in_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_oversized_message`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_crlf_or_null_in_author_name`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_crlf_or_null_in_author_email`、`tests/test_delivery_security.py::TestCommitWorkspaceInputAndInjection::test_commit_rejects_invalid_author_type` | `PYTHONPATH=src uv run --no-sync pytest tests/test_delivery_security.py -k TestCommitWorkspaceInputAndInjection`；GitHub Actions CI `Unit tests` | `bb2bd5c974fd98648738df1c1a8bacf09d0113bd`；passed；CI [R1 43847067146](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43847067146) |
| E2 | SF-DELIV-01-A2 | `tests/test_delivery_security.py::TestCommitWorkspaceStagingAndFilesystem::test_commit_rejects_symlinked_workspace_path`、`tests/test_delivery_security.py::TestCommitWorkspaceStagingAndFilesystem::test_commit_ignores_or_rejects_fifo`、`tests/test_delivery_security.py::TestCommitWorkspaceStagingAndFilesystem::test_commit_validates_commit_sha_format` | `PYTHONPATH=src uv run --no-sync pytest tests/test_delivery_security.py -k TestCommitWorkspaceStagingAndFilesystem`；GitHub Actions CI `Unit tests` | `bb2bd5c974fd98648738df1c1a8bacf09d0113bd`；passed；CI [R1 43847067146](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43847067146) |
| E3 | SF-DELIV-01-A3 | `tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_rejects_non_workspace_instance`、`tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_rejects_invalid_session_type`、`tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_rejects_symlinked_workspace_path`、`tests/test_delivery_security.py::TestRevertWorkspaceSafetyAndEvidence::test_revert_does_not_delete_target_of_untracked_symlink` | `PYTHONPATH=src uv run --no-sync pytest tests/test_delivery_security.py -k TestRevertWorkspaceSafetyAndEvidence`；GitHub Actions CI `Unit tests` | `bb2bd5c974fd98648738df1c1a8bacf09d0113bd`；passed；CI [R1 43847067146](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43847067146) |
| E4 | SF-DELIV-01-A4 | `tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_non_workspace_instance`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_non_string_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_empty_or_whitespace_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_crlf_or_null_in_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_oversized_title`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_oversized_or_null_body`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_invalid_draft_type`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_option_injection_in_base_branch`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_create_pr_rejects_option_injection_in_remote`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_run_gh_rejects_invalid_or_non_positive_timeout`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_run_gh_rejects_invalid_cwd`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_verified_commit_rejects_mismatched_session_id`、`tests/test_delivery_security.py::TestCreatePullRequestAndGHOps::test_verified_commit_rejects_invalid_commit_sha` | `PYTHONPATH=src uv run --no-sync pytest tests/test_delivery_security.py -k TestCreatePullRequestAndGHOps`；GitHub Actions CI `Unit tests` | `bb2bd5c974fd98648738df1c1a8bacf09d0113bd`；passed；CI [R1 43847067146](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43847067146) |

```text
Owner / 状态：Codex（审查整合） / In Review
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：c5cbeffe332964d08b712036919873aeb1e13f46 / feat/sf-deliv-01 (worktree: selfforge-sf-deliv-01) / bb2bd5c974fd98648738df1c1a8bacf09d0113bd
已完成验收与证据 ID：SF-DELIV-01-A1..A4（E1–E4）
未完成 / 环境限制 / 未提交内容归属：无
PR / 准确 head CI / merge SHA（有则填）：PR #94 / https://github.com/liuzhouquan/selfforge-agent/actions/runs/43847067146 / 未合并
仍在运行的本任务进程或 CI（无则写无）：无
下一步：等待 PR #94 审查与合并。
```

## 5. 验收追踪与执行记录格式

每项验收使用全局 ID `任务ID-A序号`，实现后把上表“初始证据”替换为相应记录 ID（如 E1）。在**同一规格节**追加以下表，不要求再建 task 文件：

| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |
| --- | --- | --- | --- | --- |
| E1（模板，未执行） | 所属任务-A1 | `tests/实际文件.py::实际测试函数` | 完整命令；mock/Host/Docker/真实服务分清 | 完整实际 SHA；passed/failed/skipped；真实 CI 链接 |

不能将模板当作通过：

- 对缺陷先保存旧实现的实际失败证据，再记录修复后同用例成功；新功能证明关键反例能区分未实现行为。
- 直接关联具体测试节点，不能仅写“全套通过”。CLI/API 消费者、错误/边界/恢复路径都需覆盖，不能仅测内部 helper。
- 记录对应测试树/提交、环境/镜像和命令；历史数目不相加，skip 不算通过。源码再改动后原证据失效，按相关风险重跑；仅文档变化不要求重复本地 pytest。
- 自引用问题：提交时不可能预知自身 SHA。记录被验证的源码提交 S；最后的状态/证据提交可另有 SHA，写明仅文档差异；最终 PR head H 的 CI 必须另记。不要为了填 SHA 反复改同一提交或伪造值。
- Merged 后填准确 merge SHA/PR，验收完整才闭合；合并后的 main CI 结果放 PR 交付记录，并在此链接。历史旧 Handoff 改为历史标签，不能继续充当当前状态。
- 合并 SHA 只能在合并后取得：源 PR 合并前保持 In Review；合并后由 Owner 用后续 docs-only 状态提交回填源 merge SHA/PR，状态提交不另建开发任务，也不递归记录自身 SHA。若用户先在网页合并，下一位 Agent 开工先对账补齐，再领取任务。当前没有 GitHub 事件自动同步状态的能力，不能声称文档会自动保持最新。
- 本规则由 [check_sdd.py](../scripts/check_sdd.py) 执行有界结构与本地引用检查；该检查只校验一致性与可追溯性，**不证明测试执行或语义充分**，不能替代实际测试与人工审查。

已合并行为的追踪示例（现有节点，不是未来任务的证据）：

| 行为 | 具体测试节点 | 已核验版本/证据 |
| --- | --- | --- |
| 无效声明不能报告 fresh | `tests/test_incoming_environment_regressions.py::test_invalid_declarations_fail_closed` | 产品基线 SHA；[PR #51 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43134720223)及基线 main CI |
| 不完整存储扫描保留 unknown quota | `tests/test_incoming_storage_regressions.py::test_api_preserves_incomplete_scan_and_unknown_quota` | 产品基线 SHA；[PR #54 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43135327949)及基线 main CI |
| 压缩/恢复后提示描述实际历史 | `tests/test_runtime_token_guidance.py::test_first_compacted_request_and_resume_report_actual_history` | 产品基线 SHA；[PR #53 CI](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43134727156)及基线 main CI |

## 6. 安装与验证门槛

Python 3.11+、Git、uv；CI 为 Python 3.12。仓库未配置类型检查器，Ruff 不能称作类型检查。

```sh
uv sync --locked --dev
uv run --no-sync ruff check .
uv run --no-sync python scripts/check_sdd.py
# 修改行为时，先运行该验收具体节点，再做最终完整组合
uv run --no-sync pytest -q -m 'not integration'
```

Docker 集成还需 daemon、镜像与固定评测源。按[评测准备说明](./benchmark.md#multi-file-candidate-qualification)使用共享准备入口（默认 `.local/benchmark/repos`，或 `--root` / `SELFFORGE_EVAL_SOURCE_ROOT`；已有合格缓存可传 `--offline`）：

```sh
docker build -t selfforge-agent-sandbox:latest -f docker/Dockerfile .
uv run --no-sync python scripts/prepare_eval_sources.py
SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q
```

`SELFFORGE_EVAL_SOURCE_ROOT` 可指向已有合格缓存父目录。镜像覆盖会生成独立临时清单/哈希，不是历史冻结镜像重测；不得把本命令写成历史模型成绩。无需 API Key。离线无缓存时明确缺少固定源，不能静默改 Host 或删除集成测试。

按改动执行：源代码需相关回归 + 最终完整 pytest + Ruff；CLI/API 需实际序列化/退出码；JS 需 `node --check src/selfforge_agent/api/static/app.js`，交互需浏览器 smoke 或明确未覆盖；文档-only 检查本地链接/锚点、源码/测试引用、状态与 `git diff --check`。CI 的不重叠单元/Docker 两组可证明完整集合，不能相加不同版本的结果。构建网络只用于明确准备；被测仓库代码仍在无网络 Docker 中运行。

## 7. 审查、合并与交接

交付前逐条核对：范围/依赖、每个验收 ID 的证据、异常/取消/部分写入、权限和内容证据、消费者、敏感 diff、旧回归和历史成绩。不得删测试/弱化断言来换通过。

按当前会话授权提交/PR/合并；接手他人分支保留原提交，修复另加提交。只合并已审查、准确 head CI 通过的提交。若 main 前进，解决语义冲突，保留所有有效行为，验证组合；不使用强推/强制清理绕过。多 PR 按显式依赖排序，最后核对 main tree 和 main CI。

每次结束在**当前规格节**追加交接：

```text
Owner / 状态：
实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：
已完成验收与证据 ID：
未完成 / 环境限制 / 未提交内容归属：
PR / 准确 head CI / merge SHA（有则填）：
仍在运行的本任务进程或 CI（无则写无）：
下一步：一条具体操作，不能只写“继续完善”。
```

新 Agent 先核对 Git/PR/CI 的实时状态，再信任旧交接。模板不是权限确认流程；已有授权范围内的常规设计和可逆修复自主完成。

## 8. 已合并状态快照与历史资料

以下是产品基线时的状态，供避免重复开发；完整早期已完成项保留于[历史 Backlog](./backlog.md)。不得从旧任务卡顶部/旧 Handoff 复活任务。

| 任务 ID | 状态 | PR | 合并 SHA |
| --- | --- | --- | --- |
| SF-PROV-01 | Merged | [#55](https://github.com/liuzhouquan/selfforge-agent/pull/55) | `944cfca4c1196fdb69ec29d6e23ea9b6a0e267cc` |
| SF-EVAL-06 | Merged | [#48](https://github.com/liuzhouquan/selfforge-agent/pull/48) | `9e791cdd74920f57fa9a65347af613ba17ec51f3` |
| SF-RUN-05 | Merged | [#49](https://github.com/liuzhouquan/selfforge-agent/pull/49) | `34d34681e4631a168197c6decdbe7f26b6cee292` |
| SF-WS-03 | Merged | [#50](https://github.com/liuzhouquan/selfforge-agent/pull/50) | `873ee43c92d0be4ec260b97bd24cfcf428863180` |
| SF-ENV-06 | Merged | [#51](https://github.com/liuzhouquan/selfforge-agent/pull/51) | `c9bba482e30187755017d4b505aa5bb04fb30e34` |
| SF-EVAL-07 | Merged | [#52](https://github.com/liuzhouquan/selfforge-agent/pull/52) | `4f3af88285db99b9d8f49a2185471bad6b0a0500` |
| SF-RUN-06 | Merged | [#53](https://github.com/liuzhouquan/selfforge-agent/pull/53) | `17f9d14094a707a0afe392fb86fd5aa10b2d0db1` |
| SF-WS-04 | Merged | [#54](https://github.com/liuzhouquan/selfforge-agent/pull/54) | `04452154addaeff3375fcd15996585c2d5e374e3` |

整合问题、修复与验证见 [PR #48–#54 审查](./review-pr48-54.md)。这里只引用已有证据，不把实现过程中的历史失败删除或重新计分。

## 9. 可直接复制的接手指令

```text
继续开发 selfForge Agent，以 docs/SDD.md 为统一规格和当前状态入口。
先核对 Git、origin/main、未提交改动和开放 PR；使用自己的 worktree。
执行用户指定任务；否则只领取最高优先级、未认领且前提满足的一项 Ready。
在该规格节登记 Owner/base/分支，按验收 ID 写有区分能力的回归，复用既有 Runtime/Runner。
保持 SDD 的权限、状态恢复和测试证据约束；只查涉及模块的详细契约。
用测试节点、命令、实际 SHA 与 CI 回填每条验收，未验证不能写通过。
只交付这一项任务；按本会话授权提交/PR/整合，同步 SDD 状态并留下具体下一步。
不要重复创建 task 文件，不把历史成绩或计划中的能力描述为当前已实现。
```
