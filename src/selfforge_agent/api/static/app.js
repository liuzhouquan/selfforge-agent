const $ = (id) => document.getElementById(id);
let current = null;
let hostTestsEnabled = false;
let verifiedPatch = "";
let differentialTest = "";
let configuredProviders = [];
let activeCodingJob = null;
let currentCodingSession = null;
let currentCodingRepo = null;
let currentCodingBranch = "";
let currentCodingSandbox = "docker";
let currentCodingSandboxRaw = null;
let codingJobRunning = false;
let codingPollTimer = null;
let codingDiffJobId = null;
let codingRegressionRunning = false;
let codingRegressionGeneration = 0;
let codingRegressionJobId = null;
let codingRegressionLoaded = false;
let codingGovernanceBoundRepo = "";
let codingWsGovGeneration = 0;
let codingWsGovRunning = false;
let codingFreshnessGeneration = 0;
let codingFreshnessRunning = false;

function providerLabel(name) {
  const labels = {
    deepseek: "DeepSeek",
    openrouter: "OpenRouter",
    opencode: "OpenCode",
    chatgpt: "ChatGPT (OpenAI)",
    claude: "Claude (Anthropic)",
    gemini: "Gemini (Google)",
    grok: "Grok (xAI)",
    kimi: "Kimi (Moonshot)",
    glm: "GLM (智谱AI)",
  };
  return labels[name] || name;
}

function providerStatusLabel(status) {
  return ({
    ready: "可用",
    invalid_key: "密钥无效",
    insufficient_credits: "额度不足",
    unavailable: "暂不可用",
    unconfigured: "未配置",
  })[status] || status;
}

function attemptText(attempt) {
  const usage = attempt.usage;
  const tokens = usage ? ` · ${usage.prompt_tokens} 输入 / ${usage.completion_tokens} 输出 / ${usage.total_tokens} 总 tokens` : "";
  return `${providerLabel(attempt.provider)} (${attempt.model})：${attempt.status === "success" ? "成功" : `失败 · ${attempt.error}`}${tokens}`;
}

function showAlert(message) {
  const element = $("alert");
  element.textContent = message;
  element.hidden = !message;
}

function setLoading(loading) {
  const button = $("review-button");
  button.disabled = loading;
  button.textContent = loading ? "正在拉取 PR…" : "导入并审查 →";
}

async function parseJsonResponse(response) {
  let data = null;
  try {
    data = await response.json();
  } catch (_err) {
    if (!response.ok) throw new Error(`请求失败 (${response.status})`);
    throw new Error("响应不是有效的 JSON。");
  }
  if (!response.ok) throw new Error((data && data.detail) || `请求失败 (${response.status})`);
  return data;
}

async function getJson(path) {
  return parseJsonResponse(await fetch(path));
}

async function postJson(path, payload) {
  return parseJsonResponse(await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }));
}

function showCodingError(message) {
  $("coding-error").textContent = message;
  $("coding-error").hidden = !message;
}

function refreshCodingAvailability() {
  const host = $("coding-sandbox").value === "host";
  const direct = $("coding-workspace").value === "direct";
  $("coding-host-trust").hidden = !host;
  $("coding-image-label").hidden = host;
  $("coding-direct-trust").hidden = !direct;
  $("coding-dirty-trust").hidden = !direct;
  $("coding-start").disabled = codingJobRunning || !configuredProviders.length ||
    (host && (!hostTestsEnabled || !$("coding-trust").checked)) ||
    (direct && !$("coding-confirm-direct").checked);
  $("coding-availability").textContent = !configuredProviders.length
    ? "需先配置模型密钥"
    : direct && !$("coding-confirm-direct").checked ? "直接修改原仓库需明确确认"
    : host ? (hostTestsEnabled ? "本机执行需明确勾选信任" : "本机执行需设置 SELFFORGE_ENABLE_HOST_TESTS=1")
      : "请使用已准备好项目依赖的本机镜像；运行测试时保持禁网";
}

function resetCodingRegressionState(statusMessage = "") {
  codingRegressionGeneration += 1;
  codingRegressionRunning = false;
  codingRegressionLoaded = true; // Mutations invalidate history until another job is selected.
  $("coding-regression-result").hidden = true;
  $("coding-regression-result").replaceChildren();
  if (statusMessage) {
    $("coding-regression-status").textContent = statusMessage;
    $("coding-regression-status").hidden = false;
  } else {
    $("coding-regression-status").textContent = "";
    $("coding-regression-status").hidden = true;
  }
}

async function loadDurableRegressionReport(job) {
  if (!job || job.sandbox !== "docker" || !job.branch || !job.session_id || !job.repository) return;
  const requestJobId = job.job_id;
  const requestSession = job.session_id;
  const requestRepo = job.repository;
  const requestGeneration = ++codingRegressionGeneration;
  const isCurrent = () => requestGeneration === codingRegressionGeneration &&
    requestJobId === activeCodingJob && requestSession === currentCodingSession &&
    requestRepo === currentCodingRepo;

  try {
    const res = await fetch(
      `/api/code/workspaces/${encodeURIComponent(requestSession)}/regression?repository=${encodeURIComponent(requestRepo)}`
    );
    if (!isCurrent()) return;
    if (res.status === 200) {
      const report = await res.json();
      if (!isCurrent()) return;
      renderCodingRegressionReport(report, true);
      const statusEl = $("coding-regression-status");
      statusEl.textContent = `已恢复持久化回归验证记录：${regressionStatusLabel(report.status)}（历史 Docker 快照，未经当前工作区重新验证）。`;
      statusEl.hidden = false;
    }
  } catch (_err) {
    // Silently ignore network or aborted errors
  }
}

function refreshCodingRegressionAvailability() {
  const panel = $("coding-regression");
  const btn = $("coding-regression-btn");
  const note = $("coding-regression-availability");
  const hasWorktree = Boolean(currentCodingBranch && currentCodingSession && currentCodingRepo);
  panel.hidden = !hasWorktree;
  if (!hasWorktree) {
    btn.disabled = true;
    btn.textContent = "运行独立回归验证";
    note.textContent = "";
    return;
  }
  btn.textContent = codingRegressionRunning ? "正在独立验证…" : "运行独立回归验证";
  if (codingRegressionRunning) {
    btn.disabled = true;
    note.textContent = "正在隔离对比 Base 与当前工作区…";
  } else if (codingJobRunning) {
    btn.disabled = true;
    note.textContent = "任务运行中，完成后可执行独立回归验证";
  } else if (currentCodingSandboxRaw === "host") {
    btn.disabled = true;
    note.textContent = "仅 Docker 隔离工作区支持独立回归验证（本机执行不支持）";
  } else if (currentCodingSandboxRaw !== "docker") {
    btn.disabled = true;
    note.textContent = "会话未记录 Docker 隔离环境，无法执行独立回归验证";
  } else {
    btn.disabled = false;
    note.textContent = "仅对比 Base 与当前工作区结果，不改动 Coding 测试验证状态";
  }
}

function regressionStatusLabel(status) {
  return ({
    confirmed: "已确认（Base 失败 / 当前工作区通过）",
    inconclusive: "未确认（未满足复现与修复条件）",
    error: "执行错误",
  })[status] || status;
}

function regressionReasonLabel(reason) {
  return ({
    base_failed_repaired_passed: "Base 失败且当前工作区通过（成功复现修复）",
    both_passed: "Base 与当前工作区均通过（未能证明故障复现）",
    base_did_not_fail: "Base 未能按预期失败",
    repaired_failed: "当前工作区测试未通过",
    repaired_had_skipped_tests: "当前工作区存在跳过测试（不得确认通过）",
    workspace_changed: "验证期间工作区发生变化",
    empty_or_all_skipped: "未收集到有效测试或全部被跳过",
    collection_or_runtime_error: "测试收集或执行异常",
    timeout: "回归验证超时",
    snapshot_error: "隔离快照准备失败",
  })[reason] || reason;
}

