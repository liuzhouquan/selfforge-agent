const $ = (id) => document.getElementById(id);
let current = null;
let hostTestsEnabled = false;
let verifiedPatch = "";
let configuredProviders = [];
let activeCodingJob = null;
let currentCodingSession = null;
let currentCodingRepo = null;
let currentCodingBranch = "";
let codingJobRunning = false;
let codingPollTimer = null;

function providerLabel(name) {
  return name === "deepseek" ? "DeepSeek" : name === "openrouter" ? "OpenRouter" : name;
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

async function postJson(path, payload) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || `请求失败 (${response.status})`);
  return data;
}

function showCodingError(message) {
  $("coding-error").textContent = message;
  $("coding-error").hidden = !message;
}

function refreshCodingAvailability() {
  const host = $("coding-sandbox").value === "host";
  const direct = $("coding-workspace").value === "direct";
  $("coding-host-trust").hidden = !host;
  $("coding-direct-trust").hidden = !direct;
  $("coding-start").disabled = codingJobRunning || !configuredProviders.length ||
    (host && (!hostTestsEnabled || !$("coding-trust").checked)) ||
    (direct && !$("coding-confirm-direct").checked);
  $("coding-availability").textContent = !configuredProviders.length
    ? "需先配置模型密钥"
    : direct && !$("coding-confirm-direct").checked ? "直接修改原仓库需明确确认"
    : host ? (hostTestsEnabled ? "本机执行需明确勾选信任" : "本机执行需设置 SELFFORGE_ENABLE_HOST_TESTS=1")
      : "Docker 模式需先构建 selfforge-agent-sandbox:latest 镜像";
}

function renderCodingJob(job) {
  activeCodingJob = job.job_id;
  currentCodingSession = job.session_id;
  currentCodingRepo = job.repository;
  currentCodingBranch = job.branch;
  codingJobRunning = ["queued", "running"].includes(job.state);
  const labels = {
    queued: "等待运行", running: "正在执行", completed: "已验证完成",
    unverified: "完成但未通过测试验证", failed: "执行失败",
    max_steps: "达到最大步数", timed_out: "超出时间预算", cancelled: "已取消",
  };
  $("coding-job").hidden = false;
  $("coding-state").textContent = labels[job.state] || job.state;
  $("coding-job-meta").textContent = `会话 ${job.session_id} · ${job.steps} 步 · ${job.tools_used} 次工具调用${job.cancel_requested ? " · 已请求取消" : ""}`;
  $("coding-workspace-info").textContent = job.branch
    ? `独立工作区：${job.workspace_path} · 分支 ${job.branch} · 基于 ${job.base_sha.slice(0, 12)}`
    : `直接修改：${job.workspace_path}`;
  $("coding-answer").textContent = job.error || job.answer || "Agent 正在处理任务…";
  $("coding-cancel").hidden = !codingJobRunning;
  $("coding-cancel").disabled = job.cancel_requested;
  $("coding-resume-current").hidden = codingJobRunning;
  $("coding-remove-workspace").hidden = codingJobRunning || !job.branch;
  $("coding-trace").textContent = job.trace.map((event) => {
    const detail = event.name || event.status || event.provider || event.error_type || "";
    const outcome = event.ok === undefined ? "" : event.ok ? " · 成功" : " · 失败";
    return `${(event.at || "").slice(11, 19)} · ${event.event}${detail ? ` · ${detail}` : ""}${outcome}`;
  }).join("\n") || "等待执行轨迹…";
  if (codingJobRunning) sessionStorage.setItem("selfforge-coding-job", job.job_id);
  else sessionStorage.removeItem("selfforge-coding-job");
  refreshCodingAvailability();
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
    codingJobRunning = false;
    refreshCodingAvailability();
  }
}

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
  refreshCodingAvailability();
  try {
    const job = await postJson("/api/code/jobs", {
      repository: $("coding-repo").value.trim(), task, resume_session: resume || null,
      workspace: $("coding-workspace").value,
      confirm_direct: $("coding-workspace").value === "direct" && $("coding-confirm-direct").checked,
      sandbox: $("coding-sandbox").value, trust_local: host && $("coding-trust").checked,
      provider: $("coding-provider").value, fallback: $("coding-fallback").checked,
      max_steps: Number($("coding-steps").value), time_budget: Number($("coding-time").value),
    });
    renderCodingJob(job);
    $("coding-confirm-direct").checked = false;
    $("coding-trust").checked = false;
    refreshCodingAvailability();
    if (codingJobRunning) codingPollTimer = setTimeout(() => pollCodingJob(job.job_id), 500);
  } catch (error) {
    showCodingError(error.message);
    codingJobRunning = false;
    refreshCodingAvailability();
  }
});

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
  $("coding-workspace").value = currentCodingBranch ? "worktree" : "direct";
  refreshCodingAvailability();
  $("coding-task-text").focus();
});

$("coding-remove-workspace").addEventListener("click", async () => {
  if (!currentCodingSession || !currentCodingRepo || !currentCodingBranch) return;
  showCodingError("");
  try {
    await postJson(`/api/code/workspaces/${encodeURIComponent(currentCodingSession)}/remove`, {
      repository: currentCodingRepo,
    });
    $("coding-workspace-info").textContent += " · 工作区已清理，分支仍保留";
    $("coding-remove-workspace").hidden = true;
    $("coding-resume-current").hidden = true;
    currentCodingBranch = "";
  } catch (error) {
    showCodingError(`${error.message}。请先在工作区提交或还原改动，再清理。`);
  }
});

$("coding-workspace").addEventListener("change", refreshCodingAvailability);
$("coding-confirm-direct").addEventListener("change", refreshCodingAvailability);
$("coding-sandbox").addEventListener("change", refreshCodingAvailability);
$("coding-trust").addEventListener("change", refreshCodingAvailability);
const savedCodingJob = sessionStorage.getItem("selfforge-coding-job");
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
  verifiedPatch = "";
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
    $(`${item.name}-url`).value = item.base_url;
    $(`${item.name}-model`).value = item.model;
    $(`${item.name}-key-state`).textContent = item.configured ? `已连接 · ${item.key_source === "local" ? "本地密钥" : "环境变量"}` : "未连接";
  }
  for (const select of [$("review-provider"), $("repair-provider"), $("coding-provider")]) {
    for (const option of select.options) {
      if (["deepseek", "openrouter"].includes(option.value)) option.disabled = !configuredProviders.some((item) => item.name === option.value);
    }
  }
  hostTestsEnabled = config.host_tests_enabled;
  refreshRepairAvailability();
  refreshCodingAvailability();
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
