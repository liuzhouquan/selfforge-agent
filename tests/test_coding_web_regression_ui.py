"""UI and API contract tests for SF-TEST-02 regression validation workbench integration."""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.coding.regression_validation import (
    RegressionRunSummary,
    RegressionValidationReport,
)

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "selfforge_agent" / "api" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"
STYLE_CSS = STATIC_DIR / "style.css"


class _DomInventoryParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, object]] = []
        self.stack: list[str] = []
        self.ancestors_by_id: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k: ("" if v is None else v) for k, v in attrs}
        elem_id = attr_map.get("id", "")
        classes = [item for item in attr_map.get("class", "").split() if item]
        if elem_id:
            self.ancestors_by_id[elem_id] = [item for item in self.stack if item]
        self.elements.append(
            {
                "tag": tag.lower(),
                "id": elem_id,
                "classes": classes,
                "attrs": attr_map,
            }
        )
        if tag.lower() not in {"input", "br", "hr", "img", "meta", "link"}:
            self.stack.append(elem_id)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in {"input", "br", "hr", "img", "meta", "link"} and self.stack:
            self.stack.pop()


def _parse_index_inventory() -> _DomInventoryParser:
    parser = _DomInventoryParser()
    parser.feed(INDEX_HTML.read_text(encoding="utf-8"))
    return parser


