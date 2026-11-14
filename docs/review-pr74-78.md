# PR #74–#78 审查与整合

2026-11-14；基点 `68e56fb3ffb61439b00a90a81586d983af33a601`。按用户授权审查、另加修复提交并依次合并，保留原始提交；[SDD](./SDD.md) 是当前任务状态入口。

## Standards

规范轴初审确认 6 项主要问题，最终只读复核无剩余阻断：

- #75 holdout 单次 `os.write` 未检查短写，可能只写入通过断言、丢掉后续失败断言，却仍使用完整输入哈希。现在通过普通文件缓冲写入保证完整字节，并用目录描述符锚定安装位置。
- #76 `_observation` 经父目录别名可读取 `.env` 中源码，内容可能进入模型提示。现在同时检查解析后的保护组件，逐级 no-follow 打开父目录，特殊文件、循环与不安全路径安全略过。
- #76 顶层 Assign、类体、默认参数和装饰器可隐藏执行调用。现在筛查声明初始化表达式，同时保留字面量常量及合理 pytest fixture/parametrize；AST 筛查不是 Python 沙箱，imports 与测试本身仍需 Docker 或明确 Host 信任。
- #77 请求体上限仅相信 Content-Length，无头 chunked 或伪造较小长度可以绕过。现在在端点解析/动作前累计实际 ASGI 请求字节并有界重放，超限固定返回 413。
- #77 FIFO 取消标记导致写端 open 阻塞。现在 DurableCancel 与管理器共享 nonblocking/no-follow 普通文件写入检查，FD 始终关闭。
- #78 工件 JSON 递归异常没有收敛。现在解析错误统一转为受控 ValueError，所有读取错误路径均在 finally 释放 FD。

本轴最严重项为 holdout 字节完整性和受保护观察内容泄漏。#74 未发现确定阻断，保持其原实现；锁条件、PEP 503 与读取兼容专项 48 passed。

## Spec

规格轴确认并修复以下边界，最终复核无剩余阻断：

- #75 session 未初始化保护仍遗漏尾部成本读取和坏遥测异常；现在 TypeError/KeyError/AttributeError 等均返回分类 harness outcome，不覆盖主结果。合法成本在临时会话删除前保存；验证只使用脚本模型和显式测试费率，不是实际账单。
- #75 冻结 manifest/holdout 字节也必须遵守 2 MiB/1 MiB 上限，不能只限制文件读取入口。holdout 父目录替换不能写到工作区外，失败写入会清除本次创建的文件。
- #76 大整数行号不能被 `math.isfinite` 隐式浮点转换而抛 OverflowError；正整数直接校验。top_k 拒绝布尔值；因果文本先做有界字符前缀再编码，避免为截断复制完整巨大文本。
- #77 仓库预检验证真实 Git 根目录，伪造 `.git` 不算仓库；损坏 state 等持久记录安全忽略；writer 和 reader 均限制 64 KiB，避免生成自己无法恢复的记录。
- #77 真实坏回归日志此前被 reader 清洗成 None，API 仅 mock 坏字典时才返回 422。现在共享 reader 在同一次有界扫描附带末条确定损坏状态，API strict 读取对已提交坏 JSON/非对象返回脱敏 422；缺失、空、主动失效、旧格式过滤、未提交尾部、忙锁/不完整读取仍沿用无可用记录语义。默认确认逻辑不抛错且继续撤销证据；损坏中段后的合法新记录恢复 200。必要共享接入位于 `runtime/session.py`。
- #78 版本号须为整数 1，拒绝 True；完整字段、case/repeat 与文件名身份、两侧 TestResult 摘要均需一致。读取严格 bounded/no-follow，非法结构不能成为可用工件。
- #78 排他创建不等于完整文件原子可见。现在私有临时文件完整写入并同步后，用 dirfd link 原子发布，拒绝覆盖任何既有目标并清理临时项。
- #78 命令上限按 UTF-8 字节而非字符计算；布尔值、NaN/inf、无法用作超时的巨大整数均在进程启动前拒绝。

本轴最严重项为损坏证据/工件仍被消费及边界异常逃逸。声明筛查与实际请求限制的交叉发现仍分别保留在两轴。

## 验证证据与范围