function renderRegressionRunCard(title, summary, historical = false) {
  const card = element("div", "coding-regression-card");
  const heading = element("strong", "", title);
  card.append(heading);
  if (!summary) {
    card.append(element("p", "", historical ? "历史运行明细不可恢复" : "未运行"));
    return card;
  }
  const exitText = summary.exit_code === null || summary.exit_code === undefined ? "-" : summary.exit_code;
  card.append(
    element("p", "", `${summary.status} · 退出码 ${exitText} · 耗时 ${summary.duration_seconds}s`),
    element("p", "", `通过 ${summary.passed} / 失败 ${summary.failed} / 错误 ${summary.errors} / 跳过 ${summary.skipped}`),
  );
  return card;
}

function renderCodingRegressionReport(report, historical = false) {
  const container = $("coding-regression-result");
  const summaryLine = element("div", "coding-regression-summary");
  const statusLabel = regressionStatusLabel(report.status);
  const reasonLabel = regressionReasonLabel(report.reason);
  summaryLine.append(
    element("span", "coding-regression-badge", `${historical ? "历史记录：" + statusLabel.replaceAll("当前工作区", "历史工作区") : statusLabel} (${report.status})`),
    element("span", "", `${historical ? reasonLabel.replaceAll("当前工作区", "历史工作区") : reasonLabel} (${report.reason})`),
  );
  const grid = element("div", "coding-regression-grid");
  grid.append(
    renderRegressionRunCard("Base 基线快照", report.base_result, historical),
    renderRegressionRunCard(historical ? "历史工作区快照（需重新验证）" : "当前工作区快照", report.repaired_result, historical),
  );
  const hashes = element(
    "p",
    "coding-regression-hashes",
    `会话 ${report.session_id} · Base ${report.base_sha.slice(0, 12)} · 工作区 SHA ${report.workspace_sha256.slice(0, 12)} · 测试 SHA ${report.test_sha256.slice(0, 12)}`,
  );
  hashes.title = `base=${report.base_sha} | workspace=${report.workspace_sha256} | test=${report.test_sha256}`;
  container.replaceChildren(summaryLine, grid, hashes);
  container.hidden = false;
}

function renderCodingJob(job) {
  activeCodingJob = job.job_id;
  currentCodingSession = job.session_id;
  currentCodingRepo = job.repository;
  currentCodingBranch = job.branch;
  currentCodingSandbox = job.sandbox || "docker";
  currentCodingSandboxRaw = job.sandbox || null;
  codingJobRunning = ["queued", "running"].includes(job.state);
  const context = JSON.stringify([job.job_id, job.session_id, job.repository, job.branch, job.sandbox]);
  const jobChanged = codingRegressionJobId !== context;
  if (jobChanged) {
    codingRegressionJobId = context;
    resetCodingRegressionState();
    codingRegressionLoaded = false;
  }
  if (job.sandbox === "docker" && job.branch && !codingJobRunning &&
      !codingRegressionRunning && !codingRegressionLoaded) {
    codingRegressionLoaded = true;
    loadDurableRegressionReport(job);
  }
  const labels = {
    queued: "等待运行", running: "正在执行", completed: "已验证完成",
    unverified: "完成但未通过测试验证", failed: "执行失败",
    max_steps: "达到最大步数", timed_out: "超出时间预算", cancelled: "已取消",
    interrupted: "服务中断，可续跑",
  };
  $("coding-job").hidden = false;
  $("coding-state").textContent = labels[job.state] || job.state;
  $("coding-job-meta").textContent = `会话 ${job.session_id} · ${job.steps} 步 · ${job.tools_used} 次工具调用${job.cancel_requested ? " · 已请求取消" : ""}`;
  if (job.sandbox) {
    $("coding-job-meta").textContent += job.image_id ? ` · Docker ${job.image_id.slice(7, 19)}` : " · 本机执行";
    $("coding-job-meta").title = job.image_id || "";
  }
  $("coding-workspace-info").textContent = job.branch
    ? `独立工作区：${job.workspace_path} · 分支 ${job.branch} · 基于 ${job.base_sha.slice(0, 12)}`
    : `直接修改：${job.workspace_path}`;
  $("coding-answer").textContent = job.error || job.answer || "Agent 正在处理任务…";
  const tokenMetrics = job.token_metrics;
  if (tokenMetrics && tokenMetrics.total_tokens > 0) {
    const p = tokenMetrics.prompt_tokens || 0;
    const c = tokenMetrics.completion_tokens || 0;
    $("metric-tokens").textContent = `${tokenMetrics.total_tokens.toLocaleString()} (P: ${p.toLocaleString()} / C: ${c.toLocaleString()})`;
    if (tokenMetrics.cost) {
      $("metric-cost").textContent = `$${tokenMetrics.cost.total_cost_usd.toFixed(4)}`;
      $("metric-cost-box").hidden = false;
    } else {
      $("metric-cost-box").hidden = true;
    }
    $("metric-verification").textContent = job.verification_passed ? "已通过 JUnit 验证" : (job.state === "completed" ? "已完成" : "未通过/待验证");
    $("coding-metrics-bar").hidden = false;
  } else {
    const modelCalls = (job.trace || []).filter((e) => e.event === "model_call" && e.usage);
    if (modelCalls.length) {
      const promptTokens = modelCalls.reduce((s, e) => s + (e.usage.prompt_tokens || 0), 0);
      const completionTokens = modelCalls.reduce((s, e) => s + (e.usage.completion_tokens || 0), 0);
      $("metric-tokens").textContent = `${(promptTokens + completionTokens).toLocaleString()} (P: ${promptTokens.toLocaleString()} / C: ${completionTokens.toLocaleString()})`;
      $("metric-cost-box").hidden = true;
      $("metric-verification").textContent = job.verification_passed ? "已通过 JUnit 验证" : (job.state === "completed" ? "已完成" : "未通过/待验证");
      $("coding-metrics-bar").hidden = false;
    } else {
      $("coding-metrics-bar").hidden = true;
    }
  }
  $("coding-cancel").hidden = !codingJobRunning;
  $("coding-cancel").disabled = job.cancel_requested;
  $("coding-resume-current").hidden = codingJobRunning || !job.can_resume;
  $("coding-revert-workspace").hidden = codingJobRunning || !job.branch;
  $("coding-remove-workspace").hidden = codingJobRunning || !job.branch;
  $("coding-diff").hidden = !job.branch;
  $("coding-delivery").hidden = !job.branch || codingJobRunning;
  if (job.branch && !codingJobRunning && codingDiffJobId !== job.job_id) {
    codingDiffJobId = job.job_id;
    loadCodingDiff();
    if (!$("coding-pr-title").value && job.answer) {
      $("coding-pr-title").value = job.answer.slice(0, 100);
    }
  }
  $("coding-trace").textContent = job.trace.map((event) => {
    const detail = event.name || event.status || event.provider || event.error_type || "";
    const outcome = event.ok === undefined ? "" : event.ok ? " · 成功" : " · 失败";
    return `${(event.at || "").slice(11, 19)} · ${event.event}${detail ? ` · ${detail}` : ""}${outcome}`;
  }).join("\n") || "等待执行轨迹…";
  localStorage.setItem("selfforge-coding-job", job.job_id);
  localStorage.setItem("selfforge-coding-repo", job.repository);
  if (codingJobRunning) sessionStorage.setItem("selfforge-coding-job", job.job_id);
  else sessionStorage.removeItem("selfforge-coding-job");
  refreshCodingAvailability();
  refreshCodingRegressionAvailability();
}