def _run_app_js_scenario(scenario_js: str) -> dict[str, object]:
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js unavailable")
    inventory = _parse_index_inventory()
    harness = r"""
const fs = require("node:fs");
const vm = require("node:vm");

const INVENTORY = __INVENTORY__;
const APP_JS_PATH = __APP_JS_PATH__;

class ClassList {
  constructor(initial = []) {
    this._set = new Set(initial);
  }
  add(...items) { items.forEach((i) => this._set.add(i)); }
  remove(...items) { items.forEach((i) => this._set.delete(i)); }
  contains(item) { return this._set.has(item); }
  toggle(item) {
    if (this._set.has(item)) { this._set.delete(item); return false; }
    this._set.add(item); return true;
  }
}

class FakeNode {
  constructor(tag, { id = "", classes = [], attrs = {} } = {}) {
    this.tagName = String(tag || "div").toUpperCase();
    this.id = id;
    this.attrs = { ...attrs };
    this.className = classes.join(" ");
    this.classList = new ClassList(classes);
    this.children = [];
    this.listeners = new Map();
    this.style = {};
    this.dataset = {};
    for (const [k, v] of Object.entries(this.attrs)) {
      if (k.startsWith("data-")) {
        this.dataset[k.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase())] = v;
      }
    }
    this.hidden = Object.prototype.hasOwnProperty.call(this.attrs, "hidden");
    this.disabled = Object.prototype.hasOwnProperty.call(this.attrs, "disabled");
    this.checked = Object.prototype.hasOwnProperty.call(this.attrs, "checked");
    this.value = this.attrs.value !== undefined ? String(this.attrs.value) : "";
    this.type = this.attrs.type || "";
    this.title = "";
    this.href = "";
    this.target = "";
    this.rel = "";
    this.label = "";
    this._textContent = "";
    this._innerHTMLWrites = 0;
  }
  get textContent() {
    if (this.children.length) {
      return this._textContent + this.children.map((c) => c.textContent).join(" ");
    }
    return this._textContent;
  }
  set textContent(val) {
    this.children = [];
    this._textContent = val == null ? "" : String(val);
  }
  get innerHTML() {
    throw new Error("innerHTML access is forbidden in security-checked DOM harness");
  }
  set innerHTML(_val) {
    this._innerHTMLWrites += 1;
    throw new Error("innerHTML assignment is forbidden in app.js");
  }
  get options() {
    return this.children;
  }
  append(...nodes) {
    for (const node of nodes) {
      this.children.push(typeof node === "string" ? createTextNode(node) : node);
    }
  }
  replaceChildren(...nodes) {
    this._textContent = "";
    this.children = [];
    this.append(...nodes);
  }
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }
  async dispatchEventAsync(event) {
    const evt = {
      type: event.type,
      target: event.target || this,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
    };
    for (const fn of this.listeners.get(evt.type) || []) {
      await fn(evt);
    }
    return evt;
  }
  click() {
    for (const fn of this.listeners.get("click") || []) fn({ type: "click", target: this });
  }
  focus() {}
  querySelector(selector) {
    if (selector === 'button[type="submit"]') {
      return this.children.find((c) => c.tagName === "BUTTON" && (c.type === "submit" || !c.type))
        || new FakeNode("button", { attrs: { type: "submit" } });
    }
    return null;
  }
  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => {
      for (const child of node.children) {
        if (selector === "button" && child.tagName === "BUTTON") out.push(child);
        else if (selector.startsWith(".") && child.classList.contains(selector.slice(1))) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

function createTextNode(text) {
  return { tagName: "#TEXT", textContent: String(text), children: [] };
}

const byId = new Map();
const allNodes = [];
for (const entry of INVENTORY) {
  const node = new FakeNode(entry.tag, {
    id: entry.id,
    classes: entry.classes,
    attrs: entry.attrs,
  });
  allNodes.push(node);
  if (entry.id) byId.set(entry.id, node);
}
// Ensure select defaults select their first option value if options exist in HTML
let activeSelect = null;
for (let i = 0; i < INVENTORY.length; i += 1) {
  const entry = INVENTORY[i];
  const node = allNodes[i];
  if (entry.tag === "select") {
    activeSelect = node;
  } else if (entry.tag === "option" && activeSelect) {
    activeSelect.children.push(node);
    if (!activeSelect.value) activeSelect.value = node.value || node.attrs.value || "";
  } else if (entry.tag !== "option") {
    activeSelect = null;
  }
}

const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new FakeNode(tag); },
  createTextNode,
  querySelectorAll(selector) {
    if (selector.startsWith(".")) {
      const cls = selector.slice(1);
      return allNodes.filter((n) => n.classList.contains(cls));
    }
    return [];
  },
};

const makeStorage = () => {
  const store = new Map();
  return {
    getItem(k) { return store.has(k) ? store.get(k) : null; },
    setItem(k, v) { store.set(k, String(v)); },
    removeItem(k) { store.delete(k); },
  };
};

const fetchCalls = [];
let fetchHandler = async (url, options = {}) => {
  if (url === "/api/config") {
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          providers: [{ name: "deepseek", label: "DeepSeek", model: "deepseek-chat", base_url: "https://api.deepseek.com", configured: true, key_source: "env" }],
          host_tests_enabled: false,
        };
      },
    };
  }
  if (String(url).startsWith("/api/providers/") && String(url).endsWith("/models")) {
    return { ok: true, status: 200, async json() { return []; } };
  }
  if (String(url).startsWith("/api/code/environment")) {
    return { ok: false, status: 404, async json() { return { detail: "none" }; } };
  }
  if (String(url).includes("/diff")) {
    return {
      ok: true,
      status: 200,
      async json() { return { files: [{ status: "modified", path: "calc.py", patch: "@@ -1 +1 @@\n-a\n+b" }], omitted_files: 0, base_sha: "abc123456789" }; },
    };
  }
  throw new Error(`Unhandled mock fetch: ${url}`);
};

const context = {
  document,
  localStorage: makeStorage(),
  sessionStorage: makeStorage(),
  URLSearchParams,
  URL: { createObjectURL: () => "blob:mock", revokeObjectURL: () => {} },
  Blob: class {},
  alert: () => {},
  confirm: () => true,
  setTimeout: (_fn, _ms) => 1,
  clearTimeout: () => {},
  Error,
  Number,
  String,
  Boolean,
  Object,
  Array,
  JSON,
  Map,
  Set,
  Math,
  fetch: async (url, options) => {
    fetchCalls.push({ url: String(url), options: options || {} });
    return fetchHandler(String(url), options || {});
  },
  setFetchHandler(fn) { fetchHandler = fn; },
  getFetchCalls() { return fetchCalls; },
  async flushMicrotasks() {
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
  },
};
context.window = context;

vm.createContext(context);
const appSource = fs.readFileSync(APP_JS_PATH, "utf8");
vm.runInContext(appSource, context, { filename: "app.js" });

(async () => {
  await context.flushMicrotasks();
  const result = await vm.runInContext(`(async () => { ${__SCENARIO__} })()`, context);
  process.stdout.write(JSON.stringify(result));
})().catch((err) => {
  process.stderr.write((err && err.stack) || String(err));
  process.exit(1);
});
"""
    script = (
        harness.replace("__INVENTORY__", json.dumps(inventory.elements))
        .replace("__APP_JS_PATH__", json.dumps(str(APP_JS)))
        .replace("__SCENARIO__", json.dumps(scenario_js))
    )
    proc = subprocess.run(
        [node_bin, "-e", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert proc.returncode == 0, f"Node scenario failed:\n{proc.stderr}\n{proc.stdout}"
    return json.loads(proc.stdout)


def test_coding_workbench_html_contains_regression_panel_inside_coding_job() -> None:
    client = TestClient(api_module.app)
    html_res = client.get("/")
    assert html_res.status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/style.css").status_code == 200

    inventory = _parse_index_inventory()
    required_ids = [
        "coding-regression",
        "coding-regression-availability",
        "coding-regression-form",
        "coding-regression-test-file",
        "coding-regression-timeout",
        "coding-regression-btn",
        "coding-regression-status",
        "coding-regression-result",
    ]
    for elem_id in required_ids:
        assert elem_id in inventory.ancestors_by_id, f"Missing #{elem_id} in index.html"
        assert "coding-job" in inventory.ancestors_by_id[elem_id], (
            f"#{elem_id} must sit inside #coding-job"
        )

    by_id = {entry["id"]: entry for entry in inventory.elements if entry["id"]}
    timeout_attrs = by_id["coding-regression-timeout"]["attrs"]
    assert timeout_attrs["type"] == "number"
    assert timeout_attrs["min"] == "1"
    assert timeout_attrs["max"] == "180"
    assert timeout_attrs["value"] == "60"
    assert "innerHTML" not in APP_JS.read_text(encoding="utf-8")


def test_web_ui_submits_regression_validation_and_renders_report_safely() -> None:
    report = RegressionValidationReport(
        status="confirmed",
        reason="base_failed_repaired_passed",
        session_id="sess1234",
        base_sha="1111222233334444555566667777888899990000",
        workspace_sha256="aaaa" * 16,
        test_sha256="bbbb" * 16,
        base_result=RegressionRunSummary(
            status="failed",
            exit_code=1,
            duration_seconds=0.82,
            passed=0,
            failed=1,
            errors=0,
            skipped=0,
        ),
        repaired_result=RegressionRunSummary(
            status="passed",
            exit_code=0,
            duration_seconds=0.64,
            passed=1,
            failed=0,
            errors=0,
            skipped=0,
        ),
    )
    report_json = json.dumps(report.model_dump())

    scenario = f"""
    const reportPayload = {report_json};
    renderCodingJob({{
      job_id: "job-docker-1",
      session_id: "sess1234",
      state: "unverified",
      answer: "Fixed bug <img src=x onerror=alert(1)>",
      steps: 2,
      tools_used: 3,
      error: null,
      sandbox: "docker",
      image_id: "sha256:deadbeefcafebabe",
      cancel_requested: false,
      repository: "/tmp/example-repo",
      workspace_path: "/tmp/worktrees/sess1234",
      branch: "selfforge/sess1234",
      base_sha: "1111222233334444",
      can_resume: true,
      trace: [{{ event: "model_call", usage: {{ prompt_tokens: 10, completion_tokens: 5 }} }}],
      token_metrics: {{ prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 }},
      verification_passed: false,
    }});
    await flushMicrotasks();

    const metricBefore = $("metric-verification").textContent;
    const panelHidden = $("coding-regression").hidden;
    const btnDisabled = $("coding-regression-btn").disabled;

    let capturedBody = null;
    setFetchHandler(async (url, options) => {{
      if (url === "/api/code/workspaces/sess1234/validate-regression") {{
        capturedBody = JSON.parse(options.body);
        return {{ ok: true, status: 200, async json() {{ return reportPayload; }} }};
      }}
      return {{ ok: true, status: 200, async json() {{ return {{}}; }} }};
    }});

    $("coding-regression-test-file").value = " tests/test_bugfix.py ";
    $("coding-regression-timeout").value = "45";
    await $("coding-regression-form").dispatchEventAsync({{ type: "submit" }});

    return {{
      panelHidden,
      btnDisabled,
      capturedBody,
      metricBefore,
      metricAfter: $("metric-verification").textContent,
      statusHidden: $("coding-regression-status").hidden,
      statusText: $("coding-regression-status").textContent,
      resultHidden: $("coding-regression-result").hidden,
      resultText: $("coding-regression-result").textContent,
      btnDisabledAfter: $("coding-regression-btn").disabled,
    }};
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["panelHidden"] is False
    assert outcome["btnDisabled"] is False
    assert outcome["capturedBody"] == {
        "repository": "/tmp/example-repo",
        "test_file": "tests/test_bugfix.py",
        "timeout": 45,
    }
    # Independent regression must NEVER promote coding verification badge
    assert outcome["metricBefore"] == outcome["metricAfter"]
    assert outcome["statusHidden"] is False
    assert outcome["resultHidden"] is False
    assert outcome["btnDisabledAfter"] is False
    result_text = str(outcome["resultText"])
    assert "confirmed" in result_text
    assert "111122223333" in result_text
    assert "aaaaaaaaaaaa" in result_text
    assert "bbbbbbbbbbbb" in result_text
    assert "0.82" in result_text
    assert "0.64" in result_text


def test_web_ui_enforces_sandbox_worktree_timeout_and_error_guards() -> None:
    scenario = """
    const callsBefore = getFetchCalls().length;

    // 1) Direct workspace (!branch) hides regression panel
    renderCodingJob({
      job_id: "job-direct",
      session_id: "sess-direct",
      state: "completed",
      answer: "Done",
      steps: 1,
      tools_used: 1,
      sandbox: "docker",
      repository: "/tmp/repo",
      workspace_path: "/tmp/repo",
      branch: "",
      base_sha: "abc",
      can_resume: false,
      trace: [],
      verification_passed: true,
    });
    const directHidden = $("coding-regression").hidden;

    // 2) Host sandbox worktree disables validation submit
    renderCodingJob({
      job_id: "job-host",
      session_id: "sess-host",
      state: "unverified",
      answer: "Done",
      steps: 1,
      tools_used: 1,
      sandbox: "host",
      repository: "/tmp/repo",
      workspace_path: "/tmp/wt-host",
      branch: "selfforge/sess-host",
      base_sha: "abc123def456",
      can_resume: true,
      trace: [],
      verification_passed: false,
    });
    const hostHidden = $("coding-regression").hidden;
    const hostDisabled = $("coding-regression-btn").disabled;
    const hostAvailability = $("coding-regression-availability").textContent;
    $("coding-regression-test-file").value = "tests/test_x.py";
    await $("coding-regression-form").dispatchEventAsync({ type: "submit" });
    const callsAfterHostAttempt = getFetchCalls().filter((c) => c.url.includes("validate-regression")).length;

    // 3) Running docker job disables validation submit
    renderCodingJob({
      job_id: "job-running",
      session_id: "sess-run",
      state: "running",
      answer: "",
      steps: 1,
      tools_used: 1,
      sandbox: "docker",
      image_id: "sha256:1234",
      repository: "/tmp/repo",
      workspace_path: "/tmp/wt-run",
      branch: "selfforge/sess-run",
      base_sha: "abc123def456",
      can_resume: false,
      trace: [],
      verification_passed: false,
    });
    const runningDisabled = $("coding-regression-btn").disabled;

    // 4) Idle docker job rejects empty test_file & out-of-range timeout client-side, then handles HTTP 409 cleanly
    renderCodingJob({
      job_id: "job-idle",
      session_id: "sess-idle",
      state: "unverified",
      answer: "Ready",
      steps: 2,
      tools_used: 2,
      sandbox: "docker",
      image_id: "sha256:1234",
      repository: "/tmp/repo",
      workspace_path: "/tmp/wt-idle",
      branch: "selfforge/sess-idle",
      base_sha: "abc123def456",
      can_resume: true,
      trace: [],
      verification_passed: false,
    });
    $("coding-regression-test-file").value = "   ";
    $("coding-regression-timeout").value = "60";
    await $("coding-regression-form").dispatchEventAsync({ type: "submit" });
    const emptyFileStatus = $("coding-regression-status").textContent;

    $("coding-regression-test-file").value = "tests/test_ok.py";
    $("coding-regression-timeout").value = "181";
    await $("coding-regression-form").dispatchEventAsync({ type: "submit" });
    const badTimeoutStatus = $("coding-regression-status").textContent;
    const callsAfterBadInputs = getFetchCalls().filter((c) => c.url.includes("validate-regression")).length;

    setFetchHandler(async (url) => {
      if (url.includes("validate-regression")) {
        return { ok: false, status: 409, async json() { return { detail: "Target workspace differs" }; } };
      }
      return { ok: true, status: 200, async json() { return {}; } };
    });
    $("coding-regression-timeout").value = "30";
    await $("coding-regression-form").dispatchEventAsync({ type: "submit" });

    return {
      callsBefore,
      directHidden,
      hostHidden,
      hostDisabled,
      hostAvailability,
      callsAfterHostAttempt,
      runningDisabled,
      emptyFileStatus,
      badTimeoutStatus,
      callsAfterBadInputs,
      errorStatus: $("coding-regression-status").textContent,
      resultHiddenAfterError: $("coding-regression-result").hidden,
      diffStillVisible: !$("coding-diff").hidden,
      deliveryStillVisible: !$("coding-delivery").hidden,
      btnReenabledAfterError: !$("coding-regression-btn").disabled,
    };
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["directHidden"] is True
    assert outcome["hostHidden"] is False
    assert outcome["hostDisabled"] is True
    assert "Docker" in str(outcome["hostAvailability"])
    assert outcome["callsAfterHostAttempt"] == 0
    assert outcome["runningDisabled"] is True
    assert outcome["callsAfterBadInputs"] == 0
    assert outcome["emptyFileStatus"]
    assert outcome["badTimeoutStatus"]
    assert "Target workspace differs" in str(outcome["errorStatus"])
    assert outcome["resultHiddenAfterError"] is True
    assert outcome["diffStillVisible"] is True
    assert outcome["deliveryStillVisible"] is True
    assert outcome["btnReenabledAfterError"] is True


def test_web_ui_resets_regression_state_on_job_switch_and_workspace_removal() -> None:
    inconclusive_json = json.dumps(
        RegressionValidationReport(
            status="inconclusive",
            reason="both_passed",
            session_id="sess-a",
            base_sha="abc123",
            workspace_sha256="1" * 64,
            test_sha256="2" * 64,
            base_result=RegressionRunSummary(
                status="passed",
                exit_code=0,
                duration_seconds=0.2,
                passed=1,
                failed=0,
                errors=0,
                skipped=0,
            ),
            repaired_result=RegressionRunSummary(
                status="passed",
                exit_code=0,
                duration_seconds=0.2,
                passed=1,
                failed=0,
                errors=0,
                skipped=0,
            ),
        ).model_dump()
    )
    scenario = f"""
    const inconclusivePayload = {inconclusive_json};
    setFetchHandler(async (url) => {{
      if (url.includes("/diff")) {{
        return {{ ok: true, status: 200, async json() {{ return {{ files: [], omitted_files: 0, base_sha: "abc123456789" }}; }} }};
      }}
      if (url.includes("validate-regression")) {{
        return {{
          ok: true,
          status: 200,
          async json() {{
            return inconclusivePayload;
          }},
        }};
      }}
      if (url.includes("/remove")) {{
        return {{ ok: true, status: 200, async json() {{ return {{ status: "removed" }}; }} }};
      }}
      return {{ ok: true, status: 200, async json() {{ return {{}}; }} }};
    }});

    renderCodingJob({{
      job_id: "job-a",
      session_id: "sess-a",
      state: "unverified",
      answer: "Job A",
      steps: 1,
      tools_used: 1,
      sandbox: "docker",
      image_id: "sha256:aaaa",
      repository: "/tmp/repo",
      workspace_path: "/tmp/wt-a",
      branch: "selfforge/sess-a",
      base_sha: "abc123def456",
      can_resume: true,
      trace: [],
      verification_passed: false,
    }});
    $("coding-regression-test-file").value = "tests/test_a.py";
    $("coding-regression-timeout").value = "30";
    await $("coding-regression-form").dispatchEventAsync({{ type: "submit" }});
    const shownForA = !$("coding-regression-result").hidden;
    const inconclusiveTextForA = $("coding-regression-result").textContent;

    // Switching to Job B clears stale status/result from Job A
    renderCodingJob({{
      job_id: "job-b",
      session_id: "sess-b",
      state: "unverified",
      answer: "Job B",
      steps: 1,
      tools_used: 1,
      sandbox: "docker",
      image_id: "sha256:bbbb",
      repository: "/tmp/repo",
      workspace_path: "/tmp/wt-b",
      branch: "selfforge/sess-b",
      base_sha: "def456abc123",
      can_resume: true,
      trace: [],
      verification_passed: false,
    }});
    const clearedOnSwitch = $("coding-regression-result").hidden && $("coding-regression-status").hidden;

    // Removing workspace hides regression panel cleanly
    await $("coding-remove-workspace").dispatchEventAsync({{ type: "click" }});
    const hiddenAfterRemove = $("coding-regression").hidden;

    return {{ shownForA, inconclusiveTextForA, clearedOnSwitch, hiddenAfterRemove }};
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["shownForA"] is True
    assert "未确认" in str(outcome["inconclusiveTextForA"])
    assert "both_passed" in str(outcome["inconclusiveTextForA"])
    assert outcome["clearedOnSwitch"] is True
    assert outcome["hiddenAfterRemove"] is True


def test_web_ui_revert_invalidates_stale_report_and_unrecorded_sandbox_disables_submit() -> None:
    scenario = """
    setFetchHandler(async (url) => {
      if (url.includes("/diff")) {
        return { ok: true, status: 200, async json() { return { files: [], omitted_files: 0, base_sha: "abc123456789" }; } };
      }
      if (url.includes("validate-regression")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              status: "confirmed",
              reason: "base_failed_repaired_passed",
              session_id: "sess-rev",
              base_sha: "abc123",
              workspace_sha256: "1".repeat(64),
              test_sha256: "2".repeat(64),
              base_result: { status: "failed", exit_code: 1, duration_seconds: 0.2, passed: 0, failed: 1, errors: 0, skipped: 0 },
              repaired_result: { status: "passed", exit_code: 0, duration_seconds: 0.2, passed: 1, failed: 0, errors: 0, skipped: 0 },
            };
          },
        };
      }
      if (url.includes("/revert")) {
        return { ok: true, status: 200, async json() { return { reverted_files: ["calculator.py"] }; } };
      }
      return { ok: true, status: 200, async json() { return {}; } };
    });

    // 1) Null sandbox disables regression validation safely
    renderCodingJob({
      job_id: "job-nosandbox",
      session_id: "sess-nosandbox",
      state: "unverified",
      answer: "Done",
      steps: 1,
      tools_used: 1,
      sandbox: null,
      repository: "/tmp/repo",
      workspace_path: "/tmp/wt-ns",
      branch: "selfforge/sess-ns",
      base_sha: "abc123",
      can_resume: true,
      trace: [],
      verification_passed: false,
    });
    const nullSandboxDisabled = $("coding-regression-btn").disabled;

    // 2) Docker job runs validation then workspace revert clears stale result
    renderCodingJob({
      job_id: "job-rev",
      session_id: "sess-rev",
      state: "unverified",
      answer: "Done",
      steps: 1,
      tools_used: 1,
      sandbox: "docker",
      image_id: "sha256:abcd",
      repository: "/tmp/repo",
      workspace_path: "/tmp/wt-rev",
      branch: "selfforge/sess-rev",
      base_sha: "abc123",
      can_resume: true,
      trace: [],
      verification_passed: false,
    });
    $("coding-regression-test-file").value = "tests/test_rev.py";
    $("coding-regression-timeout").value = "30";
    await $("coding-regression-form").dispatchEventAsync({ type: "submit" });
    const shownBeforeRevert = !$("coding-regression-result").hidden;

    await $("coding-revert-workspace").dispatchEventAsync({ type: "click" });
    return {
      nullSandboxDisabled,
      shownBeforeRevert,
      resultHiddenAfterRevert: $("coding-regression-result").hidden,
      statusAfterRevert: $("coding-regression-status").textContent,
    };
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["nullSandboxDisabled"] is True
    assert outcome["shownBeforeRevert"] is True
    assert outcome["resultHiddenAfterRevert"] is True
    assert "还原" in str(outcome["statusAfterRevert"])