- 共 39 个新增负例在原行为/修复前实际失败，另有 5 个兼容控制。包含短写丢断言、安装目录替换、冻结输入超限、成本丢失与坏遥测、声明内执行、受保护目录别名、循环路径、大整数、chunked 请求、FIFO 取消、伪 Git、坏作业、真实坏回归日志、工件身份/结构/FD/原子可见性及 UTF-8 命令。修复后四套专项 83 passed；无外部模型调用。
- 相关兼容验证：锁文件/依赖/导出 48 passed；benchmark/隔离/冻结策略 24 passed；测试生成/因果/风险 42 passed、2 integration deselected；API/持久作业/Origin 33 passed；最终工件/日志/API组合 67 passed。专项数量与完整组合不累加，deselected 不算通过。
- JSON 递归及 fstat 故障使用显式错误注入；它们证明异常收敛和 FD 生命周期，不冒充真实恶意仓库安全测试。短写、目录替换、FIFO、HTTP 和日志案例使用真实临时文件/服务。
- 最终受测源码 `dc6ff236e46c59e7e3fb7ed772947093ca3cb698`；完整组合：**878 passed，0 skipped**，628.36 秒；1 条既有 Starlette/httpx 弃用警告。
- 命令：`SELFFORGE_EVAL_SOURCE_ROOT=<通过 v3 --offline 的五源缓存> SELFFORGE_EVAL_IMAGE=selfforge-agent-sandbox:latest uv run --no-sync pytest -q`。实际 Docker 覆盖 15 项集成及 22 题无模型参考正负控。基础镜像 `sha256:8d45ef8ef046ddf7320d86bb0a10d2f09855e8765f41874397604d15bb4b7853`；锁定依赖镜像 `sha256:6b13b3c0348cf54585daf0e9ceff7b072efe91cbf76ad9c1bbd595b019b18e1d`。
- 真实 uvicorn/HTTP 客户端 smoke：2.4 MB 无 Content-Length 的 chunked 请求返回 413；小型无效 JSON DTO 正常返回 422。另有 TestClient/真实 Git/真实 SessionStore 覆盖持久化和坏尾恢复。没有 UI/JS 修改；本任务临时服务已关闭。
- Ruff、SDD 结构/本地引用与空白检查通过；SDD 检查不证明测试语义。未修改冻结评测资产、历史模型成绩，未执行付费模型测量。
- 最终复核独立定向测试：规范轴 24 passed、规格轴 23 passed；选择有重叠，不与完整组合相加。

## 远端交付

| PR | 准确审查 head | 准确 head CI | Merge SHA |
| --- | --- | --- | --- |
| [#74](https://github.com/liuzhouquan/selfforge-agent/pull/74) | `41949826bfe7b3221dc9c455a85f8a202a88f1d1` | [43572163341](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43572163341)：成功（单元与 Docker） | `b14b297a429d5e78a8347347e1705a8528add509` |
| [#75](https://github.com/liuzhouquan/selfforge-agent/pull/75) | `8d6ae9f8e9ee172d75576c50479516d4c0d45549` | [43598756462](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43598756462)：793 单元 + 15 Docker，通过 | `446c94eb76520118dae889d2c8b5b2f3093c69e8` |
| [#76](https://github.com/liuzhouquan/selfforge-agent/pull/76) | `51200833565ac2a3574da34df1cf895d1265ffe2` | [43599288215](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43599288215)：813 单元 + 15 Docker，通过 | `1ba9512f76cec1c9496a2bfa6cf75b53b2b6a53e` |
| [#77](https://github.com/liuzhouquan/selfforge-agent/pull/77) | `eb33a15948cd38e22f4fab4663faf3571d4c0e74` | [43599899516](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43599899516)：830 单元 + 15 Docker，通过 | `199bac4bb397d22ea3cb9e408090ae01f63539e5` |
| [#78](https://github.com/liuzhouquan/selfforge-agent/pull/78) | `dc6ff236e46c59e7e3fb7ed772947093ca3cb698` | [43600944896](https://github.com/liuzhouquan/selfforge-agent/actions/runs/43600944896)：863 单元 + 15 Docker，通过 | `843a3d5d88a08dd1f2b6ab8d1751f89ed30fa543` |

#74 保留原准确 head；后续分支累计集成前序原始历史和修复。#78 最终组合还包含 #75 的遥测、#76 的循环路径和 #77 的真实回归日志收尾。最终 main CI 结论回填 [PR #78 交付描述](https://github.com/liuzhouquan/selfforge-agent/pull/78)；后续 SDD 状态对账只改文档。
