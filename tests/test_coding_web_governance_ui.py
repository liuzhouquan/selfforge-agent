"""UI and API contract tests for SF-WS-06 workspace governance and sandbox freshness UI."""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selfforge_agent.api import app as api_module
from selfforge_agent.runtime.workspace import CodingWorkspaceManager

STATIC_DIR = Path(__file__).resolve().parents[1] / "src" / "selfforge_agent" / "api" / "static"
INDEX_HTML = STATIC_DIR / "index.html"
APP_JS = STATIC_DIR / "app.js"
STYLE_CSS = STATIC_DIR / "style.css"

_VOID_TAGS = frozenset({"input", "br", "hr", "img", "meta", "link"})


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
        if tag.lower() not in _VOID_TAGS:
            self.stack.append(elem_id)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() not in _VOID_TAGS and self.stack:
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
  async clickAsync() {
    return this.dispatchEventAsync({ type: "click" });
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
  return { ok: false, status: 404, async json() { return { detail: `unmocked ${url}` }; } };
};

const context = {
  console,
  URLSearchParams,
  localStorage: makeStorage(),
  sessionStorage: makeStorage(),
  document,
  setTimeout: () => 0,
  clearTimeout: () => {},
  fetch: async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return fetchHandler(url, options);
  },
  setFetchHandler(fn) {
    fetchHandler = fn;
  },
  fetchCalls,
  assert(cond, msg) {
    if (!cond) throw new Error(msg || "Assertion failed");
  },
  flushMicrotasks: async () => {
    for (let i = 0; i < 12; i += 1) {
      await Promise.resolve();
    }
  },
};
context.globalThis = context;
context.window = context;

vm.createContext(context);
const appSource = fs.readFileSync(APP_JS_PATH, "utf-8");
vm.runInContext(appSource, context, { filename: "app.js" });

