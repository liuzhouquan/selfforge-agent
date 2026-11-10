# Providers PR #55 审查与修复（2026-11-10）

仓库：selfForge Agent；PR [#55](https://github.com/liuzhouquan/selfforge-agent/pull/55)。原候选 `3a431ad7e536d1d0dd6fe776b2b9bff937881954`，main基线 `4ecd6a5d605c7f3b5b9140f00cd1a5c3f6360c7a`。审查在独占 `review/provider-presets-20261110` worktree 完成，不改主工作区原有 cli.py/backlog.md 未提交内容；不与GoStream仓库合并。

## Standards

**P1 · 健康检查误报**：原新增通用分支对任意200 JSON直接返回ready/“API Key有效”，包括空对象、error对象和非法列表。实际函数反例失败后，修复为非空且每项有有效ID的models envelope校验；返回“目录可访问，推理权限与额度尚未验证”。保留401/403/402和传输失败分类、不泄漏Key。目录读取不计作成功推理。

**CI门槛**：原PR run 42570659435因cli.py导入排序失败；按现有Ruff规则修正，无新增风格偏好。跨进程配置锁、原子0600存储、URL校验、原生Runtime和共享fallback deadline保留，没有新增Runtime或私有状态系统。

## Spec

**P1 · 默认值与协议消费者**：原Claude默认claude-3-5-sonnet-20241022已退役；Review/Repair发送的json_object被官方兼容层拒绝。Anthropic确实支持OpenAI兼容及Bearer，不能误报为整个协议或鉴权错误。

修复：Claude预设改为当前文档中的Sonnet4.6；原生Anthropic endpoint的专项请求省略不兼容json_object，系统提示和Pydantic输出验证仍保留，非法JSON依然拒绝。其他兼容端点保持原JSON模式。Runtime原生工具调用路径不需另建适配器。

更新OpenCode为Chat Completions入口与glm-5.3，模型目录使用独立的inference/v1/models。仅默认OpenCode入口采用该目录，自定义Base URL仍从自定义端点列举模型，不会转发其密钥到默认目录。同步Gemini alias、Grok4.3及HTML/任务卡旧值，用户现存手动配置不被自动改写。

**能力边界**：chatgpt是OpenAI API预设，不是ChatGPT订阅/OAuth/Codex登录；Claude key需具备相应API/workspace权限，未新增workspace选择器。默认模型和路由需以用户账户目录/权限为准。本轮没有使用真实密钥或调用付费推理，不能宣称九家均已live验证。

## 官方核对与限制

- [Anthropic OpenAI compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)：确认base URL/Bearer与JSON参数支持范围。
- [Claude model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)：原Sonnet3.5于2025-10-28退役。
- [OpenCode inference API](https://opencode.ai/docs/console/api/inference/)：Chat Completions入口和独立模型目录；使用文档明确的chat模型，未把订阅或免费模型自动当作通用授权。
- [Gemini models](https://ai.google.dev/gemini-api/docs/models)与[OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)：采用官方索引中的flash-latest别名；本轮工具未能读取正文，未验证账户实际推理。
- [xAI model migration](https://docs.x.ai/developers/migration/may-15-retirement)：更新旧beta默认值，用户仍可选择其实际可用模型。

外部服务模型列表会变化，静态预设不是可用性保证。保留刷新列表/手动输入及环境变量覆盖，不执行无授权的计费探测。

## 验证

- Python3.12.13，`uv sync --locked --dev --default-index https://pypi.org/simple`；原镜像403，锁文件未修改，官方索引安装成功。
- 新增真实健康函数和专项消费者请求回归：5种坏目录响应、Claude Review/Repair请求参数，旧代码6项失败；修复后通过。另验证独立OpenCode目录/自定义目录与Claude非法输出仍拒绝。
- 相关模型/配置/API/Repair/deadline回归42项通过；该选择与后面的全套重叠，不能相加。
- 最终完整 `uv run --no-sync pytest -q`：**491 passed，0 skipped**，461.54秒，包含可用Docker隔离测试。1条已有Starlette/httpx弃用警告，未称为零告警。
- Ruff、JS语法与diff检查通过。未独立重跑浏览器表单交互；API序列化和UI消费参数由既有TestClient/源码检查覆盖，不冒充真实浏览器或live供应商演示。
- 原始测试输出在临时诊断目录，不提交密钥、providers.json、私有会话或trace。合并只依据PR最后head的CI。

## Handoff

提交修复到原PR分支，不强制推送、不覆盖他人未提交内容。CI通过后合入GitHub main；原主worktree因有未提交修改保持不动，远端主线及本独占工作区的合并验证结果为交付依据。剩余：用户账户的实际可用模型/推理权限验证，以及可选的真实浏览器表单演示，不编造live结果。

规范轴：1项P1和1项现有CI规则失败；需求轴：默认模型/消费者协议2类P1与能力边界修正。未削弱权限、输出验证、deadline或测试断言。