async function pollCodingJob(jobId) {
  clearTimeout(codingPollTimer);
  try {
    const response = await fetch(`/api/code/jobs/${encodeURIComponent(jobId)}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.detail || `读取任务失败 (${response.status})`);
    renderCodingJob(job);
    if (codingJobRunning && activeCodingJob === jobId) {
      codingPollTimer = setTimeout(() => pollCodingJob(jobId), 1200);
    }
  } catch (error) {
    showCodingError(error.message);
    sessionStorage.removeItem("selfforge-coding-job");
    localStorage.removeItem("selfforge-coding-job");
    codingJobRunning = false;
    refreshCodingAvailability();
  }
}

async function loadCodingHistory() {
  const repository = $("coding-repo").value.trim();
  if (!repository) {
    showCodingError("请先输入 Git 仓库路径，再读取历史任务。");
    return;
  }
  showCodingError("");
  try {
    const response = await fetch(`/api/code/jobs?repository=${encodeURIComponent(repository)}`);
    const jobs = await response.json();
    if (!response.ok) throw new Error(jobs.detail || `读取历史失败 (${response.status})`);
    const select = $("coding-recent");
    select.replaceChildren();
    const prompt = document.createElement("option");
    prompt.value = "";
    prompt.textContent = jobs.length ? `选择历史任务（${jobs.length} 条）` : "这个仓库还没有历史任务";
    select.append(prompt);
    for (const job of jobs) {
      const option = document.createElement("option");
      option.value = job.job_id;
      option.textContent = `${job.state} · 会话 ${job.session_id} · ${job.branch || "直接修改"}`;
      select.append(option);
    }
  } catch (error) {
    showCodingError(error.message);
  }
}

$("coding-history-refresh").addEventListener("click", loadCodingHistory);
$("coding-recent").addEventListener("change", (event) => {
  if (event.target.value) pollCodingJob(event.target.value);
});

$("coding-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showCodingError("");
  const task = $("coding-task-text").value.trim();
  const resume = $("coding-resume").value.trim();
  if (!task && !resume) {
    showCodingError("请输入开发任务，或填写要续跑的会话 ID。");
    return;
  }
  const host = $("coding-sandbox").value === "host";
  codingJobRunning = true;
  resetCodingRegressionState();
  refreshCodingRegressionAvailability();
  refreshCodingAvailability();
  try {
    const job = await postJson("/api/code/jobs", {
      repository: $("coding-repo").value.trim(), task, resume_session: resume || null,
      workspace: $("coding-workspace").value,
      confirm_direct: $("coding-workspace").value === "direct" && $("coding-confirm-direct").checked,
      allow_dirty: $("coding-workspace").value === "direct" && $("coding-allow-dirty").checked,
      sandbox: $("coding-sandbox").value, trust_local: host && $("coding-trust").checked,
      image: host ? null : $("coding-image").value.trim() || null,
      provider: $("coding-provider").value, fallback: $("coding-fallback").checked,
      max_steps: Number($("coding-steps").value), time_budget: Number($("coding-time").value),
    });
    renderCodingJob(job);
    $("coding-confirm-direct").checked = false;
    $("coding-allow-dirty").checked = false;
    $("coding-trust").checked = false;
    refreshCodingAvailability();
    if (codingJobRunning) codingPollTimer = setTimeout(() => pollCodingJob(job.job_id), 500);
  } catch (error) {
    showCodingError(error.message);
    codingJobRunning = false;
    refreshCodingAvailability();
    refreshCodingRegressionAvailability();
  }
});

async function loadCodingDiff() {
  if (!currentCodingSession || !currentCodingRepo || !currentCodingBranch) return;
  $("coding-diff-state").textContent = "正在读取改动…";
  try {
    const params = new URLSearchParams({ repository: currentCodingRepo });
    const response = await fetch(`/api/code/workspaces/${encodeURIComponent(currentCodingSession)}/diff?${params}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `读取 Diff 失败 (${response.status})`);
    $("coding-diff-state").textContent = `${data.files.length} 个文件${data.omitted_files ? ` · ${data.omitted_files} 个文件因数量或保护规则未展示` : ""} · 基于 ${data.base_sha.slice(0, 12)}`;
    const list = $("coding-diff-files");
    list.replaceChildren();
    $("coding-diff-patch").hidden = true;
    if (!data.files.length) {
      $("coding-diff-patch").textContent = "当前没有可展示的改动。";
      $("coding-diff-patch").hidden = false;
      return;
    }
    data.files.forEach((file, index) => {
      const button = element("button", "coding-diff-file", `${file.status === "untracked" ? "新文件" : "变更"} · ${file.path}`);
      button.type = "button";
      button.addEventListener("click", () => {
        list.querySelectorAll("button").forEach((item) => item.classList.remove("active"));
        button.classList.add("active");
        $("coding-diff-patch").textContent = file.patch || "此文件没有可展示的文本补丁。";
        $("coding-diff-patch").hidden = false;
      });
      list.append(button);
      if (index === 0) button.click();
    });
  } catch (error) {
    $("coding-diff-state").textContent = error.message;
  }
}

$("coding-diff-refresh").addEventListener("click", loadCodingDiff);

$("coding-cancel").addEventListener("click", async () => {
  if (!activeCodingJob || !codingJobRunning) return;
  try {
    renderCodingJob(await postJson(`/api/code/jobs/${encodeURIComponent(activeCodingJob)}/cancel`, {}));
  } catch (error) {
    showCodingError(error.message);
  }
});

$("coding-resume-current").addEventListener("click", () => {
  if (!currentCodingSession) return;
  $("coding-resume").value = currentCodingSession;
  $("coding-repo").value = currentCodingRepo;
  syncCodingGovernanceRepo();
  $("coding-workspace").value = currentCodingBranch ? "worktree" : "direct";
  $("coding-sandbox").value = currentCodingSandbox;
  $("coding-image").value = "";
  $("coding-trust").checked = false;
  refreshCodingAvailability();
  $("coding-task-text").focus();
});