(async () => {
  try {
    await context.flushMicrotasks();
    const scenarioFn = new vm.Script(`(async () => {\n${__SCENARIO__}\n})()`).runInContext(context);
    const result = await scenarioFn;
    process.stdout.write(JSON.stringify({ ok: true, result: result ?? null }));
  } catch (err) {
    process.stderr.write(String(err && err.stack ? err.stack : err));
    process.exit(1);
  }
})();
"""
    script = (
        harness.replace("__INVENTORY__", json.dumps(inventory.elements))
        .replace("__APP_JS_PATH__", json.dumps(str(APP_JS)))
        .replace("__SCENARIO__", json.dumps(scenario_js))
    )
    completed = subprocess.run(
        [node_bin, "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, f"Node harness failed:\n{completed.stderr}"
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    return payload.get("result") or {}


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return res.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["requests>=2.31.0"]\n',
        encoding="utf-8",
    )
    (repo / "requirements.lock").write_text(
        "pytest==8.2.0 --hash=sha256:" + "0" * 64 + "\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial commit")
    return repo


def test_coding_workbench_html_contains_governance_and_freshness_panels() -> None:
    inventory = _parse_index_inventory()
    required_ids = (
        "coding-governance",
        "coding-ws-governance",
        "coding-ws-head-ref",
        "coding-ws-quota",
        "coding-ws-audit-btn",
        "coding-ws-storage-btn",
        "coding-ws-prune-dry-btn",
        "coding-ws-prune-merged",
        "coding-ws-prune-confirm",
        "coding-ws-prune-exec-btn",
        "coding-ws-status",
        "coding-ws-result",
        "coding-ws-prune-result",
        "coding-freshness-panel",
        "coding-freshness-lock",
        "coding-freshness-req",
        "coding-freshness-btn",
        "coding-freshness-status",
        "coding-freshness-result",
    )
    for elem_id in required_ids:
        assert elem_id in inventory.ancestors_by_id, f"Missing element #{elem_id}"
        assert "coding-task" in inventory.ancestors_by_id[elem_id], (
            f"Element #{elem_id} must live inside #coding-task"
        )

    by_id = {entry["id"]: entry for entry in inventory.elements if entry["id"]}
    assert "checked" in by_id["coding-ws-prune-merged"]["attrs"]
    assert "checked" not in by_id["coding-ws-prune-confirm"]["attrs"]
    assert "disabled" in by_id["coding-ws-prune-exec-btn"]["attrs"]
    assert "text/javascript" not in INDEX_HTML.read_text(encoding="utf-8").lower()


def test_web_ui_workspace_audit_and_storage_render_and_quota_guards() -> None:
    scenario = r"""
    // Empty repo starts with disabled governance controls and blocks direct clicks
    assert($("coding-ws-audit-btn").disabled === true, "audit button must be disabled without repo");
    assert($("coding-ws-storage-btn").disabled === true, "storage button must be disabled without repo");
    assert($("coding-ws-prune-dry-btn").disabled === true, "dry run button must be disabled without repo");
    assert($("coding-freshness-btn").disabled === true, "freshness button must be disabled without repo");

    const beforeEmptyCalls = fetchCalls.length;
    await $("coding-ws-audit-btn").clickAsync();
    assert(fetchCalls.length === beforeEmptyCalls, "empty repo must not call audit endpoint");

    // Enter valid repo
    $("coding-repo").value = "/tmp/demo-repo";
    await $("coding-repo").dispatchEventAsync({ type: "input" });
    assert($("coding-ws-audit-btn").disabled === false, "audit button enabled once repo entered");

    // Invalid quota values must fail closed BEFORE issuing any fetch
    for (const badQuota of ["-1", "12.5", "10mb", "99999999999999999999"]) {
      $("coding-ws-quota").value = badQuota;
      const callsBefore = fetchCalls.length;
      await $("coding-ws-storage-btn").clickAsync();
      assert(fetchCalls.length === callsBefore, `invalid quota ${badQuota} must not fetch`);
      assert($("coding-ws-status").hidden === false, "quota error status must be visible");
      assert($("coding-ws-result").hidden === true, "stale storage result must stay hidden");
    }

    // Valid audit with XSS payload and storage with incomplete scan + zero quota
    $("coding-ws-head-ref").value = "main";
    $("coding-ws-quota").value = "0";
    setFetchHandler(async (url, _opts) => {
      if (String(url).startsWith("/api/code/workspaces/audit?")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return [
              {
                session_id: "sess-<img src=x onerror=alert(1)>",
                path: "/tmp/wt-1",
                branch: "selfforge/<script>xss</script>",
                base_sha: "a".repeat(40),
                is_locked: false,
                is_dirty: false,
                is_merged: true,
                prunable: true,
              },
            ];
          },
        };
      }
      if (String(url).startsWith("/api/code/workspaces/storage?")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              repository: "/tmp/demo-repo",
              workspace_count: 1,
              total_bytes: 2048,
              total_cache_bytes: 512,
              reclaimable_bytes: 0,
              quota_bytes: 0,
              quota_exceeded: null,
              complete: false,
              measurement_scope: "Apparent bytes, partial scan",
              recommendations: ["Incomplete scan: byte counts are lower bounds; quota may be unknown."],
              workspaces: [
                {
                  session_id: "sess-partial",
                  path: "/tmp/wt-partial",
                  branch: "selfforge/sess-partial",
                  base_sha: "b".repeat(40),
                  complete: false,
                  scan_error: "PermissionError: /tmp/wt-partial/secret",
                  total_bytes: 2048,
                  cache_bytes: 512,
                  file_count: 14,
                  is_locked: true,
                  is_dirty: false,
                  is_merged: true,
                  prunable: false,
                  reclaimable_bytes: 0,
                },
              ],
            };
          },
        };
      }
      return { ok: false, status: 400, async json() { return { detail: "Not a git repo" }; } };
    });

    await $("coding-ws-audit-btn").clickAsync();
    const auditReq = fetchCalls.find((c) => String(c.url).startsWith("/api/code/workspaces/audit?"));
    assert(Boolean(auditReq), "audit endpoint must be called");
    assert(String(auditReq.url).includes("head_ref=main"), "head_ref query param forwarded");
    assert($("coding-ws-result").hidden === false, "audit result must render");
    assert($("coding-ws-result").textContent.includes("<script>xss</script>"), "XSS payload rendered literally");

    await $("coding-ws-storage-btn").clickAsync();
    const storageReq = fetchCalls.find((c) => String(c.url).startsWith("/api/code/workspaces/storage?"));
    assert(Boolean(storageReq), "storage endpoint must be called");
    assert(String(storageReq.url).includes("quota_bytes=0"), "zero quota_bytes must be preserved");
    const storageText = $("coding-ws-result").textContent;
    assert(storageText.includes("PermissionError"), "scan_error must be shown");
    assert(storageText.includes("未完整") || storageText.includes("Incomplete"), "incomplete scan warning shown");
    assert(storageText.includes("未知"), "null quota_exceeded shown as unknown, not false");

    // HTTP 400 error surfacing
    setFetchHandler(async () => ({
      ok: false,
      status: 400,
      async json() { return { detail: "Repository has no commits" }; },
    }));
    await $("coding-ws-audit-btn").clickAsync();
    assert($("coding-ws-status").textContent.includes("Repository has no commits"), "HTTP error rendered");
    assert($("coding-ws-result").hidden === true, "failed request hides stale result");
    """
    _run_app_js_scenario(scenario)


def test_web_ui_workspace_prune_dry_run_confirmation_and_session_sync() -> None:
    scenario = r"""
    $("coding-repo").value = "/tmp/demo-repo";
    await $("coding-repo").dispatchEventAsync({ type: "input" });

    assert($("coding-ws-prune-merged").checked === true, "merged_only must default checked");
    assert($("coding-ws-prune-confirm").checked === false, "prune confirm must start unchecked");
    assert($("coding-ws-prune-exec-btn").disabled === true, "prune execute must be disabled without confirm");

    // Unconfirmed prune execute click must never fetch
    const beforeBlocked = fetchCalls.length;
    await $("coding-ws-prune-exec-btn").clickAsync();
    assert(fetchCalls.length === beforeBlocked, "unconfirmed prune execute must not call API");

    // Load a completed coding job bound to active worktree sess-active
    setFetchHandler(async (url, opts) => {
      if (String(url).startsWith("/api/code/jobs/job-active")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              job_id: "job-active",
              state: "completed",
              repository: "/tmp/demo-repo",
              session_id: "sess-active",
              provider: "deepseek",
              model: "deepseek-chat",
              branch: "selfforge/sess-active",
              base_sha: "a".repeat(40),
              workspace_path: "/tmp/wt-active",
              sandbox: "docker",
              verification_passed: true,
              answer: "done",
              trace: [],
              token_metrics: {},
            };
          },
        };
      }
      if (String(url).includes("/diff")) {
        return { ok: true, status: 200, async json() { return { files: [], omitted_files: 0 }; } };
      }
      if (url === "/api/code/workspaces/prune") {
        const body = JSON.parse(opts.body);
        if (body.dry_run) {
          return {
            ok: true,
            status: 200,
            async json() {
              return {
                pruned: ["sess-active", "sess-old"],
                skipped_locked: ["sess-locked"],
                skipped_dirty: ["sess-dirty"],
                skipped_unmerged: ["sess-unmerged"],
              };
            },
          };
        }
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              pruned: ["sess-active"],
              skipped_locked: ["sess-locked"],
              skipped_dirty: [],
              skipped_unmerged: ["sess-unmerged"],
            };
          },
        };
      }
      if (String(url).startsWith("/api/code/workspaces/storage?")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              repository: "/tmp/demo-repo",
              workspace_count: 1,
              total_bytes: 1024,
              total_cache_bytes: 0,
              reclaimable_bytes: 0,
              quota_bytes: null,
              quota_exceeded: false,
              complete: true,
              measurement_scope: "Apparent bytes",
              recommendations: [],
              workspaces: [],
            };
          },
        };
      }
      return { ok: true, status: 200, async json() { return {}; } };
    });

    await pollCodingJob("job-active");
    assert($("coding-delivery").hidden === false, "active worktree shows delivery");
    assert($("coding-regression-btn").disabled === false, "regression enabled before prune");

    // Dry run preview leaves active session intact
    await $("coding-ws-prune-dry-btn").clickAsync();
    const dryCall = fetchCalls.find((c) => c.url === "/api/code/workspaces/prune");
    assert(JSON.parse(dryCall.options.body).dry_run === true, "dry run sends dry_run=true");
    assert($("coding-ws-prune-result").hidden === false, "dry run renders prune preview");
    assert($("coding-ws-prune-result").textContent.includes("sess-locked"), "dry run lists skipped_locked");
    assert($("coding-delivery").hidden === false, "dry run must not disable active worktree delivery");

    // Confirm checkbox enables execute; executing prune resets confirm & syncs active session
    $("coding-ws-prune-confirm").checked = true;
    await $("coding-ws-prune-confirm").dispatchEventAsync({ type: "change" });
    assert($("coding-ws-prune-exec-btn").disabled === false, "prune execute enabled once confirmed");

    await $("coding-ws-prune-exec-btn").clickAsync();
    assert($("coding-ws-prune-confirm").checked === false, "confirm checkbox must reset after prune");
    assert($("coding-ws-prune-exec-btn").disabled === true, "execute button disables after prune");
    assert($("coding-delivery").hidden === true, "pruning active session hides delivery panel");
    assert($("coding-remove-workspace").hidden === true, "pruning active session hides remove button");
    assert($("coding-regression-btn").disabled === true, "pruning active session disables regression");
    assert($("coding-ws-prune-result").hidden === false, "prune result stays visible after storage refresh");
    """
    _run_app_js_scenario(scenario)


def test_web_ui_freshness_diagnostics_and_repo_switch_race_invalidation() -> None:
    scenario = r"""
    $("coding-repo").value = "/tmp/repo-a";
    await $("coding-repo").dispatchEventAsync({ type: "input" });
    $("coding-freshness-lock").value = "requirements.lock.txt";
    $("coding-freshness-req").value = "pyproject.toml";

    let resolveSlowAudit;
    let resolveSlowFreshness;
    setFetchHandler(async (url, opts) => {
      if (String(url).startsWith("/api/code/workspaces/audit?") && url.includes("repo-a") && !resolveSlowAudit) {
        return new Promise((resolve) => { resolveSlowAudit = resolve; });
      }
      if (url === "/api/sandbox/freshness") {
        const body = JSON.parse(opts.body);
        if (body.repository === "/tmp/repo-a" && !resolveSlowFreshness) {
          return new Promise((resolve) => { resolveSlowFreshness = resolve; });
        }
        return {
          ok: true,
          status: 200,
          async json() {
            return {
              version: 1,
              fresh: false,
              status: "missing_dependencies",
              lock_file: "/tmp/repo-a/requirements.lock.txt",
              declaration_file: "/tmp/repo-a/pyproject.toml",
              declared_count: 2,
              locked_count: 1,
              missing_packages: [
                { name: "httpx<img src=x onerror=alert(2)>", specifier: ">=0.27", source_file: "pyproject.toml" },
              ],
              incompatible_packages: [
                { name: "pydantic", declared_specifier: ">=2.10", locked_version: "2.5.0", source_file: "pyproject.toml" },
              ],
              is_stale_by_mtime: true,
              warnings: ["Declared dependencies missing from lockfile: httpx"],
            };
          },
        };
      }
      if (String(url).startsWith("/api/code/workspaces/audit?")) {
        return {
          ok: true,
          status: 200,
          async json() {
            return [{
              session_id: "fresh-gen2",
              path: "/tmp/wt-gen2",
              branch: "selfforge/gen2",
              base_sha: "c".repeat(40),
              is_locked: false,
              is_dirty: false,
              is_merged: false,
              prunable: false,
            }];
          },
        };
      }
      return { ok: true, status: 200, async json() { return {}; } };
    });

    // Launch generation-1 requests on repo-a (held open)
    const pAudit1 = $("coding-ws-audit-btn").clickAsync();
    const pFresh1 = $("coding-freshness-btn").clickAsync();
    await flushMicrotasks();

    // Switch repo A -> B -> A while gen-1 is in flight
    $("coding-repo").value = "/tmp/repo-b";
    await $("coding-repo").dispatchEventAsync({ type: "input" });
    $("coding-repo").value = "/tmp/repo-a";
    await $("coding-repo").dispatchEventAsync({ type: "input" });

    // Launch generation-2 requests on repo-a and finish them first
    await $("coding-ws-audit-btn").clickAsync();
    await $("coding-freshness-btn").clickAsync();

    // Now resolve stale generation-1 requests with poisoned data
    resolveSlowAudit({
      ok: true,
      status: 200,
      async json() {
        return [{ session_id: "STALE-GEN1", path: "/tmp/stale", branch: "stale", base_sha: "0".repeat(40), is_locked: false, is_dirty: false, is_merged: false, prunable: false }];
      },
    });
    resolveSlowFreshness({
      ok: true,
      status: 200,
      async json() {
        return {
          version: 1,
          fresh: true,
          status: "fresh",
          lock_file: "/tmp/stale/requirements.lock.txt",
          declaration_file: "/tmp/stale/pyproject.toml",
          declared_count: 1,
          locked_count: 1,
          missing_packages: [],
          incompatible_packages: [],
          is_stale_by_mtime: false,
          warnings: ["STALE-GEN1-WARNING"],
        };
      },
    });
    await pAudit1;
    await pFresh1;

    assert($("coding-ws-result").textContent.includes("fresh-gen2"), "new audit response must win");
    assert(!$("coding-ws-result").textContent.includes("STALE-GEN1"), "stale audit response must be dropped");
    const freshText = $("coding-freshness-result").textContent;
    assert(freshText.includes("missing_dependencies"), "freshness status rendered");
    assert(freshText.includes("httpx<img src=x onerror=alert(2)>"), "XSS package name rendered safely");
    assert(freshText.includes("pydantic"), "incompatible package rendered");
    assert(!freshText.includes("STALE-GEN1-WARNING"), "stale freshness response must be dropped");

    // Changing repo input clears stale panels & disables when empty
    $("coding-repo").value = "";
    await $("coding-repo").dispatchEventAsync({ type: "input" });
    assert($("coding-ws-result").hidden === true, "clearing repo hides workspace result");
    assert($("coding-freshness-result").hidden === true, "clearing repo hides freshness result");
    assert($("coding-freshness-btn").disabled === true, "clearing repo disables freshness button");
    """
    _run_app_js_scenario(scenario)


def test_web_ui_governance_and_freshness_roundtrip_real_fastapi_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _init_repo(tmp_path)
    workspaces_root = tmp_path / "workspaces"
    locks_root = tmp_path / "locks"
    monkeypatch.setenv("SELFFORGE_LOCKS_ROOT", str(locks_root))
    manager = CodingWorkspaceManager(root=workspaces_root)
    monkeypatch.setattr(api_module, "_CODING_WORKSPACES", manager)

    # Create one clean merged worktree and one dirty unmerged worktree
    ws_merged = manager.create(repo, "sessMerged1")
    ws_dirty = manager.create(repo, "sessDirty2")
    (ws_dirty.path / "untracked.py").write_text("x = 1\n", encoding="utf-8")

    with TestClient(api_module.app) as client:
        index_resp = client.get("/")
        assert index_resp.status_code == 200
        assert "coding-governance" in index_resp.text
        assert "coding-freshness-panel" in index_resp.text

        audit_resp = client.get(
            "/api/code/workspaces/audit",
            params={"repository": str(repo), "head_ref": "HEAD"},
        )
        assert audit_resp.status_code == 200
        audit_payload = audit_resp.json()

        storage_resp = client.get(
            "/api/code/workspaces/storage",
            params={"repository": str(repo), "head_ref": "HEAD", "quota_bytes": 1000000},
        )
        assert storage_resp.status_code == 200
        storage_payload = storage_resp.json()

        dry_prune_resp = client.post(
            "/api/code/workspaces/prune",
            json={"repository": str(repo), "dry_run": True, "merged_only": True, "head_ref": "HEAD"},
        )
        assert dry_prune_resp.status_code == 200
        dry_prune_payload = dry_prune_resp.json()
        assert dry_prune_payload["pruned"] == ["sessMerged1"]
        assert dry_prune_payload["skipped_dirty"] == ["sessDirty2"]

        freshness_resp = client.post(
            "/api/sandbox/freshness",
            json={"repository": str(repo)},
        )
        assert freshness_resp.status_code == 200
        freshness_payload = freshness_resp.json()
        assert freshness_payload["status"] == "missing_dependencies"

    assert ws_merged.path.exists()

    # Feed exact live FastAPI responses into app.js DOM runtime to verify round-trip schema parity
    scenario = f"""
    const AUDIT_PAYLOAD = {json.dumps(audit_payload)};
    const STORAGE_PAYLOAD = {json.dumps(storage_payload)};
    const PRUNE_PAYLOAD = {json.dumps(dry_prune_payload)};
    const FRESHNESS_PAYLOAD = {json.dumps(freshness_payload)};

    $("coding-repo").value = {json.dumps(str(repo))};
    await $("coding-repo").dispatchEventAsync({{ type: "input" }});
    $("coding-ws-quota").value = "1000000";

    setFetchHandler(async (url, _opts) => {{
      if (String(url).startsWith("/api/code/workspaces/audit?")) {{
        return {{ ok: true, status: 200, async json() {{ return AUDIT_PAYLOAD; }} }};
      }}
      if (String(url).startsWith("/api/code/workspaces/storage?")) {{
        return {{ ok: true, status: 200, async json() {{ return STORAGE_PAYLOAD; }} }};
      }}
      if (url === "/api/code/workspaces/prune") {{
        return {{ ok: true, status: 200, async json() {{ return PRUNE_PAYLOAD; }} }};
      }}
      if (url === "/api/sandbox/freshness") {{
        return {{ ok: true, status: 200, async json() {{ return FRESHNESS_PAYLOAD; }} }};
      }}
      return {{ ok: true, status: 200, async json() {{ return {{}}; }} }};
    }});

    await $("coding-ws-audit-btn").clickAsync();
    assert($("coding-ws-result").textContent.includes("sessMerged1"), "audit renders real merged session");
    assert($("coding-ws-result").textContent.includes("sessDirty2"), "audit renders real dirty session");

    await $("coding-ws-storage-btn").clickAsync();
    assert($("coding-ws-result").textContent.includes("2"), "storage renders workspace count 2");

    await $("coding-ws-prune-dry-btn").clickAsync();
    assert($("coding-ws-prune-result").textContent.includes("sessMerged1"), "dry prune renders sessMerged1");
    assert($("coding-ws-prune-result").textContent.includes("sessDirty2"), "dry prune renders skipped_dirty");

    await $("coding-freshness-btn").clickAsync();
    assert($("coding-freshness-result").textContent.includes("requests"), "freshness renders missing requests dep");
    """
    _run_app_js_scenario(scenario)