def test_web_ui_roundtrips_fastapi_validate_regression_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    report = RegressionValidationReport(
        status="error",
        reason="timeout",
        session_id="sessapi01",
        base_sha="9999888877776666",
        workspace_sha256="c" * 64,
        test_sha256="d" * 64,
        base_result=RegressionRunSummary(
            status="timeout",
            exit_code=None,
            duration_seconds=5.0,
            passed=0,
            failed=0,
            errors=0,
            skipped=0,
        ),
        repaired_result=None,
    )
    captured_calls: list[tuple[Path, str, str, float]] = []

    def fake_validate(
        repo: Path,
        session_id: str,
        test_file: str,
        *,
        timeout: float,
        workspace_manager: object,
        legacy_session_roots: tuple[Path, ...],
    ) -> RegressionValidationReport:
        captured_calls.append((repo, session_id, test_file, timeout))
        return report

    monkeypatch.setattr(api_module, "validate_workspace_regression", fake_validate)
    client = TestClient(api_module.app)
    api_res = client.post(
        "/api/code/workspaces/sessapi01/validate-regression",
        json={"repository": str(tmp_path), "test_file": "tests/test_reg.py", "timeout": 5},
    )
    assert api_res.status_code == 200
    assert captured_calls == [(tmp_path, "sessapi01", "tests/test_reg.py", 5.0)]
    payload_json = json.dumps(api_res.json())

    scenario = f"""
    const serverPayload = {payload_json};
    setFetchHandler(async (url) => {{
      if (url.includes("/diff")) {{
        return {{ ok: true, status: 200, async json() {{ return {{ files: [], omitted_files: 0, base_sha: "999988887777" }}; }} }};
      }}
      if (url.includes("validate-regression")) {{
        return {{ ok: true, status: 200, async json() {{ return serverPayload; }} }};
      }}
      return {{ ok: true, status: 200, async json() {{ return {{}}; }} }};
    }});
    renderCodingJob({{
      job_id: "job-api",
      session_id: "sessapi01",
      state: "unverified",
      answer: "Done",
      steps: 1,
      tools_used: 1,
      sandbox: "docker",
      image_id: "sha256:9999",
      repository: {json.dumps(str(tmp_path))},
      workspace_path: {json.dumps(str(tmp_path / "wt"))},
      branch: "selfforge/sessapi01",
      base_sha: "999988887777",
      can_resume: true,
      trace: [],
      verification_passed: false,
    }});
    $("coding-regression-test-file").value = "tests/test_reg.py";
    $("coding-regression-timeout").value = "5";
    await $("coding-regression-form").dispatchEventAsync({{ type: "submit" }});
    return {{
      resultHidden: $("coding-regression-result").hidden,
      resultText: $("coding-regression-result").textContent,
      statusText: $("coding-regression-status").textContent,
    }};
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["resultHidden"] is False
    assert "timeout" in str(outcome["resultText"])
    assert "未运行" in str(outcome["resultText"])
    assert "999988887777" in str(outcome["resultText"])



@pytest.mark.parametrize('invalidate', ['switch_back', 'revert', 'remove', 'start_job'])
@pytest.mark.parametrize('reject', [False, True])
def test_regression_responses_cannot_survive_invalidation(invalidate: str, reject: bool) -> None:
    scenario = r'''
    const job = (id) => ({job_id:id, session_id:id, repository:'/tmp/repo',
      workspace_path:'/tmp/wt', branch:'selfforge/'+id, base_sha:'abc123',
      sandbox:'docker', state:'unverified', answer:'Done', steps:1, tools_used:1,
      trace:[], can_resume:true, verification_passed:false});
    let finish;
    setFetchHandler(async (url) => {
      if (url.includes('validate-regression')) return new Promise(resolve => { finish=resolve; });
      if (url.includes('/revert')) return {ok:true, json:async()=>({reverted_files:[]})};
      if (url === '/api/code/jobs') return {ok:false, json:async()=>({detail:'job rejected'})};
      return {ok:true, json:async()=>({files:[], omitted_files:0})};
    });
    renderCodingJob(job('a'));
    $('coding-regression-test-file').value='tests/test_fix.py';
    $('coding-regression-timeout').value='30';
    const pending=$('coding-regression-form').dispatchEventAsync({type:'submit'});
    await flushMicrotasks();
    const action=__ACTION__;
    if(action === 'switch_back') {renderCodingJob(job('b'));renderCodingJob(job('a'));}
    if(action === 'revert') await $('coding-revert-workspace').dispatchEventAsync({type:'click'});
    if(action === 'remove') await $('coding-remove-workspace').dispatchEventAsync({type:'click'});
    if(action === 'start_job') {
      $('coding-task-text').value='new change';
      await $('coding-form').dispatchEventAsync({type:'submit'});
    }
    const before=$('coding-regression-status').textContent;
    finish({ok:!__REJECT__, json:async()=> __REJECT__ ? {detail:'STALE_ERROR'} : ({
      status:'confirmed', reason:'base_failed_repaired_passed', session_id:'a', base_sha:'abc123',
      workspace_sha256:'a'.repeat(64), test_sha256:'b'.repeat(64), base_result:null,repaired_result:null})});
    await pending;
    return {hidden:$('coding-regression-result').hidden,
      statusUnchanged:before === $('coding-regression-status').textContent};
    '''
    scenario = scenario.replace('__ACTION__', json.dumps(invalidate)).replace('__REJECT__', json.dumps(reject))
    outcome = _run_app_js_scenario(scenario)
    assert outcome['hidden'] is True
    assert outcome['statusUnchanged'] is True


def test_web_ui_restores_persisted_regression_report_on_job_render() -> None:
    scenario = r"""
    const savedReport = {
      status: "confirmed",
      reason: "base_failed_repaired_passed",
      session_id: "sess_durable",
      base_sha: "1111222233334444",
      workspace_sha256: "a".repeat(64),
      test_sha256: "b".repeat(64),
      base_result: {
        status: "failed",
        exit_code: 1,
        duration_seconds: 12.5,
        passed: 0,
        failed: 1,
        errors: 0,
        skipped: 0,
      },
      repaired_result: {
        status: "passed",
        exit_code: 0,
        duration_seconds: 10.2,
        passed: 1,
        failed: 0,
        errors: 0,
        skipped: 0,
      },
    };

    let requestedUrl = null;
    setFetchHandler(async (url) => {
      if (url.includes("/regression?")) {
        requestedUrl = url;
        return {
          ok: true,
          status: 200,
          async json() { return savedReport; },
        };
      }
      return { ok: true, status: 200, async json() { return { files: [], omitted_files: 0 }; } };
    });

    renderCodingJob({
      job_id: "job-durable",
      session_id: "sess_durable",
      repository: "/repo/demo",
      workspace_path: "/repo/demo/wt",
      branch: "selfforge/sess_durable",
      base_sha: "1111222233334444",
      sandbox: "docker",
      state: "completed",
      answer: "Done fix",
      steps: 3,
      tools_used: 2,
      can_resume: true,
      verification_passed: true,
      trace: [],
    });

    await flushMicrotasks();

    return {
      requestedUrl,
      resultHidden: $("coding-regression-result").hidden,
      resultText: $("coding-regression-result").textContent,
      statusHidden: $("coding-regression-status").hidden,
      statusText: $("coding-regression-status").textContent,
    };
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["requestedUrl"] is not None
    assert "/api/code/workspaces/sess_durable/regression" in str(outcome["requestedUrl"])
    assert outcome["resultHidden"] is False
    assert "已确认修复" in str(outcome["resultText"]) or "confirmed" in str(outcome["resultText"])
    assert "已恢复" in str(outcome["statusText"])