$("coding-revert-workspace").addEventListener("click", async () => {
  if (!currentCodingSession || !currentCodingRepo || !currentCodingBranch) return;
  if (!confirm("确定要还原工作区的所有未提交改动吗？此操作将丢失当前未提交的代码和新建的文件。")) return;
  showCodingError("");
  const btn = $("coding-revert-workspace");
  btn.disabled = true;
  btn.textContent = "正在还原…";
  resetCodingRegressionState();
  refreshCodingRegressionAvailability();
  const mutationJobId = activeCodingJob;
  const mutationSession = currentCodingSession;
  try {
    const res = await postJson(`/api/code/workspaces/${encodeURIComponent(currentCodingSession)}/revert`, {
      repository: currentCodingRepo,
    });
    if (mutationJobId !== activeCodingJob || mutationSession !== currentCodingSession) return;
    resetCodingRegressionState("工作区已还原，之前的回归验证结果已失效；如有需要请重新运行。");
    refreshCodingRegressionAvailability();
    loadCodingDiff();
    alert(`工作区已还原，共处理 ${res.reverted_files.length} 个文件。`);
  } catch (error) {
    showCodingError(`还原工作区失败: ${error.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "还原工作区改动";
  }
});

$("coding-remove-workspace").addEventListener("click", async () => {
  if (!currentCodingSession || !currentCodingRepo || !currentCodingBranch) return;
  showCodingError("");
  resetCodingRegressionState();
  refreshCodingRegressionAvailability();
  const mutationJobId = activeCodingJob;
  const mutationSession = currentCodingSession;
  try {
    await postJson(`/api/code/workspaces/${encodeURIComponent(currentCodingSession)}/remove`, {
      repository: currentCodingRepo,
    });
    if (mutationJobId !== activeCodingJob || mutationSession !== currentCodingSession) return;
    $("coding-workspace-info").textContent += " · 工作区已清理，分支仍保留";
    $("coding-remove-workspace").hidden = true;
    $("coding-resume-current").hidden = true;
    $("coding-delivery").hidden = true;
    currentCodingBranch = "";
    resetCodingRegressionState();
    refreshCodingRegressionAvailability();
  } catch (error) {
    showCodingError(`${error.message}。请先在工作区提交或还原改动，再清理。`);
  }
});

$("coding-regression-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentCodingSession || !currentCodingRepo || !currentCodingBranch) return;
  if (codingJobRunning || codingRegressionRunning) return;
  const statusEl = $("coding-regression-status");
  if (currentCodingSandboxRaw !== "docker") {
    statusEl.textContent = "仅 Docker 隔离工作区支持独立回归验证。";
    statusEl.hidden = false;
    return;
  }
  const testFile = $("coding-regression-test-file").value.trim();
  if (!testFile) {
    statusEl.textContent = "请输入候选测试文件路径（相对仓库根目录）。";
    statusEl.hidden = false;
    return;
  }
  const rawTimeout = $("coding-regression-timeout").value.trim();
  const timeout = Number(rawTimeout);
  if (!rawTimeout || !Number.isFinite(timeout) || timeout < 1 || timeout > 180) {
    statusEl.textContent = "超时预算必须在 1 到 180 秒之间。";
    statusEl.hidden = false;
    return;
  }
  const requestJobId = activeCodingJob;
  const requestSession = currentCodingSession;
  const requestGeneration = ++codingRegressionGeneration;
  const isCurrentRequest = () => requestGeneration === codingRegressionGeneration &&
    requestJobId === activeCodingJob && requestSession === currentCodingSession;
  codingRegressionRunning = true;
  codingRegressionLoaded = true;
  $("coding-regression-result").hidden = true;
  statusEl.textContent = "正在隔离运行 Base 与当前工作区对照验证…";
  statusEl.hidden = false;
  refreshCodingRegressionAvailability();
  try {
    const report = await postJson(
      `/api/code/workspaces/${encodeURIComponent(requestSession)}/validate-regression`,
      { repository: currentCodingRepo, test_file: testFile, timeout },
    );
    if (!isCurrentRequest()) return;
    renderCodingRegressionReport(report);
    statusEl.textContent = `独立回归验证完成：${regressionStatusLabel(report.status)}（不改变 Coding 测试验证标记）。`;
    statusEl.hidden = false;
  } catch (error) {
    if (!isCurrentRequest()) return;
    $("coding-regression-result").hidden = true;
    statusEl.textContent = `回归验证失败：${error.message}`;
    statusEl.hidden = false;
  } finally {
    if (isCurrentRequest()) {
      codingRegressionRunning = false;
      refreshCodingRegressionAvailability();
    }
  }
});

$("coding-commit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentCodingSession || !currentCodingRepo) return;
  const message = $("coding-commit-msg").value.trim();
  if (!message) {
    $("coding-commit-status").textContent = "请输入提交说明。";
    $("coding-commit-status").hidden = false;
    return;
  }
  const btn = $("coding-commit-btn");
  btn.disabled = true;
  btn.textContent = "正在提交…";
  $("coding-commit-status").textContent = "";
  $("coding-commit-status").hidden = true;
  try {
    const res = await postJson(`/api/code/workspaces/${encodeURIComponent(currentCodingSession)}/commit`, {
      repository: currentCodingRepo,
      message,
    });
    $("coding-commit-status").textContent = `提交成功：${res.commit_sha.slice(0, 10)}（${res.files_committed.length} 个文件）`;
    $("coding-commit-status").hidden = false;
    $("coding-commit-msg").value = "";
    resetCodingRegressionState();
    refreshCodingRegressionAvailability();
    loadCodingDiff();
  } catch (err) {
    $("coding-commit-status").textContent = `提交失败：${err.message}`;
    $("coding-commit-status").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "提交改动";
  }
});

$("coding-pr-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!currentCodingSession || !currentCodingRepo) return;
  const title = $("coding-pr-title").value.trim();
  if (!title) {
    $("coding-pr-status").textContent = "请输入 PR 标题。";
    $("coding-pr-status").hidden = false;
    return;
  }
  const btn = $("coding-pr-btn");
  btn.disabled = true;
  btn.textContent = "正在推送并创建 PR…";
  $("coding-pr-status").textContent = "";
  $("coding-pr-status").hidden = true;
  try {
    const res = await postJson(`/api/code/workspaces/${encodeURIComponent(currentCodingSession)}/pr`, {
      repository: currentCodingRepo,
      title,
      body: $("coding-pr-body").value.trim(),
      base_branch: $("coding-pr-base").value.trim() || null,
      remote: $("coding-pr-remote").value.trim() || "origin",
    });
    const link = document.createElement("a");
    link.href = res.pr_url;
    link.target = "_blank";
    link.rel = "noopener";
    link.textContent = `PR #${res.pr_number || ""}：${res.pr_url}`;
    $("coding-pr-status").replaceChildren(document.createTextNode("Pull Request 创建成功："), link);
    $("coding-pr-status").hidden = false;
  } catch (err) {
    $("coding-pr-status").textContent = `创建 PR 失败：${err.message}`;
    $("coding-pr-status").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "推送并创建 Pull Request";
  }
});

function formatBytes(bytes) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes) || bytes < 0) return "未知";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB (${bytes} B)`;
  return `${(bytes / 1048576).toFixed(2)} MB (${bytes} B)`;
}

function parseOptionalQuotaBytes(raw) {
  const trimmed = String(raw || "").trim();
  if (!trimmed) return { ok: true, value: null };
  if (!/^\d+$/.test(trimmed)) {
    return { ok: false, error: "配额字节数必须是非负整数（例如 0 或 104857600）。" };
  }
  const parsed = Number(trimmed);
  if (!Number.isSafeInteger(parsed)) {
    return { ok: false, error: "配额字节数超出安全整数范围。" };
  }
  return { ok: true, value: parsed };
}

function refreshCodingGovernanceAvailability() {
  const repo = $("coding-repo").value.trim();
  const hasRepo = Boolean(repo);
  $("coding-ws-audit-btn").disabled = !hasRepo || codingWsGovRunning;
  $("coding-ws-storage-btn").disabled = !hasRepo || codingWsGovRunning;
  $("coding-ws-prune-dry-btn").disabled = !hasRepo || codingWsGovRunning;
  $("coding-ws-prune-exec-btn").disabled = !hasRepo || codingWsGovRunning || !$("coding-ws-prune-confirm").checked;
  $("coding-freshness-btn").disabled = !hasRepo || codingFreshnessRunning;
  $("coding-governance-repo-hint").textContent = hasRepo
    ? `当前仓库：${repo}`
    : "输入仓库路径后可执行 worktree 审计、配额检查、安全清理与依赖新鲜度诊断";
}

function resetCodingGovernanceState() {
  codingWsGovGeneration += 1;
  codingWsGovRunning = false;
  codingFreshnessGeneration += 1;
  codingFreshnessRunning = false;
  $("coding-ws-prune-confirm").checked = false;
  $("coding-ws-status").textContent = "";
  $("coding-ws-status").hidden = true;
  $("coding-ws-result").replaceChildren();
  $("coding-ws-result").hidden = true;
  $("coding-ws-prune-result").replaceChildren();
  $("coding-ws-prune-result").hidden = true;
  $("coding-freshness-status").textContent = "";
  $("coding-freshness-status").hidden = true;
  $("coding-freshness-result").replaceChildren();
  $("coding-freshness-result").hidden = true;
}

function syncCodingGovernanceRepo() {
  const repo = $("coding-repo").value.trim();
  if (repo !== codingGovernanceBoundRepo) {
    codingGovernanceBoundRepo = repo;
    resetCodingGovernanceState();
  }
  refreshCodingGovernanceAvailability();
}

function workspaceFlagLabels(item) {
  return [
    item.is_locked ? "locked（锁定）" : "unlocked",
    item.is_dirty ? "dirty（含未提交改动）" : "clean",
    item.is_merged ? "merged（已合并）" : "unmerged（未合并）",
    item.prunable ? "prunable（可清理）" : "retained（保留）",
  ].join(" · ");
}

function renderWorkspaceAuditItems(items) {
  const container = $("coding-ws-result");
  if (!items.length) {
    container.replaceChildren(element("p", "", "该仓库当前没有托管中的 Coding worktree。"));
    container.hidden = false;
    return;
  }
  const summary = element("div", "coding-regression-summary");
  summary.append(
    element("span", "coding-regression-badge", `审计 ${items.length} 个 worktree`),
    element("span", "", `可清理 ${items.filter((i) => i.prunable).length} · 锁定 ${items.filter((i) => i.is_locked).length} · 脏区 ${items.filter((i) => i.is_dirty).length}`),
  );
  const list = element("div", "coding-governance-list");
  for (const item of items) {
    const card = element("div", "coding-regression-card");
    card.append(
      element("strong", "", `会话 ${item.session_id} · ${item.branch}`),
      element("p", "", `状态：${workspaceFlagLabels(item)}`),
      element("p", "coding-regression-hashes", `基线 ${(item.base_sha || "").slice(0, 12)} · 路径 ${item.path}`),
    );
    list.append(card);
  }
  container.replaceChildren(summary, list);
  container.hidden = false;
}

function renderWorkspaceStorageReport(report) {
  const container = $("coding-ws-result");
  const quotaLabel = report.quota_bytes === null || report.quota_bytes === undefined
    ? "未设配额"
    : report.quota_exceeded === true
      ? "已超出配额"
      : report.quota_exceeded === null
        ? "配额状态未知（扫描未完整）"
        : "未超出配额";
  const badgeText = report.complete
    ? `${report.workspace_count} 个工作区 · ${quotaLabel}`
    : `${report.workspace_count} 个工作区 · 扫描未完整（${quotaLabel}）`;
  const summary = element("div", "coding-regression-summary");
  summary.append(
    element("span", "coding-regression-badge", badgeText),
    element("span", "", `总占用 ${formatBytes(report.total_bytes)} · 缓存 ${formatBytes(report.total_cache_bytes)} · 可回收 ${formatBytes(report.reclaimable_bytes)}`),
  );
  const children = [summary];
  if (!report.complete) {
    children.push(element("p", "coding-regression-hashes", "告警：目录扫描未完整（Incomplete scan），当前字节数为下界估计，配额判定按未知处理。"));
  }
  if (report.measurement_scope) {
    children.push(element("p", "coding-regression-hashes", `统计口径：${report.measurement_scope}`));
  }
  for (const rec of report.recommendations || []) {
    children.push(element("p", "", `建议：${rec}`));
  }
  const list = element("div", "coding-governance-list");
  for (const item of report.workspaces || []) {
    const card = element("div", "coding-regression-card");
    card.append(
      element("strong", "", `会话 ${item.session_id} · ${item.branch}`),
      element("p", "", `大小 ${formatBytes(item.total_bytes)}（缓存 ${formatBytes(item.cache_bytes)} · ${item.file_count} 文件）· 可回收 ${formatBytes(item.reclaimable_bytes)}`),
      element("p", "", `状态：${workspaceFlagLabels(item)}${item.complete === false ? " · 扫描未完整" : ""}`),
    );
    if (item.scan_error) {
      card.append(element("p", "coding-regression-hashes", `扫描异常：${item.scan_error}`));
    }
    list.append(card);
  }
  children.push(list);
  container.replaceChildren(...children);
  container.hidden = false;
}

function renderWorkspacePruneResult(result, dryRun) {
  const container = $("coding-ws-prune-result");
  const modeLabel = dryRun ? "预演清理结果 (Dry Run)" : "清理执行结果";
  const formatGroup = (title, items) => `${title} (${(items || []).length}): ${(items && items.length) ? items.join(", ") : "无"}`;
  const card = element("div", "coding-regression-card");
  card.append(
    element("strong", "", modeLabel),
    element("p", "", formatGroup(dryRun ? "预计清理" : "已清理", result.pruned)),
    element("p", "", formatGroup("已跳过（锁定）", result.skipped_locked)),
    element("p", "", formatGroup("已跳过（脏区）", result.skipped_dirty)),
    element("p", "", formatGroup("已跳过（未合并）", result.skipped_unmerged)),
  );
  container.replaceChildren(card);
  container.hidden = false;
}

function freshnessStatusLabel(status) {
  const labels = {
    fresh: "一致 (fresh)",
    no_declarations: "无依赖声明 (no_declarations)",
    missing_dependencies: "存在未锁定依赖 (missing_dependencies)",
    incompatible_versions: "锁定版本与声明不兼容 (incompatible_versions)",
    stale_mtime: "声明文件较 lockfile 更新 (stale_mtime)",
  };
  return labels[status] || status;
}

function renderFreshnessReport(report) {
  const container = $("coding-freshness-result");
  const summary = element("div", "coding-regression-summary");
  summary.append(
    element("span", "coding-regression-badge", freshnessStatusLabel(report.status)),
    element("span", "", `${report.fresh ? "lockfile 与声明一致" : "检测到不一致或过时"} · 声明 ${report.declared_count} 项 / 锁定 ${report.locked_count} 项`),
  );
  const children = [
    summary,
    element("p", "", "说明：此结果仅作只读环境诊断，不代表目标容器已可安装，不会触发自动安装。"),
    element("p", "coding-regression-hashes", `Lockfile: ${report.lock_file || "无"} · 声明文件: ${report.declaration_file || "无"}${report.is_stale_by_mtime ? " · mtime 过期" : ""}`),
  ];
  if ((report.missing_packages || []).length) {
    const card = element("div", "coding-regression-card");
    card.append(element("strong", "", `缺失锁定包 (${report.missing_packages.length})`));
    for (const pkg of report.missing_packages) {
      card.append(element("p", "", `${pkg.name}${pkg.specifier || ""}（来源 ${pkg.source_file}）`));
    }
    children.push(card);
  }
  if ((report.incompatible_packages || []).length) {
    const card = element("div", "coding-regression-card");
    card.append(element("strong", "", `版本冲突包 (${report.incompatible_packages.length})`));
    for (const pkg of report.incompatible_packages) {
      card.append(element("p", "", `${pkg.name}：锁定 ${pkg.locked_version} 不满足声明 ${pkg.declared_specifier}（来源 ${pkg.source_file}）`));
    }
    children.push(card);
  }
  for (const warning of report.warnings || []) {
    children.push(element("p", "coding-regression-hashes", `提示：${warning}`));
  }
  container.replaceChildren(...children);
  container.hidden = false;
}

async function runWorkspaceGovernanceAction(mode) {
  const repo = $("coding-repo").value.trim();
  const statusEl = $("coding-ws-status");
  if (!repo) {
    statusEl.textContent = "请先输入 Git 仓库绝对路径。";
    statusEl.hidden = false;
    return;
  }
  if (codingWsGovRunning) return;
  const quotaCheck = mode === "storage"
    ? parseOptionalQuotaBytes($("coding-ws-quota").value)
    : { ok: true, value: null };
  if (!quotaCheck.ok) {
    $("coding-ws-result").hidden = true;
    statusEl.textContent = quotaCheck.error;
    statusEl.hidden = false;
    return;
  }
  const dryRun = mode === "prune-dry";
  const executePrune = mode === "prune-exec";
  if (executePrune && !$("coding-ws-prune-confirm").checked) {
    statusEl.textContent = "请先勾选清理确认复选框。";
    statusEl.hidden = false;
    return;
  }
  const requestGeneration = ++codingWsGovGeneration;
  const isCurrent = () => requestGeneration === codingWsGovGeneration &&
    $("coding-repo").value.trim() === repo;
  const headRef = $("coding-ws-head-ref").value.trim() || "HEAD";
  const mergedOnly = $("coding-ws-prune-merged").checked;
  codingWsGovRunning = true;
  refreshCodingGovernanceAvailability();
  statusEl.textContent = executePrune
    ? "正在按安全锁执行工作区清理…"
    : dryRun
      ? "正在预演工作区清理…"
      : mode === "storage"
        ? "正在扫描工作区磁盘占用与配额…"
        : "正在审计工作区 Git 状态…";
  statusEl.hidden = false;
  try {
    if (mode === "audit") {
      const params = new URLSearchParams({ repository: repo, head_ref: headRef });
      const items = await getJson(`/api/code/workspaces/audit?${params}`);
      if (!isCurrent()) return;
      renderWorkspaceAuditItems(items);
      statusEl.textContent = `已完成 ${items.length} 个工作区的快速审计。`;
    } else if (mode === "storage") {
      const params = new URLSearchParams({ repository: repo, head_ref: headRef });
      if (quotaCheck.value !== null) params.set("quota_bytes", String(quotaCheck.value));
      const report = await getJson(`/api/code/workspaces/storage?${params}`);
      if (!isCurrent()) return;
      renderWorkspaceStorageReport(report);
      statusEl.textContent = report.complete ? "存储与配额扫描完成。" : "存储扫描已完成（部分目录未完整扫描）。";
    } else {
      const pruneReport = await postJson("/api/code/workspaces/prune", {
        repository: repo,
        dry_run: dryRun,
        merged_only: mergedOnly,
        head_ref: headRef,
      });
      if (!isCurrent()) return;
      renderWorkspacePruneResult(pruneReport, dryRun);
      if (executePrune) {
        $("coding-ws-prune-confirm").checked = false;
        if (currentCodingRepo === repo && currentCodingSession && Array.isArray(pruneReport.pruned) &&
            pruneReport.pruned.includes(currentCodingSession)) {
          currentCodingBranch = "";
          $("coding-workspace-info").textContent += " · 工作区已被清理";
          $("coding-remove-workspace").hidden = true;
          $("coding-resume-current").hidden = true;
          $("coding-delivery").hidden = true;
          resetCodingRegressionState("当前会话工作区已被清理，之前的回归验证结果已失效。");
          refreshCodingRegressionAvailability();
        }
        try {
          const storageParams = new URLSearchParams({ repository: repo, head_ref: headRef });
          const refreshed = await getJson(`/api/code/workspaces/storage?${storageParams}`);
          if (!isCurrent()) return;
          renderWorkspaceStorageReport(refreshed);
          statusEl.textContent = `清理完成：移除 ${(pruneReport.pruned || []).length} 个工作区，并已刷新存储统计。`;
        } catch (refreshError) {
          if (!isCurrent()) return;
          $("coding-ws-result").hidden = true;
          statusEl.textContent = `清理已执行（移除 ${(pruneReport.pruned || []).length} 个工作区），但刷新存储失败：${refreshError.message}`;
        }
      } else {
        statusEl.textContent = `预演完成：预计可清理 ${(pruneReport.pruned || []).length} 个工作区（未删除文件）。`;
      }
    }
    statusEl.hidden = false;
  } catch (error) {
    if (!isCurrent()) return;
    if (mode === "audit" || mode === "storage") $("coding-ws-result").hidden = true;
    else $("coding-ws-prune-result").hidden = true;
    statusEl.textContent = `工作区治理操作失败：${error.message}`;
    statusEl.hidden = false;
  } finally {
    if (isCurrent()) {
      codingWsGovRunning = false;
      refreshCodingGovernanceAvailability();
    }
  }
}

async function runFreshnessCheck() {
  const repo = $("coding-repo").value.trim();
  const statusEl = $("coding-freshness-status");
  if (!repo) {
    statusEl.textContent = "请先输入 Git 仓库绝对路径。";
    statusEl.hidden = false;
    return;
  }
  if (codingFreshnessRunning) return;
  const requestGeneration = ++codingFreshnessGeneration;
  const isCurrent = () => requestGeneration === codingFreshnessGeneration &&
    $("coding-repo").value.trim() === repo;
  codingFreshnessRunning = true;
  refreshCodingGovernanceAvailability();
  statusEl.textContent = "正在只读检查依赖声明与 lockfile 新鲜度…";
  statusEl.hidden = false;
  try {
    const report = await postJson("/api/sandbox/freshness", {
      repository: repo,
      lock_file: $("coding-freshness-lock").value.trim() || null,
      requirements_file: $("coding-freshness-req").value.trim() || null,
    });
    if (!isCurrent()) return;
    renderFreshnessReport(report);
    statusEl.textContent = `依赖新鲜度检查完成：${freshnessStatusLabel(report.status)}。`;
    statusEl.hidden = false;
  } catch (error) {
    if (!isCurrent()) return;
    $("coding-freshness-result").hidden = true;
    statusEl.textContent = `依赖新鲜度检查失败：${error.message}`;
    statusEl.hidden = false;
  } finally {
    if (isCurrent()) {
      codingFreshnessRunning = false;
      refreshCodingGovernanceAvailability();
    }
  }
}

$("coding-ws-audit-btn").addEventListener("click", () => runWorkspaceGovernanceAction("audit"));
$("coding-ws-storage-btn").addEventListener("click", () => runWorkspaceGovernanceAction("storage"));
$("coding-ws-prune-dry-btn").addEventListener("click", () => runWorkspaceGovernanceAction("prune-dry"));
$("coding-ws-prune-exec-btn").addEventListener("click", () => runWorkspaceGovernanceAction("prune-exec"));
$("coding-ws-prune-confirm").addEventListener("change", refreshCodingGovernanceAvailability);
$("coding-freshness-btn").addEventListener("click", runFreshnessCheck);

async function loadRepoEnvironment() {
  syncCodingGovernanceRepo();
  const repo = $("coding-repo").value.trim();
  const infoEl = $("coding-env-info");
  if (!repo) {
    infoEl.hidden = true;
    return;
  }
  try {
    const params = new URLSearchParams({ repository: repo });
    const response = await fetch(`/api/code/environment?${params}`);
    if (!response.ok) {
      infoEl.hidden = true;
      return;
    }
    const env = await response.json();
    const details = [
      `布局: ${env.layout === "src" ? "src 结构" : "扁平结构"}`,
      `测试: ${env.test_framework} (${env.test_paths.join(", ") || "默认"})`,
    ];
    if (env.dependency_files.length) details.push(`依赖: ${env.dependency_files.join(", ")}`);
    if (env.lock_files.length) details.push(`锁定: ${env.lock_files.join(", ")}`);
    if (env.suggested_image && !$("coding-image").value) {
      details.push(`推荐镜像: ${env.suggested_image}`);
    }
    infoEl.textContent = `已识别环境：${details.join(" · ")}`;
    infoEl.hidden = false;
  } catch (_e) {
    infoEl.hidden = true;
  }
}

$("coding-repo").addEventListener("input", syncCodingGovernanceRepo);
$("coding-repo").addEventListener("change", loadRepoEnvironment);
$("coding-repo").addEventListener("blur", loadRepoEnvironment);
$("coding-workspace").addEventListener("change", refreshCodingAvailability);
$("coding-confirm-direct").addEventListener("change", refreshCodingAvailability);
$("coding-sandbox").addEventListener("change", refreshCodingAvailability);
$("coding-trust").addEventListener("change", refreshCodingAvailability);
const savedCodingRepo = localStorage.getItem("selfforge-coding-repo");
if (savedCodingRepo) {
  $("coding-repo").value = savedCodingRepo;
  loadRepoEnvironment();
}
syncCodingGovernanceRepo();
const savedCodingJob = localStorage.getItem("selfforge-coding-job") ||
  sessionStorage.getItem("selfforge-coding-job");
if (savedCodingJob) pollCodingJob(savedCodingJob);

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function showFile(file, button) {
  document.querySelectorAll(".file-item").forEach((item) => item.classList.remove("active"));
  button.classList.add("active");
  $("selected-path").textContent = file.path;
  const body = $("diff-body");
  body.replaceChildren();
  if (!file.patch_available || !file.hunks.length) {
    body.append(element("div", "diff-empty", file.patch_available ? "此文件没有可展示的文本 hunk。" : "GitHub 没有提供此文件的文本 patch。"));
    return;
  }
  for (const hunk of file.hunks) {
    body.append(element("div", "hunk-header", hunk.header));
    for (const line of hunk.lines) {
      const row = element("div", `diff-line ${line.kind}`);
      row.append(element("span", "ln", line.old_line ?? ""));
      row.append(element("span", "ln", line.new_line ?? ""));
      row.append(element("code", "", `${line.kind === "added" ? "+" : line.kind === "removed" ? "-" : " "}${line.content}`));
      body.append(row);
    }
  }
}

function showFindings(findings) {
  const list = $("finding-list");
  list.replaceChildren();
  if (!findings.length) {
    list.append(element("div", "finding-empty", "当前规则与模型没有返回高置信风险。\n这不等于代码已被证明没有问题。"));
    return;
  }
  for (const finding of findings) {
    const card = element("div", "finding");
    const top = element("div", "finding-top");
    top.append(element("span", `severity ${finding.severity}`, finding.severity));
    top.append(element("span", "path", `${finding.file_path}:${finding.line_number}`));
    card.append(top);
    card.append(element("strong", "", finding.category));
    card.append(element("p", "", finding.description));
    card.append(element("small", "", `${finding.source} · ${finding.verification_status}`));
    card.append(element("code", "", finding.evidence));
    list.append(card);
  }
}

function render(data) {
  current = data;
  const { pr, report } = data;
  $("empty-state").hidden = true;
  $("results").hidden = false;
  $("checkout-result").hidden = true;
  $("repair-result").hidden = true;
  $("testgen-result").hidden = true;
  verifiedPatch = "";
  differentialTest = "";
  $("pr-number").textContent = `#${pr.number}`;
  $("pr-title").textContent = pr.title;
  $("pr-meta").textContent = `${pr.owner}/${pr.repo} · ${pr.author} · ${pr.state} · ${pr.base_branch} ← ${pr.head_branch}`;
  $("github-link").href = pr.url;
  $("metric-files").textContent = pr.context.files.length;
  $("metric-findings").textContent = report.findings.length;
  $("metric-status").textContent = data.model_provider ? providerLabel(data.model_provider) : data.ai_error ? "部分完成" : "规则完成";
  $("file-count").textContent = `${pr.context.files.length} files`;

  const notes = [...pr.warnings];
  if (data.ai_error) notes.unshift(`${data.ai_error}。规则审查结果仍可查看。`);
  $("warnings").textContent = notes.join(" · ");
  $("warnings").hidden = !notes.length;
  $("model-summary").textContent = data.model_attempts.map(attemptText).join(" → ");
  $("model-summary").hidden = !data.model_attempts.length;

  const risks = $("testgen-risk");
  risks.replaceChildren();
  const optNone = element("option", "", "不关联，手动描述行为");
  optNone.value = "";
  risks.append(optNone);
  const optAuto = element("option", "", "⚡ 自动选择最高优先级风险 (Auto-prioritize)");
  optAuto.value = "auto";
  risks.append(optAuto);
  report.findings.forEach((finding, index) => {
    const option = element("option", "", `${finding.file_path}:${finding.line_number} · ${finding.category} · ${finding.description}`);
    option.value = String(index);
    risks.append(option);
  });
  $("testgen-task").value = "";

  const files = $("file-list");
  files.replaceChildren();
  pr.context.files.forEach((file, index) => {
    const button = element("button", "file-item", file.path);
    button.type = "button";
    button.append(element("small", "", `${file.status} · ${file.hunks.length} hunks${file.patch_available ? "" : " · patch 不可用"}`));
    button.addEventListener("click", () => showFile(file, button));
    files.append(button);
    if (index === 0) showFile(file, button);
  });
  if (!pr.context.files.length) $("diff-body").replaceChildren(element("div", "diff-empty", "PR 没有变更文件。"));
  showFindings(report.findings);
}

