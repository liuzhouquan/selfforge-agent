# SF-RUN-03：准确记录中断、超时与异常场景下的步数归因

> 已核对：PR #39 已合并，merge SHA `071afd62dd3a4a72c7c9ebba36c613fdc9318f4e`。以下规格/测试是历史记录；当前开发以 [SDD](../SDD.md) 为准，不重新领取本任务。

- 状态：Merged（历史任务）
- Owner：Antigravity Agent
- 基线：`7eabefc67439432001921e5dbe81aad50b2bfc18`
- 分支：`feat/runtime-step-attribution-on-interruption`
- 依赖：PR #36 已合并入 main；PR #37 发现 `more_itertools_replace_limit` r3 报告 `steps=0 tools=4`
- PR：[PR #39](https://github.com/liuzhouquan/selfforge-agent/pull/39)

## 目标与范围

在 20 题 × 3 次真实模型评测（SF-EVAL-03）中，`more_itertools_replace_limit` repeat 3 发生超时失败，报告记录为：
`reason=agent_timed_out steps=0 tools=4 vis=8/0 hold=3/7`。
即使该用例在超时前已成功执行了 4 次工具调用，其实际步数却被记录为 `0`。

排查发现：
在 [`src/selfforge_agent/runtime/agent.py`](file:///home/lzq/projects/studyfor26job/verified-pr-agent/src/selfforge_agent/runtime/agent.py) 的 `CodingAgent.run()` 方法末尾，异常捕获块：
```python
        except KeyboardInterrupt:
            return self._finish("cancelled", "", 0, tools_used)
        except ModelCallCancelled:
            return self._finish("cancelled", "", 0, tools_used)
        except ModelCallTimedOut as exc:
            self.session.trace("error", error_type=type(exc).__name__, message=str(exc))
            return self._finish("timed_out", "", 0, tools_used, str(exc))
        except TimeoutError as exc:
            return self._finish("timed_out", "", 0, tools_used, str(exc))
        except (RuntimeError, ValueError, OSError) as exc:
            self.session.trace("error", error_type=type(exc).__name__, message=str(exc)[:300])
            return self._finish("failed", "", 0, tools_used, str(exc))
```
所有 5 类异常处理中均硬编码传入 `steps=0`，直接丢弃了在异常发生前已实际完成的步数进度。

本次改进：
- 在 `CodingAgent.run()` 中维护精确的 `completed_steps`：
  - 进入循环前初始化为 0。
  - 每当一次模型调用成功返回时，更新 `completed_steps = step`。
  - 若在第 `step` 轮的模型调用期间发生超时或取消，`completed_steps` 保持为 `step - 1`，忠实反映此前已完成的轮次数。
  - 若在工具执行期间发生超时或异常，`completed_steps` 保持为当前 `step`，忠实反映当前轮次已接收模型决策。
  - 在循环末尾的各个异常捕获处理中，传入真实的 `completed_steps`，保证 CLI 打印、Web 任务序列化、测评报告中的步数统计与真实执行轨迹一致。

本次不做：不改变超时/取消判定条件；不修改各异常类型的 status 映射；不放宽完成门槛。
预计触及：`src/selfforge_agent/runtime/agent.py`、`tests/test_model_deadline.py`、`docs/backlog.md`。
复用：现有 `_finish()` 接口及 Session 状态记录。
约束：[Runtime 约束](../engineering-invariants.md#runtime-model-protocol-and-context)。

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | Agent 在执行 2 轮工具后第 3 轮模型调用超时 / 观察 `result.status == "timed_out"`, `result.steps == 2`, `result.tools_used == 2` | 单元测试断言，证明旧代码因 `assert 0 == 2` 失败 | 已通过（旧实现失败，新实现通过） |
| A2 | Agent 在执行 1 轮工具后第 2 轮模型调用被取消 / 观察 `result.status == "cancelled"`, `result.steps == 1`, `result.tools_used == 1` | 单元测试断言，证明旧代码因 `assert 0 == 1` 失败 | 已通过（旧实现失败，新实现通过） |
| A3 | Agent 在第 2 轮工具执行中发生 `TimeoutError` / 观察 `result.status == "timed_out"`, `result.steps == 2` | 单元测试断言 | 已通过 |
| A4 | 在首轮模型调用前（如环境绑定或压缩中）发生超时或停止 / 观察 `steps == 0` | 现有测试及边界验证 | 已通过 |
| A5 | 完整测试集回归无破坏 | `uv run --no-sync pytest -q` | 已通过（375 单元测试通过，11 Docker 集成测试通过） |

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `uv run --no-sync pytest -q tests/test_model_deadline.py` | 本地 Host Python 3.12 | `feat/runtime-step-attribution-on-interruption` | 7 passed in 0.24s |
| `uv run --no-sync pytest -q tests/test_runtime*.py tests/test_session_environment.py` | 本地 Host Python 3.12 | `feat/runtime-step-attribution-on-interruption` | 71 passed, 1 skipped in 60.82s |
| `uv run --no-sync pytest -q` | 本地 Host Python 3.12 | `feat/runtime-step-attribution-on-interruption` | 375 passed, 11 skipped in 348.56s |
| `uv run --no-sync pytest -q -m integration` | 本地 Docker 29.8.0 | `feat/runtime-step-attribution-on-interruption` | 11 passed in 52.20s |
| `uv run --no-sync ruff check .` | 静态检查 | `feat/runtime-step-attribution-on-interruption` | All checks passed |
| `git diff --check` | 差异检查 | `feat/runtime-step-attribution-on-interruption` | Clean (0 issues) |



审查补充：steps 表示本次 run 已返回的模型轮次，不是工具数或会话累计轮数。超时模型调用本身不计入；工具阶段异常保留该已返回模型轮次。原 53/60 报告保持冻结，不用新的遥测规则改写历史计数。
