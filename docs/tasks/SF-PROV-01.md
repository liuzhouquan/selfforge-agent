# SF-PROV-01：扩充多模型供应商预设与通用健康检查机制

- 状态：In Review
- Owner：Antigravity Agent
- 基线：`4ecd6a5b6727fb2e2938cf15c929a7001ae4f849`（`origin/main`）
- 分支 / 所属 worktree：`feat/expand-provider-presets`
- 依赖：无（基于已合并的 PR #47 / main）
- PR：[#55](https://github.com/liuzhouquan/selfforge-agent/pull/55)

## 目标与范围

在原有仅内置 `deepseek` 与 `openrouter` 两个主力供应商预设的基础上，扩充支持开发者常用的 7 家主流大模型供应商预设（`opencode`、`chatgpt`、`claude`、`gemini`、`grok`、`kimi`、`glm`），并在保持安全隔离、防 SSRF、Key 防泄漏、跨进程文件锁和共享 Fallback Deadline 等工程不变量的前提下，统一健全 `/models` 通用健康检查机制与全链路 UI/CLI 配置能力。

原来发生什么：
1. 内置预设仅覆盖 DeepSeek 与 OpenRouter，用户要配置 OpenAI、Anthropic Claude、Google Gemini、xAI Grok、Moonshot Kimi 或智谱 GLM 等常见模型时，缺乏即选即用的预设卡片和模型自动同步能力。
2. 健康检查 `check_provider()` 仅对 DeepSeek（`/user/balance`）和 OpenRouter（`/key`）做了分支判断，对其他标准 OpenAI 兼容提供商缺少通用探测逻辑，且 32KB 限制在拉取部分模型列表时存在被截断的风险。
3. CLI `--provider` 选项和 API 请求模型的字面量硬编码了 `deepseek` 和 `openrouter`，限制了扩展。

本次完成后应观察到：
1. `src/selfforge_agent/providers.py` 扩充预设至 9 家常见供应商：
   - `opencode`：OpenCode Zen（`https://opencode.ai/zen/v1`，默认 `claude-3-5-sonnet`，环境变量 `OPENCODE_API_KEY`）
   - `chatgpt`：ChatGPT (OpenAI)（`https://api.openai.com/v1`，默认 `gpt-4o-mini`，环境变量 `OPENAI_API_KEY` / `CHATGPT_API_KEY`）
   - `claude`：Claude (Anthropic)（`https://api.anthropic.com/v1`，默认 `claude-3-5-sonnet-20241022`，环境变量 `ANTHROPIC_API_KEY` / `CLAUDE_API_KEY`）
   - `gemini`：Gemini (Google)（`https://generativelanguage.googleapis.com/v1beta/openai`，默认 `gemini-1.5-flash`，环境变量 `GEMINI_API_KEY`）
   - `grok`：Grok (xAI)（`https://api.x.ai/v1`，默认 `grok-beta`，环境变量 `XAI_API_KEY` / `GROK_API_KEY`）
   - `kimi`：Kimi (Moonshot)（`https://api.moonshot.cn/v1`，默认 `moonshot-v1-8k`，环境变量 `MOONSHOT_API_KEY` / `KIMI_API_KEY`）
   - `glm`：GLM (智谱AI)（`https://open.bigmodel.cn/api/paas/v4`，默认 `glm-4-flash`，环境变量 `ZHIPUAI_API_KEY` / `GLM_API_KEY`）
2. 通用健康检查机制：
   - 对新增的通用 OpenAI 兼容供应商，默认探测 `${base_url}/models`，将响应上限提升至 256KB。
   - 当返回 HTTP 200 且格式有效时报告 `status="ready"`、`balance="API Key 有效"`，精确区分 `invalid_key`、`insufficient_credits` 与 `unavailable`。
3. 全链路 CLI 与 Web UI 贯通：
   - CLI 子命令（`code`、`eval`、`eval-suite`、`repair`、`generate-test`）统一动态读取 `_DEFAULTS` 选项。
   - Web 页面各任务模块下拉菜单完整接入 9 个供应商选项。
   - Web 设置中心增加全部 7 家新增供应商的独立保存卡片与 `<datalist>` 动态模型同步。
4. 严格保留工程不变量：
   - 凭证与安全：API Key 永远不回显给前端；`validate_base_url` 严格校验协议（必须为 https，仅本地放行 http）且拒绝 URL 用户名/密码及 query 污染。
   - 存储并发安全：更新 `.local/providers.json` 时严格遵守 `_settings_lock` 排他锁与原子临时文件覆盖（权限 `0o600`）。
   - 共享 Deadline：Fallback 级联降级各候选题依然严格共享单次调用总 deadline 预算。

预计触及：
- `src/selfforge_agent/providers.py`
- `src/selfforge_agent/api/app.py`
- `src/selfforge_agent/cli.py`
- `src/selfforge_agent/api/static/index.html`
- `src/selfforge_agent/api/static/app.js`
- `tests/test_providers.py`
- `tests/test_github_web.py`
- `docs/tasks/SF-PROV-01.md`
- `docs/backlog.md`

## 验收

| 编号 | Given / When / Then | 证明方式 | 结果 |
| --- | --- | --- | --- |
| A1 | 预设供应商列表与配置解析 | `_DEFAULTS` 包含 9 家配置，支持多别名环境变量解析 | 通过 |
| A2 | 通用供应商连通性检查 | 模拟 401 报错报告 `invalid_key`，模拟有效 200 响应报告 `ready` | 通过 |
| A3 | API 接口与路由多供应商支持 | `/api/config`、`/api/providers/config` 与 `/api/providers/{provider}/models` 正确映射全部 9 家 | 通过 |
| A4 | 动态 Fallback 路由 | `route_providers` 正确处理新增供应商首选与级联回退顺序 | 通过 |
| A5 | 跨模块回归测试 | `test_model_deadline`、`test_pricing`、`test_github_web` 等全部通过 | 通过 |

## 实施顺序

1. 扩充 `providers.py` 中的类型定义、`_DEFAULTS` 与 `check_provider()`。
2. 泛化 `api/app.py` 各 Request 校验及模型同步端点。
3. 动态化 `cli.py` 各子命令 `--provider` 选项绑定。
4. 扩充 Web 页面 `index.html` 与 `app.js` 设置卡片与下拉选项。
5. 扩展 `tests/test_providers.py` 与 `tests/test_github_web.py` 自动化测试用例。
6. 更新任务卡与 backlog，提交并推送创建 PR #55。

## 验证记录

| 命令/演示 | 环境/模式 | 测试树或 SHA | 实际结果 |
| --- | --- | --- | --- |
| `git diff --check` | Host | 本分支 | 全部通过（无空白/换行缺陷） |
| `PYTHONPATH=src pytest -q tests/test_providers.py` | Host | 本分支 | 7 passed in 1.20s |
| `PYTHONPATH=src pytest -q tests/test_model_deadline.py tests/test_github_web.py tests/test_pricing.py tests/test_providers.py` | Host | 本分支 | 29 passed in 1.26s |
| `PYTHONPATH=src pytest -q tests/test_risk_prioritization.py tests/test_reference_validation.py tests/test_session_metrics.py tests/test_dependency_images.py` | Host | 本分支 | 45 passed in 55.26s |

## Review

- 严格保持向后兼容：现有 DeepSeek 与 OpenRouter 配置、环境变量、费率卡完全不受影响。
- 安全防护一致性：所有新增预设受相同的 `validate_base_url`、本地 0600 文件锁以及 API Key 不回显策略保护。

## Handoff

- 交付状态：开发与本地验证已就绪，准备推送到 GitHub 远端并提交 PR #55。
- 分支：`feat/expand-provider-presets`