$("review-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  showAlert("");
  setLoading(true);
  try {
    const data = await postJson("/api/github/review", {
      url: $("pr-url").value.trim(),
      provider: $("review-provider").value,
      fallback: $("review-fallback").checked,
    });
    render(data);
  } catch (error) {
    showAlert(error.message);
  } finally {
    setLoading(false);
  }
});

$("checkout-button").addEventListener("click", async () => {
  if (!current) return;
  const button = $("checkout-button");
  button.disabled = true;
  button.textContent = "正在下载…";
  showAlert("");
  try {
    const result = await postJson("/api/github/checkout", { url: current.pr.url });
    $("checkout-result").textContent = `已下载到 ${result.path}，本地分支 ${result.branch}。可以在 IDE 中打开并修改；提交后是否能推回原 PR 取决于该分支权限，也可以推到自己的 fork 再开 PR。`;
    $("checkout-result").hidden = false;
  } catch (error) {
    showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "下载可编辑工作区";
  }
});

$("repair-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!current || $("repair-button").disabled) return;
  const host = $("repair-sandbox").value === "host";
  showAlert("");
  const button = $("repair-button");
  button.disabled = true;
  button.textContent = "正在验证修复…";
  try {
    const response = await postJson("/api/github/repair", {
      url: current.pr.url,
      task: $("repair-task").value.trim(),
      trust_local_tests: host && $("trust-tests").checked,
      sandbox: $("repair-sandbox").value,
      provider: $("repair-provider").value,
      fallback: $("repair-fallback").checked,
    });
    const report = response.report;
    verifiedPatch = report.verified_patch;
    $("repair-status").textContent = `状态：${report.status}`;
    $("repair-detail").textContent = `Base ${report.base_test?.status ?? "未运行"} · Head ${report.head_test?.status ?? "未运行"} · ${report.attempts.length} 次尝试 · ${$("repair-sandbox").value} 测试 · 轨迹 ID：${report.run_id} · 编码模型：${response.model_provider ? providerLabel(response.model_provider) : "未调用"} · 本地 checkout：${response.checkout.path}${report.errors.length ? ` · ${report.errors.join("; ")}` : ""}${response.model_attempts.length ? ` · ${response.model_attempts.map(attemptText).join(" → ")}` : ""}`;
    $("repair-patch").textContent = verifiedPatch || "没有生成已验证补丁。请查看报告中的测试和错误信息。";
    $("download-patch").hidden = !verifiedPatch;
    $("repair-result").hidden = false;
  } catch (error) {
    showAlert(error.message);
  } finally {
    refreshRepairAvailability();
    button.textContent = "运行验证修复";
  }
});