def test_web_ui_durable_regression_404_resets_panel_cleanly() -> None:
    scenario = r"""
    let requestedUrl = null;
    setFetchHandler(async (url) => {
      if (url.includes("/regression?")) {
        requestedUrl = url;
        return {
          ok: false,
          status: 404,
          async json() { return { detail: "No regression validation record found" }; },
        };
      }
      return { ok: true, status: 200, async json() { return { files: [], omitted_files: 0 }; } };
    });

    renderCodingJob({
      job_id: "job-norec",
      session_id: "sess_norec",
      repository: "/repo/demo",
      workspace_path: "/repo/demo/wt",
      branch: "selfforge/sess_norec",
      base_sha: "1111222233334444",
      sandbox: "docker",
      state: "unverified",
      answer: "Done fix",
      steps: 1,
      tools_used: 1,
      can_resume: true,
      verification_passed: false,
      trace: [],
    });

    await flushMicrotasks();

    return {
      requestedUrl,
      resultHidden: $("coding-regression-result").hidden,
      statusHidden: $("coding-regression-status").hidden,
    };
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["requestedUrl"] is not None
    assert outcome["resultHidden"] is True


def test_web_ui_direct_mode_skips_durable_regression_query_and_commit_invalidates() -> None:
    scenario = r"""
    const calls = [];
    setFetchHandler(async (url) => {
      calls.push(url);
      if (url.includes("/regression?")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              status: "confirmed",
              reason: "base_failed_repaired_passed",
              session_id: "sess_com",
              base_sha: "1111222233334444",
              workspace_sha256: "a".repeat(64),
              test_sha256: "b".repeat(64),
              base_result: null,
              repaired_result: null,
            };
          },
        };
      }
      if (url.includes("/commit")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              session_id: "sess_com",
              branch: "selfforge/sess_com",
              commit_sha: "9999888877776666",
              message: "test commit",
              files_committed: ["app.py"],
            };
          },
        };
      }
      return { ok: true, status: 200, async json() { return { files: [], omitted_files: 0 }; } };
    });

    // 1. Direct mode has branch: null -> should NOT call /regression
    renderCodingJob({
      job_id: "job-direct",
      session_id: "sess_direct",
      repository: "/repo/demo",
      workspace_path: "/repo/demo",
      branch: null,
      base_sha: "1111222233334444",
      sandbox: "docker",
      state: "completed",
      answer: "Done direct",
      steps: 1,
      tools_used: 1,
      can_resume: false,
      verification_passed: true,
      trace: [],
    });
    await flushMicrotasks();
    const callsAfterDirect = calls.filter((c) => c.includes("/regression?")).length;

    // 2. Worktree mode with branch -> restores report
    renderCodingJob({
      job_id: "job-com",
      session_id: "sess_com",
      repository: "/repo/demo",
      workspace_path: "/repo/demo/wt",
      branch: "selfforge/sess_com",
      base_sha: "1111222233334444",
      sandbox: "docker",
      state: "completed",
      answer: "Done com",
      steps: 1,
      tools_used: 1,
      can_resume: true,
      verification_passed: true,
      trace: [],
    });
    await flushMicrotasks();
    const resultVisibleBeforeCommit = !$("coding-regression-result").hidden;

    // 3. Submitting commit resets regression panel
    $("coding-commit-msg").value = "Commit changes";
    await $("coding-commit-form").dispatchEventAsync({ type: "submit" });
    const resultHiddenAfterCommit = $("coding-regression-result").hidden;

    return {
      callsAfterDirect,
      resultVisibleBeforeCommit,
      resultHiddenAfterCommit,
    };
    """
    outcome = _run_app_js_scenario(scenario)
    assert outcome["callsAfterDirect"] == 0
    assert outcome["resultVisibleBeforeCommit"] is True
    assert outcome["resultHiddenAfterCommit"] is True
