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
let codingJobRunning = false;
let codingPollTimer = null;
let codingDiffJobId = null;

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
  $("coding-image-label").hidden = host;
  $("coding-direct-trust").hidden = !direct;
  $("coding-start").disabled = codingJobRunning || !configuredProviders.length ||
    (host && (!hostTestsEnabled || !$("coding-trust").checked)) ||
    (direct && !$("coding-confirm-direct").checked);
  $("coding-availability").textContent = !configuredProviders.length
    ? "需先配置模型密钥"
    : direct && !$("coding-confirm-direct").checked ? "直接修改原仓库需明确确认"
    : host ? (hostTestsEnabled ? "本机执行需明确勾选信任" : "本机执行需设置 SELFFORGE_ENABLE_HOST_TESTS=1")
      : "请使用已准备好项目依赖的本机镜像；运行测试时保持禁网";
}

function renderCodingJob(job) {
  activeCodingJob = job.job_id;
  currentCodingSession = job.session_id;
  currentCodingRepo = job.repository;
  currentCodingBranch = job.branch;
  currentCodingSandbox = job.sandbox || "docker";
  codingJobRunning = ["queued", "running"].includes(job.state);
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
  $("coding-cancel").hidden = !codingJobRunning;
  $("coding-cancel").disabled = job.cancel_requested;
  $("coding-resume-current").hidden = codingJobRunning || !job.can_resume;
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
  refreshCodingAvailability();
  try {
    const job = await postJson("/api/code/jobs", {
      repository: $("coding-repo").value.trim(), task, resume_session: resume || null,
      workspace: $("coding-workspace").value,
      confirm_direct: $("coding-workspace").value === "direct" && $("coding-confirm-direct").checked,
      sandbox: $("coding-sandbox").value, trust_local: host && $("coding-trust").checked,
      image: host ? null : $("coding-image").value.trim() || null,
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
  $("coding-workspace").value = currentCodingBranch ? "worktree" : "direct";
  $("coding-sandbox").value = currentCodingSandbox;
  $("coding-image").value = "";
  $("coding-trust").checked = false;
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
    $("coding-delivery").hidden = true;
    currentCodingBranch = "";
  } catch (error) {
    showCodingError(`${error.message}。请先在工作区提交或还原改动，再清理。`);
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

async function loadRepoEnvironment() {
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
  risks.replaceChildren(element("option", "", "不关联，手动描述行为"));
  risks.firstChild.value = "";
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
    const finding = riskIndex === "" ? null : current.report.findings[Number(riskIndex)];
    const target = finding ? {
      file_path: finding.file_path, line_number: finding.line_number,
      description: finding.description, evidence: finding.evidence,
      category: finding.category,
    } : null;
    const response = await postJson("/api/github/generate-tests", {
      url: current.pr.url, task: $("testgen-task").value.trim(),
      sandbox: $("testgen-sandbox").value,
      trust_local_tests: host && $("testgen-trust").checked,
      provider: $("testgen-provider").value,
      fallback: $("testgen-fallback").checked,
      max_candidates: Number($("testgen-count").value), target,
    });
    const batch = response.batch;
    const selected = batch.candidates.find((item) => item.candidate_index === batch.selected_candidate);
    differentialTest = selected?.generated_test?.test_code || "";
    $("testgen-status").textContent = `状态：${batch.status}`;
    $("testgen-detail").textContent = `${batch.candidates.length} 次尝试 · 停止原因：${batch.stop_reason} · ${batch.target ? `关联 ${batch.target.file_path}:${batch.target.line_number}（待验证假设）` : "未关联审查风险"} · 轨迹 ID：${batch.run_id}`;
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
  if (value !== "" && current) {
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
    $(`${item.name}-url`).value = item.base_url;
    $(`${item.name}-model`).value = item.model;
    $(`${item.name}-key-state`).textContent = item.configured ? `已连接 · ${item.key_source === "local" ? "本地密钥" : "环境变量"}` : "未连接";
  }
  for (const select of [$("review-provider"), $("repair-provider"), $("coding-provider"), $("testgen-provider")]) {
    for (const option of select.options) {
      if (["deepseek", "openrouter"].includes(option.value)) option.disabled = !configuredProviders.some((item) => item.name === option.value);
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