$("download-patch").addEventListener("click", () => {
  if (!verifiedPatch) return;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([verifiedPatch], { type: "text/x-patch" }));
  link.download = "verified-repair.patch";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});

$("testgen-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!current || $("testgen-button").disabled) return;
  const host = $("testgen-sandbox").value === "host";
  const button = $("testgen-button");
  showAlert("");
  button.disabled = true;
  button.textContent = "正在生成与验证…";
  try {
    const riskIndex = $("testgen-risk").value;
    const isAuto = riskIndex === "auto";
    const finding = (riskIndex === "" || isAuto) ? null : current.report.findings[Number(riskIndex)];
    const target = finding ? {
      file_path: finding.file_path, line_number: finding.line_number,
      description: finding.description, evidence: finding.evidence,
      category: finding.category,
    } : null;
    const response = await postJson("/api/github/generate-tests", {
      url: current.pr.url,
      task: $("testgen-task").value.trim() || (isAuto ? "验证优先级最高的变更风险" : ""),
      sandbox: $("testgen-sandbox").value,
      trust_local_tests: host && $("testgen-trust").checked,
      provider: $("testgen-provider").value,
      fallback: $("testgen-fallback").checked,
      max_candidates: Number($("testgen-count").value),
      target,
      auto_target: isAuto,
    });
    const batch = response.batch;
    const selected = batch.candidates.find((item) => item.candidate_index === batch.selected_candidate);
    differentialTest = selected?.generated_test?.test_code || "";
    $("testgen-status").textContent = `状态：${batch.status}`;
    const inv = batch.selected_causal_investigation || selected?.causal_investigation;
    const cost = batch.total_cost || selected?.cost;
    let meta = `${batch.candidates.length} 次尝试 · 停止原因：${batch.stop_reason} · ${batch.target ? `关联 ${batch.target.file_path}:${batch.target.line_number}（待验证假设）` : "未关联审查风险"}`;
    if (inv) {
      meta += ` · 堆栈关联线索：${inv.confidence}（${inv.explanation}）`;
    }
    if (cost) {
      meta += ` · 预估成本：$${cost.total_cost_usd.toFixed(6)} ${cost.currency}`;
    }
    meta += ` · 轨迹 ID：${batch.run_id}`;
    $("testgen-detail").textContent = meta;
    const list = $("testgen-candidates");
    list.replaceChildren();
    batch.candidates.forEach((candidate) => {
      const item = element("button", "coding-diff-file", `候选 ${candidate.candidate_index} · ${candidate.status} · Base ${candidate.base_test?.status ?? "未运行"} · Head ${candidate.head_test?.status ?? "未运行"}`);
      item.type = "button";
      item.addEventListener("click", () => {
        list.querySelectorAll("button").forEach((button) => button.classList.remove("active"));
        item.classList.add("active");
        $("testgen-candidate-detail").textContent = `${providerLabel(candidate.provider || "未调用")} / ${candidate.model || ""} · Base ${candidate.base_test?.passed ?? 0} 通过 · Head ${candidate.head_test?.failed ?? 0} 失败${candidate.errors.length ? ` · ${candidate.errors.join("; ")}` : ""}`;
        $("testgen-code").textContent = candidate.generated_test?.test_code || "没有生成可运行的测试。";
      });
      list.append(item);
      if (candidate === (selected || batch.candidates[batch.candidates.length - 1])) item.click();
    });
    $("download-test").hidden = !differentialTest;
    $("testgen-result").hidden = false;
  } catch (error) {
    showAlert(error.message);
  } finally {
    refreshTestgenAvailability();
    button.textContent = "生成并差异验证";
  }
});

