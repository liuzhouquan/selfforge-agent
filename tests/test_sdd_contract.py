"""Regression tests for scripts/check_sdd.py (SF-DOC-01)."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_sdd.py"

SHA_A = "a" * 40
SHA_B = "b" * 40


def _run_check(root: Path | None, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(SCRIPT_PATH)]
    if root is not None:
        cmd.extend(["--root", str(root)])
    return subprocess.run(
        cmd,
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _assert_contract_failure(res: subprocess.CompletedProcess[str], *expected_substrings: str) -> None:
    assert res.returncode == 1, f"expected exit code 1, got {res.returncode}: {res.stderr}"
    lowered = res.stderr.lower()
    assert any(sub.lower() in lowered for sub in expected_substrings), (
        f"expected one of {expected_substrings!r} in stderr:\n{res.stderr}"
    )


def _task_spec(
    task_id: str,
    *,
    acc_rows: str | None = None,
    evidence_table: str = "",
    exec_record: str = "执行记录（Owner/状态以上表为准）：Base=领取时填写；Branch=无；PR=无。",
    handoff: str = "",
) -> str:
    if acc_rows is None:
        acc_rows = f"| {task_id}-A1 | given / when / then | `tests/test_sample.py` | 未验证 |"
    parts = [
        f"### {task_id}",
        "",
        "**目标：** test spec.",
        "",
        "| 验收 ID | Given / When / Then | 最少证明方式 | 初始证据 |",
        "| --- | --- | --- | --- |",
        acc_rows,
    ]
    if evidence_table:
        parts.extend(["", evidence_table])
    parts.extend(["", exec_record])
    if handoff:
        parts.extend(["", "```text", handoff, "```"])
    return "\n".join(parts)


def _write_minimal_repo(
    tmp_path: Path,
    *,
    queue_rows: str | None = None,
    specs: str | None = None,
    extra_links: str = "",
    historical_rows: str | None = None,
    test_code: str | None = None,
) -> Path:
    docs_dir = tmp_path / "docs"
    tests_dir = tmp_path / "tests"
    docs_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    (docs_dir / "guide.md").write_text("# Guide\n\n## Details\n", encoding="utf-8")
    if test_code is None:
        test_code = (
            "import pytest\n\n"
            "@pytest.mark.parametrize('val', [1, 2])\n"
            "def test_param(val):\n"
            "    assert val > 0\n\n"
            "class TestSuite:\n"
            "    def test_method(self):\n"
            "        assert True\n\n"
            "def test_ok():\n"
            "    assert True\n"
        )
    (tests_dir / "test_sample.py").write_text(test_code, encoding="utf-8")

    if queue_rows is None:
        queue_rows = "| 1 | [SF-ONE-01](#sf-one-01) | Ready | 未认领 | 无 | 交付结果 |"
    if specs is None:
        specs = _task_spec("SF-ONE-01")
    if historical_rows is None:
        historical_rows = f"| SF-OLD-01 | Merged | #12 | `{SHA_A}` |"

    sdd_content = (
        "# Demo SDD\n\n"
        "## 1. 产品目标与范围\n\n"
        f"参见 [Guide Details](./guide.md#details)。{extra_links}\n\n"
        "## 2. 开工与选择任务\n\n"
        "### 状态与文档权威\n\n"
        "状态枚举：`Ready` / `In Progress` / `In Review` / `Merged` / `Waiting` / `Needs Design`。\n\n"
        "## 3. 实际架构与必须保持的约束\n\n"
        "## 4. 下一阶段里程碑与任务队列\n\n"
        "| 顺序 | 任务 ID | 状态 | Owner | 前提 | 交付结果 |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"{queue_rows}\n\n"
        f"{specs}\n\n"
        "## 5. 验收追踪与执行记录格式\n\n"
        "| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| E1（模板，未执行） | 所属任务-A1 | `tests/实际文件.py::实际测试函数` | 完整命令 | 完整实际 SHA；passed/failed/skipped；真实 CI 链接 |\n\n"
        "| 行为 | 具体测试节点 | 已核验版本/证据 |\n"
        "| --- | --- | --- |\n"
        "| 示例行 | `tests/test_sample.py::test_ok` | 基线 SHA |\n\n"
        "## 6. 安装与验证门槛\n\n"
        "## 7. 审查、合并与交接\n\n"
        "## 8. 已合并状态快照与历史资料\n\n"
        "| 任务 ID | 状态 | PR | 合并 SHA |\n"
        "| --- | --- | --- | --- |\n"
        f"{historical_rows}\n\n"
        "## 9. 可直接复制的接手指令\n"
    )
    (docs_dir / "SDD.md").write_text(sdd_content, encoding="utf-8")
    return tmp_path


def test_current_repo_sdd_passes_and_states_consistency_only_scope() -> None:
    """SF-DOC-01-A1/A4: Current repo passes and reports structural-consistency-only scope."""
    res_explicit = _run_check(REPO_ROOT)
    assert res_explicit.returncode == 0, res_explicit.stderr
    combined = f"{res_explicit.stdout}\n{res_explicit.stderr}".lower()
    assert "does not prove test execution" in combined

    res_default = _run_check(None, cwd=REPO_ROOT)
    assert res_default.returncode == 0, res_default.stderr


def test_valid_sdd_across_statuses_tables_and_historical_snapshot_passes(tmp_path: Path) -> None:
    """SF-DOC-01-A1: All valid statuses, parameterized base test names, and Sec 8 snapshots pass."""
    queue_rows = (
        "| 1 | [SF-ONE-01](#sf-one-01) | Ready | 未认领 | 无 | ready task |\n"
        "| 2 | [SF-TWO-02](#sf-two-02) | Waiting | 未认领 | SF-ONE-01 | waiting task |\n"
        "| 3 | [SF-THR-03](#sf-thr-03) | Needs Design | 未认领 | 无 | needs design |\n"
        "| 4 | [SF-FOU-04](#sf-fou-04) | In Progress | Alice | 无 | in progress |\n"
        "| 5 | [SF-FIV-05](#sf-fiv-05) | In Review | Bob | 无 | in review |\n"
        "| 6 | [SF-SIX-06](#sf-six-06) | Merged | Carol | 无 | merged |"
    )
    specs = "\n\n".join(
        [
            _task_spec("SF-ONE-01"),
            _task_spec(
                "SF-TWO-02",
                acc_rows="| SF-TWO-02-A1 | g / w / t | `tests/test_sample.py` | 未验证；前提未满足 |",
                exec_record="执行记录：Base/Branch/PR=无。",
            ),
            _task_spec(
                "SF-THR-03",
                acc_rows="| SF-THR-03-A1 | g / w / t | `tests/test_sample.py` | 待实现 |",
            ),
            _task_spec(
                "SF-FOU-04",
                acc_rows="| SF-FOU-04-A1 | g / w / t | `tests/test_sample.py` | E1 |",
                evidence_table=(
                    "| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    f"| E1 | SF-FOU-04-A1 | `tests/test_sample.py::test_param` | `pytest tests/test_sample.py`（Host） | `{SHA_A}`；passed |"
                ),
                exec_record=f"执行记录：Base=`{SHA_A}`；Branch=`feat/four`；PR=无。",
                handoff=(
                    "Owner / 状态：Alice / In Progress\n"
                    f"实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：{SHA_A} / feat/four / {SHA_A}\n"
                    "已完成验收与证据 ID：E1\n"
                    "未完成 / 环境限制 / 未提交内容归属：无\n"
                    "PR / 准确 head CI / merge SHA（有则填）：无\n"
                    "仍在运行的本任务进程或 CI（无则写无）：无\n"
                    "下一步：打开 PR。"
                ),
            ),
            _task_spec(
                "SF-FIV-05",
                acc_rows="| SF-FIV-05-A1 | g / w / t | `tests/test_sample.py` | E1 |",
                evidence_table=(
                    "| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    f"| E1 | SF-FIV-05-A1 | `tests/test_sample.py::TestSuite::test_method` | `pytest -q` | `{SHA_A}`；passed；CI Run #1 |"
                ),
                exec_record=(
                    "| Base | 分支 | PR |\n"
                    "| --- | --- | --- |\n"
                    f"| `{SHA_A}` | `feat/five` | #5 |"
                ),
            ),
            _task_spec(
                "SF-SIX-06",
                acc_rows="| SF-SIX-06-A1 | g / w / t | `tests/test_sample.py` | E1 |",
                evidence_table=(
                    "| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |\n"
                    "| --- | --- | --- | --- | --- |\n"
                    f"| E1 | SF-SIX-06-A1 | `tests/test_sample.py::test_ok` | `pytest -q` | `{SHA_A}`；passed；CI passed |"
                ),
                exec_record=f"执行记录：Base=`{SHA_A}`；Branch=`feat/six`。",
                handoff=(
                    "Owner / 状态：Carol / Merged\n"
                    f"实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：{SHA_A} / feat/six / {SHA_A}\n"
                    "已完成验收与证据 ID：E1\n"
                    "未完成 / 环境限制 / 未提交内容归属：无\n"
                    f"PR / 准确 head CI / merge SHA（有则填）：PR #6 (https://github.com/org/repo/pull/6) / https://github.com/org/repo/actions/runs/99 / {SHA_B}\n"
                    "仍在运行的本任务进程或 CI（无则写无）：无\n"
                    "下一步：已合并。"
                ),
            ),
        ]
    )
    root = _write_minimal_repo(tmp_path, queue_rows=queue_rows, specs=specs)
    res = _run_check(root)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize(
    ("case_name", "expected_err"),
    [
        ("duplicate_queue_id", "duplicate queue task id"),
        ("invalid_status", "invalid task status"),
        ("missing_spec_section", "missing spec section"),
        ("duplicate_spec_section", "duplicate spec section"),
        ("orphan_spec_section", "orphan spec section"),
        ("wrong_queue_anchor", "queue anchor"),
        ("broken_local_file_link", "missing referenced path"),
        ("broken_local_anchor", "missing anchor"),
        ("missing_local_anchor", "missing local anchor"),
        ("disallowed_uri_scheme", "disallowed uri scheme"),
    ],
)
def test_rejects_queue_status_spec_and_link_violations(
    tmp_path: Path, case_name: str, expected_err: str
) -> None:
    """SF-DOC-01-A1: Invalid queue IDs, statuses, spec mappings, or local links fail non-zero."""
    queue_rows = "| 1 | [SF-ONE-01](#sf-one-01) | Ready | 未认领 | 无 | ok |"
    specs = _task_spec("SF-ONE-01")
    extra_links = ""

    if case_name == "duplicate_queue_id":
        queue_rows = (
            "| 1 | [SF-ONE-01](#sf-one-01) | Ready | 未认领 | 无 | one |\n"
            "| 2 | [SF-ONE-01](#sf-one-01) | Ready | 未认领 | 无 | dup |"
        )
    elif case_name == "invalid_status":
        queue_rows = "| 1 | [SF-ONE-01](#sf-one-01) | Done | 未认领 | 无 | bad status |"
    elif case_name == "missing_spec_section":
        queue_rows = (
            "| 1 | [SF-ONE-01](#sf-one-01) | Ready | 未认领 | 无 | one |\n"
            "| 2 | [SF-TWO-02](#sf-two-02) | Ready | 未认领 | 无 | missing |"
        )
    elif case_name == "duplicate_spec_section":
        specs = f"{_task_spec('SF-ONE-01')}\n\n{_task_spec('SF-ONE-01')}"
    elif case_name == "orphan_spec_section":
        specs = f"{_task_spec('SF-ONE-01')}\n\n{_task_spec('SF-EXTRA-99')}"
    elif case_name == "wrong_queue_anchor":
        queue_rows = "| 1 | [SF-ONE-01](#wrong-anchor) | Ready | 未认领 | 无 | bad anchor |"
    elif case_name == "broken_local_file_link":
        extra_links = " [Missing](./missing.md)"
    elif case_name == "broken_local_anchor":
        extra_links = " [Bad](./guide.md#nonexistent-heading)"
    elif case_name == "missing_local_anchor":
        extra_links = " [Nope](#nonexistent-section)"
    elif case_name == "disallowed_uri_scheme":
        extra_links = " [HostFile](file:///etc/passwd)"

    root = _write_minimal_repo(
        tmp_path, queue_rows=queue_rows, specs=specs, extra_links=extra_links
    )
    _assert_contract_failure(_run_check(root), expected_err)


@pytest.mark.parametrize(
    ("case_name", "expected_err"),
    [
        ("duplicate_acceptance", "duplicate acceptance id"),
        ("wrong_task_prefix", "must start with"),
        ("missing_acceptance_table", "missing acceptance table"),
    ],
)
def test_rejects_acceptance_id_or_table_violations(
    tmp_path: Path, case_name: str, expected_err: str
) -> None:
    """SF-DOC-01-A1: Duplicate, mismatched, or missing acceptance definitions fail non-zero."""
    if case_name == "duplicate_acceptance":
        acc = (
            "| SF-ONE-01-A1 | g1 | `tests/test_sample.py` | 未验证 |\n"
            "| SF-ONE-01-A1 | g2 | `tests/test_sample.py` | 未验证 |"
        )
        specs = _task_spec("SF-ONE-01", acc_rows=acc)
    elif case_name == "wrong_task_prefix":
        acc = "| SF-OTHER-99-A1 | g1 | `tests/test_sample.py` | 未验证 |"
        specs = _task_spec("SF-ONE-01", acc_rows=acc)
    else:
        specs = "### SF-ONE-01\n\n执行记录：Base=领取时填写；PR=无。"

    root = _write_minimal_repo(tmp_path, specs=specs)
    _assert_contract_failure(_run_check(root), expected_err)


@pytest.mark.parametrize(
    ("case_name", "expected_err"),
    [
        ("in_progress_unclaimed", "requires a real owner"),
        ("in_progress_placeholder_base", "40-hex base sha"),
        ("in_progress_short_base", "40-hex base sha"),
        ("in_review_unclaimed", "requires a real owner"),
        ("in_review_missing_base", "40-hex base sha"),
        ("handoff_status_mismatch", "handoff status"),
        ("merged_missing_pr", "requires a pr reference"),
        ("merged_missing_merge_sha", "40-hex merge sha"),
        ("merged_unverified_acceptance", "unverified"),
    ],
)
def test_rejects_status_metadata_and_merge_requirements(
    tmp_path: Path, case_name: str, expected_err: str
) -> None:
    """SF-DOC-01-A2: Unclaimed In Progress/Review, bad bases, or incomplete Merged fail."""
    status = "In Progress"
    owner = "Alice"
    acc = "| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | 未验证 |"
    evidence = ""
    exec_rec = f"执行记录：Base=`{SHA_A}`；Branch=`feat/one`；PR=无。"
    handoff = ""

    if case_name == "in_progress_unclaimed":
        owner = "未认领"
    elif case_name == "in_progress_placeholder_base":
        exec_rec = "执行记录：Base=领取时填写；Branch=`feat/one`；PR=无。"
    elif case_name == "in_progress_short_base":
        exec_rec = "执行记录：Base=`abc1234`；Branch=`feat/one`；PR=无。"
    elif case_name == "in_review_unclaimed":
        status = "In Review"
        owner = "未认领"
    elif case_name == "in_review_missing_base":
        status = "In Review"
        exec_rec = "执行记录：Base=无；Branch=`feat/one`；PR=#9。"
    elif case_name == "handoff_status_mismatch":
        status = "In Review"
        handoff = (
            "Owner / 状态：Alice / Ready\n"
            f"实际 base SHA / 分支及 worktree 标识 / 当前 HEAD：{SHA_A} / feat/one / {SHA_A}\n"
            "已完成验收与证据 ID：无\n"
            "未完成 / 环境限制 / 未提交内容归属：无\n"
            "PR / 准确 head CI / merge SHA（有则填）：无\n"
            "仍在运行的本任务进程或 CI（无则写无）：无\n"
            "下一步：更新状态。"
        )
    elif case_name.startswith("merged_"):
        status = "Merged"
        acc = "| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | E1 |"
        evidence = (
            "| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |\n"
            "| --- | --- | --- | --- | --- |\n"
            f"| E1 | SF-ONE-01-A1 | `tests/test_sample.py::test_ok` | `pytest -q` | `{SHA_A}`；passed；CI passed |"
        )
        if case_name == "merged_missing_pr":
            exec_rec = f"执行记录：Base=`{SHA_A}`；Branch=`feat/one`；PR=无；Merge SHA=`{SHA_B}`。"
        elif case_name == "merged_missing_merge_sha":
            exec_rec = f"执行记录：Base=`{SHA_A}`；Branch=`feat/one`；PR=#10；Merge SHA=未合并。"
        elif case_name == "merged_unverified_acceptance":
            acc = "| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | 未验证 |"
            evidence = ""
            exec_rec = (
                f"执行记录：Base=`{SHA_A}`；Branch=`feat/one`；PR=#10；Merge SHA=`{SHA_B}`。"
            )

    queue_rows = f"| 1 | [SF-ONE-01](#sf-one-01) | {status} | {owner} | 无 | task |"
    specs = _task_spec(
        "SF-ONE-01",
        acc_rows=acc,
        evidence_table=evidence,
        exec_record=exec_rec,
        handoff=handoff,
    )
    root = _write_minimal_repo(tmp_path, queue_rows=queue_rows, specs=specs)
    _assert_contract_failure(_run_check(root), expected_err)


@pytest.mark.parametrize(
    ("case_name", "expected_err"),
    [
        ("passed_without_evidence_row", "without citing an evidence record"),
        ("missing_evidence_record_id", "missing evidence record"),
        ("missing_test_file", "missing referenced path"),
        ("missing_test_function", "not found in"),
        ("evidence_acc_id_mismatch", "unknown acceptance id"),
        ("missing_verified_sha", "40-hex verified commit sha"),
        ("missing_command", "missing concrete command"),
        ("evidence_still_unverified", "still unverified"),
        ("evidence_reports_failed", "reports failing test result"),
        ("evidence_mixed_pass_unverified", "mixes evidence ids"),
        ("ready_false_passed_in_evidence", "without citing an evidence record"),
    ],
)
def test_rejects_fake_or_incomplete_acceptance_evidence(
    tmp_path: Path, case_name: str, expected_err: str
) -> None:
    """SF-DOC-01-A2: Claiming passed without valid test node, command, and SHA is rejected."""
    status = "In Progress"
    owner = "Alice"
    acc = "| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | E1 |"
    node = "`tests/test_sample.py::test_ok`"
    cmd = "`pytest -q`"
    sha_res = f"`{SHA_A}`；passed"
    ev_acc = "SF-ONE-01-A1"
    ev_id = "E1"

    if case_name == "passed_without_evidence_row":
        acc = "| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | 通过 |"
        ev_table = ""
    else:
        if case_name == "missing_evidence_record_id":
            ev_id = "E9"
        elif case_name == "missing_test_file":
            node = "`tests/no_such_test.py::test_ok`"
        elif case_name == "missing_test_function":
            node = "`tests/test_sample.py::test_not_defined`"
        elif case_name == "evidence_acc_id_mismatch":
            ev_acc = "SF-ONE-01-A99"
        elif case_name == "missing_verified_sha":
            sha_res = "passed（SHA 待定）"
        elif case_name == "missing_command":
            cmd = "无"
        elif case_name == "evidence_still_unverified":
            sha_res = f"`{SHA_A}`；未验证"
        elif case_name == "evidence_reports_failed":
            sha_res = f"`{SHA_A}`；18 passed, 1 failed"
        elif case_name == "evidence_mixed_pass_unverified":
            acc = "| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | E1（通过，未验证） |"
        elif case_name == "ready_false_passed_in_evidence":
            status = "Ready"
            owner = "未认领"
            acc = "| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | 通过（待实现） |"
        ev_table = (
            "| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |\n"
            "| --- | --- | --- | --- | --- |\n"
            f"| {ev_id} | {ev_acc} | {node} | {cmd} | {sha_res} |"
        )

    queue_rows = f"| 1 | [SF-ONE-01](#sf-one-01) | {status} | {owner} | 无 | task |"
    exec_rec = (
        f"执行记录：Base=`{SHA_A}`；Branch=`feat/one`；PR=无。"
        if status == "In Progress"
        else "执行记录：Base=领取时填写；PR=无。"
    )
    specs = _task_spec("SF-ONE-01", acc_rows=acc, evidence_table=ev_table, exec_record=exec_rec)
    root = _write_minimal_repo(tmp_path, queue_rows=queue_rows, specs=specs)
    _assert_contract_failure(_run_check(root), expected_err)


def test_rejects_read_budget_exceeded_over_4mib(tmp_path: Path) -> None:
    """SF-DOC-01-A3: Enforces a 4 MiB total read budget across SDD and linked docs."""
    sdd_dir_case = tmp_path / "single_large"
    _write_minimal_repo(sdd_dir_case)
    sdd_path = sdd_dir_case / "docs" / "SDD.md"
    base_text = sdd_path.read_text(encoding="utf-8")
    padding = "\n" + ("x" * 1024 + "\n") * 4200
    sdd_path.write_text(base_text + padding, encoding="utf-8")
    _assert_contract_failure(_run_check(sdd_dir_case), "read limit exceeded")

    cum_case = tmp_path / "cumulative_large"
    _write_minimal_repo(
        cum_case,
        extra_links=" [Big1](./big1.md) [Big2](./big2.md)",
    )
    chunk = ("y" * 1024 + "\n") * 2200
    (cum_case / "docs" / "big1.md").write_text("# Big1\n" + chunk, encoding="utf-8")
    (cum_case / "docs" / "big2.md").write_text("# Big2\n" + chunk, encoding="utf-8")
    _assert_contract_failure(_run_check(cum_case), "read limit exceeded")


def test_rejects_symlinks_non_regular_files_and_out_of_bounds_paths(tmp_path: Path) -> None:
    """SF-DOC-01-A3: Symlinks, FIFOs, and out-of-bounds links never get followed and fail."""
    assert _run_check(tmp_path / "nonexistent_root").returncode == 1

    outside_doc = tmp_path / "outside.md"
    outside_doc.write_text("# Outside\n", encoding="utf-8")
    oob_repo = tmp_path / "oob_repo"
    _write_minimal_repo(oob_repo, extra_links=" [Escape](../../outside.md)")
    _assert_contract_failure(_run_check(oob_repo), "escapes repository root")

    # Symlink root
    real_repo = tmp_path / "real_repo"
    _write_minimal_repo(real_repo)
    assert _run_check(real_repo).returncode == 0
    sym_root = tmp_path / "sym_root"
    os.symlink(real_repo, sym_root)
    _assert_contract_failure(_run_check(sym_root), "must not be a symlink")

    # Symlinked docs directory inside repo
    sym_dir_repo = tmp_path / "sym_dir_repo"
    sym_dir_repo.mkdir()
    outside_docs_dir = tmp_path / "ext_docs"
    outside_docs_dir.mkdir()
    (outside_docs_dir / "SDD.md").write_text("# External\n", encoding="utf-8")
    os.symlink(outside_docs_dir, sym_dir_repo / "docs")
    _assert_contract_failure(_run_check(sym_dir_repo), "symlink path rejected")

    # Symlink linked doc inside repo
    sym_doc_repo = tmp_path / "sym_doc_repo"
    _write_minimal_repo(sym_doc_repo, extra_links=" [Sym](./sym.md)")
    os.symlink(outside_doc, sym_doc_repo / "docs" / "sym.md")
    _assert_contract_failure(_run_check(sym_doc_repo), "symlink path rejected")

    # Symlink cited test file
    sym_test_repo = tmp_path / "sym_test_repo"
    ev_table = (
        "| 记录 | 验收 ID | 测试节点 / 实际入口 | 命令与环境 | 验证 SHA / 结果 / CI |\n"
        "| --- | --- | --- | --- | --- |\n"
        f"| E1 | SF-ONE-01-A1 | `tests/test_sym.py::test_ok` | `pytest -q` | `{SHA_A}`；passed |"
    )
    _write_minimal_repo(
        sym_test_repo,
        queue_rows="| 1 | [SF-ONE-01](#sf-one-01) | In Progress | Alice | 无 | ok |",
        specs=_task_spec(
            "SF-ONE-01",
            acc_rows="| SF-ONE-01-A1 | g / w / t | `tests/test_sample.py` | E1 |",
            evidence_table=ev_table,
            exec_record=f"执行记录：Base=`{SHA_A}`；Branch=`feat/one`；PR=无。",
        ),
    )
    os.symlink(sym_test_repo / "tests" / "test_sample.py", sym_test_repo / "tests" / "test_sym.py")
    _assert_contract_failure(_run_check(sym_test_repo), "symlink path rejected")

    # Non-regular file (FIFO) linked in doc
    fifo_repo = tmp_path / "fifo_repo"
    _write_minimal_repo(fifo_repo, extra_links=" [Pipe](./pipe.md)")
    os.mkfifo(fifo_repo / "docs" / "pipe.md")
    _assert_contract_failure(_run_check(fifo_repo), "non-regular file rejected")


def test_does_not_execute_documented_commands_or_import_exec_network(tmp_path: Path) -> None:
    """SF-DOC-01-A3: Shell blocks are never executed and checker uses no exec/network modules."""
    marker = tmp_path / "pwned_marker.txt"
    repo = _write_minimal_repo(
        tmp_path / "repo",
        extra_links=(
            "\n\n```sh\n"
            f"python3 -c \"open('{marker}', 'w').write('owned')\"\n"
            "false\n"
            "exit 99\n"
            "```\n"
        ),
    )
    res = _run_check(repo)
    assert res.returncode == 0, res.stderr
    assert not marker.exists()

    script_tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(script_tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"subprocess", "urllib.request", "http.client", "socket", "pty", "shutil"}
    assert imported.isdisjoint(forbidden)


def test_ci_workflow_and_docs_declare_sdd_consistency_scope() -> None:
    """SF-DOC-01-A4: CI executes check_sdd.py and docs clarify consistency-only guarantee."""
    ci_text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "scripts/check_sdd.py" in ci_text

    sdd_text = (REPO_ROOT / "docs" / "SDD.md").read_text(encoding="utf-8")
    dev_text = (REPO_ROOT / "docs" / "development.md").read_text(encoding="utf-8")
    assert "check_sdd.py" in sdd_text
    assert "不证明测试执行或语义充分" in sdd_text
    assert "check_sdd.py" in dev_text
    assert "不证明测试执行或语义充分" in dev_text
