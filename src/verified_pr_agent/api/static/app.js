const $ = (id) => document.getElementById(id);
let current = null;
let hostTestsEnabled = false;
let verifiedPatch = "";

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
  $("metric-status").textContent = data.ai_error ? "部分完成" : "完成";
  $("file-count").textContent = `${pr.context.files.length} files`;

  const notes = [...pr.warnings];
  if (data.ai_error) notes.unshift(`DeepSeek 审查失败：${data.ai_error}。规则审查结果仍可查看。`);
  $("warnings").textContent = notes.join(" · ");
  $("warnings").hidden = !notes.length;

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
      use_deepseek: $("use-deepseek").checked,
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
  if (!current || !hostTestsEnabled) return;
  showAlert("");
  const button = $("repair-button");
  button.disabled = true;
  button.textContent = "正在验证修复…";
  try {
    const response = await postJson("/api/github/repair", {
      url: current.pr.url,
      task: $("repair-task").value.trim(),
      trust_local_tests: $("trust-tests").checked,
    });
    const report = response.report;
    verifiedPatch = report.verified_patch;
    $("repair-status").textContent = `状态：${report.status}`;
    $("repair-detail").textContent = `Base ${report.base_test?.status ?? "未运行"} · Head ${report.head_test?.status ?? "未运行"} · ${report.attempts.length} 次尝试 · 本地 checkout：${response.checkout.path}${report.errors.length ? ` · ${report.errors.join("; ")}` : ""}`;
    $("repair-patch").textContent = verifiedPatch || "没有生成已验证补丁。请查看报告中的测试和错误信息。";
    $("download-patch").hidden = !verifiedPatch;
    $("repair-result").hidden = false;
  } catch (error) {
    showAlert(error.message);
  } finally {
    button.disabled = false;
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

fetch("/api/config").then((response) => response.json()).then((config) => {
  $("use-deepseek").disabled = !config.deepseek_available;
  $("model-state").textContent = config.deepseek_available ? "DeepSeek 已配置，可按需启用" : "未检测到 DEEPSEEK_API_KEY，规则审查可用";
  hostTestsEnabled = config.host_tests_enabled && config.deepseek_available;
  $("repair-button").disabled = !hostTestsEnabled;
  $("repair-availability").textContent = hostTestsEnabled ? "还需勾选上面的可信执行选项" : "需配置 DeepSeek 并设置 VPA_ENABLE_HOST_TESTS=1 后重启服务";
}).catch(() => { $("model-state").textContent = "无法读取模型配置"; });