$("testgen-risk").addEventListener("change", () => {
  const value = $("testgen-risk").value;
  if (value === "auto") {
    $("testgen-task").value = "验证优先级最高的变更风险";
  } else if (value !== "" && current) {
    $("testgen-task").value = current.report.findings[Number(value)].description;
  }
});

$("download-test").addEventListener("click", () => {
  if (!differentialTest) return;
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([differentialTest], { type: "text/x-python" }));
  link.download = "test_selfforge_regression.py";
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
});

$("check-providers").addEventListener("click", async () => {
  const button = $("check-providers");
  button.disabled = true;
  button.textContent = "正在检查…";
  try {
    const statuses = await postJson("/api/providers/check", { provider: "all" });
    $("provider-status").textContent = statuses.map((item) => `${providerLabel(item.name)}：${providerStatusLabel(item.status)}${item.balance ? ` · 余额 ${item.balance}` : ""}${item.usage ? ` · ${item.usage}` : ""}${item.error ? ` · ${item.error}` : ""}`).join("\n");
    $("provider-status").hidden = false;
  } catch (error) {
    showAlert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "检查密钥与额度";
  }
});

async function syncModels(name) {
  const status = $(`${name}-sync-state`);
  status.textContent = "正在同步模型列表…";
  try {
    const response = await fetch(`/api/providers/${name}/models`);
    const models = await response.json();
    if (!response.ok) throw new Error(models.detail || `HTTP ${response.status}`);
    const list = $(`${name}-models`);
    list.replaceChildren(...models.map((model) => {
      const option = document.createElement("option");
      option.value = model.id;
      option.label = model.name;
      return option;
    }));
    status.textContent = `已同步 ${models.length} 个模型。输入名称或 ID 选择，也可手动填写。`;
  } catch (error) {
    status.textContent = `同步失败：${error.message}。仍可手动输入模型 ID。`;
  }
}

function applyConfig(config) {
  configuredProviders = config.providers.filter((item) => item.configured);
  $("model-state").textContent = configuredProviders.length ? `已配置：${configuredProviders.map((item) => `${item.label} (${item.model})`).join("、")}` : "未检测到模型密钥，规则审查可用";
  for (const item of config.providers) {
    const urlInput = $(`${item.name}-url`);
    const modelInput = $(`${item.name}-model`);
    const keyState = $(`${item.name}-key-state`);
    if (urlInput) urlInput.value = item.base_url;
    if (modelInput) modelInput.value = item.model;
    if (keyState) {
      keyState.textContent = item.configured ? `已连接 · ${item.key_source === "local" ? "本地密钥" : "环境变量"}` : "未连接";
    }
  }
  for (const select of [$("review-provider"), $("repair-provider"), $("coding-provider"), $("testgen-provider")]) {
    for (const option of select.options) {
      if (!["auto", "rules"].includes(option.value)) option.disabled = !configuredProviders.some((item) => item.name === option.value);
    }
  }
  hostTestsEnabled = config.host_tests_enabled;
  refreshRepairAvailability();
  refreshTestgenAvailability();
  refreshCodingAvailability();
}

function refreshTestgenAvailability() {
  const host = $("testgen-sandbox").value === "host";
  $("testgen-trust-label").hidden = !host;
  $("testgen-button").disabled = !configuredProviders.length ||
    (host && (!hostTestsEnabled || !$("testgen-trust").checked));
  $("testgen-availability").textContent = !configuredProviders.length
    ? "需先配置模型密钥"
    : host ? (hostTestsEnabled ? "本机测试需明确勾选信任" : "本机测试需设置 SELFFORGE_ENABLE_HOST_TESTS=1")
      : "Docker 模式需先构建 selfforge-agent-sandbox:latest 镜像";
}

function refreshRepairAvailability() {
  const host = $("repair-sandbox").value === "host";
  $("host-trust-label").hidden = !host;
  $("repair-button").disabled = !configuredProviders.length || (host && (!hostTestsEnabled || !$("trust-tests").checked));
  $("repair-availability").textContent = !configuredProviders.length
    ? "需先配置模型密钥"
    : host ? (hostTestsEnabled ? "本机测试需明确勾选信任" : "本机测试需设置 SELFFORGE_ENABLE_HOST_TESTS=1")
      : "Docker 模式需先构建 selfforge-agent-sandbox:latest 镜像";
}

$("repair-sandbox").addEventListener("change", refreshRepairAvailability);
$("trust-tests").addEventListener("change", refreshRepairAvailability);
$("testgen-sandbox").addEventListener("change", refreshTestgenAvailability);
$("testgen-trust").addEventListener("change", refreshTestgenAvailability);

for (const form of document.querySelectorAll(".settings-card")) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = form.dataset.provider;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    $(`${name}-sync-state`).textContent = "正在保存…";
    try {
      await postJson("/api/providers/config", {
        provider: name,
        base_url: $(`${name}-url`).value.trim(),
        api_key: $(`${name}-key`).value.trim() || null,
        model: $(`${name}-model`).value.trim(),
      });
      $(`${name}-key`).value = "";
      const response = await fetch("/api/config");
      if (!response.ok) throw new Error(`配置读取失败 (${response.status})`);
      applyConfig(await response.json());
      await syncModels(name);
    } catch (error) {
      $(`${name}-sync-state`).textContent = `保存失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
}

document.querySelectorAll(".sync-models").forEach((button) => {
  button.addEventListener("click", () => syncModels(button.dataset.provider));
});

fetch("/api/config").then((response) => response.json()).then((config) => {
  applyConfig(config);
  for (const provider of configuredProviders) syncModels(provider.name);
}).catch(() => { $("model-state").textContent = "无法读取模型配置"; });
