"""Validate docs/SDD.md structure, task states, acceptance evidence, and safe local refs.

Note: Passing this check verifies SDD structural consistency and local file/test-node
traceability only; it does not execute tests or prove test execution or semantic adequacy.
"""

from __future__ import annotations

import argparse
import ast
import os
import posixpath
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_TOTAL_BYTES = 4 * 1024 * 1024  # 4 MiB total read budget across SDD and local refs
MAX_REPORTED_ERRORS = 40

VALID_STATUSES = frozenset(
    {"Ready", "In Progress", "In Review", "Merged", "Waiting", "Needs Design"}
)
ACTIVE_STATUSES = frozenset({"In Progress", "In Review", "Merged"})
PLACEHOLDER_VALUES = frozenset({"", "-", "无", "未认领", "领取时填写", "tbd", "todo", "n/a", "待定", "未合并"})
UNVERIFIED_MARKERS = ("未验证", "待实现", "前提未满足")
PENDING_MARKERS = ("待运行", "待执行")
PASS_MARKERS = ("passed", "通过", "已验证", "全绿", "all checks passed", "success")

TASK_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*-[A-Z0-9]+-\d+$")
ACC_ID_RE = re.compile(r"^([A-Z][A-Z0-9]*-[A-Z0-9]+-\d+)-A\d+$")
SHA40_RE = re.compile(r"\b[0-9a-f]{40}\b")
SHA40_EXACT_RE = re.compile(r"^[0-9a-f]{40}$")
EVIDENCE_REF_RE = re.compile(r"\bE\d+\b")
PR_REF_RE = re.compile(r"(?:#\d+|/pull/\d+|PR\s*#?\d+)", re.IGNORECASE)
TEST_NODE_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.py)::([A-Za-z0-9_]+)"
    r"(?:::([A-Za-z0-9_]+))?(?:\[[^\]]*\])?"
)
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
LINK_RE = re.compile(r"(?<!!)(?<!\\)\[([^\]]+)\]\(([^)\n]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")
FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
REPO_PATH_RE = re.compile(r"^(?:tests|scripts|src|docs|\.github)/[A-Za-z0-9_./-]+$")


class SDDViolationError(Exception):
    """Raised on fatal SDD I/O, symlink, traversal, or read-budget errors."""


@dataclass(frozen=True)
class MarkdownTable:
    """Parsed Markdown pipe table."""

    start_line: int
    headers: tuple[str, ...]
    rows: tuple[tuple[int, tuple[str, ...]], ...]


@dataclass(frozen=True)
class QueueEntry:
    """Row from the Section 4 task queue table."""

    line_no: int
    task_id: str
    status: str
    owner: str


@dataclass(frozen=True)
class HeadingInfo:
    """Markdown heading outside fenced code blocks."""

    line_no: int
    level: int
    raw_title: str
    anchor: str


class SafeRepoReader:
    """Bounded, symlink-rejecting reader restricted to files inside the repository root."""

    def __init__(self, root: Path, max_bytes: int = MAX_TOTAL_BYTES) -> None:
        self.root = self._validate_root(root)
        self.max_bytes = max_bytes
        self.bytes_read = 0
        self._text_cache: dict[str, str] = {}
        self._ast_cache: dict[str, ast.AST] = {}

    @staticmethod
    def _validate_root(root: Path) -> Path:
        if os.path.islink(root):
            raise SDDViolationError(f"root path must not be a symlink: {root}")
        try:
            st = os.lstat(root)
        except OSError as exc:
            raise SDDViolationError(f"cannot access root directory {root}: {exc}") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise SDDViolationError(f"root path is not a regular directory: {root}")
        return root

    @staticmethod
    def normalize_rel(rel_posix: str) -> PurePosixPath:
        """Normalize a repo-relative POSIX path while rejecting traversal outside root."""
        if not rel_posix or "\x00" in rel_posix or "\\" in rel_posix:
            raise SDDViolationError(f"invalid repository path: {rel_posix!r}")
        if rel_posix.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", rel_posix):
            raise SDDViolationError(f"absolute or drive path rejected: {rel_posix!r}")
        norm = posixpath.normpath(rel_posix)
        if norm in (".", "") or norm == ".." or norm.startswith("../"):
            raise SDDViolationError(f"path escapes repository root: {rel_posix!r}")
        return PurePosixPath(norm)

    def _verify_no_symlink_components(self, rel: PurePosixPath, *, allow_dir: bool = False) -> Path:
        cur = self.root
        last_idx = len(rel.parts) - 1
        for idx, part in enumerate(rel.parts):
            cur = cur / part
            try:
                st = os.lstat(cur)
            except OSError as exc:
                raise SDDViolationError(f"missing referenced path '{rel.as_posix()}': {exc}") from exc
            if stat.S_ISLNK(st.st_mode):
                raise SDDViolationError(f"symlink path rejected: '{rel.as_posix()}'")
            if idx < last_idx and not stat.S_ISDIR(st.st_mode):
                raise SDDViolationError(f"expected directory component in '{rel.as_posix()}'")
            if idx == last_idx:
                is_valid = stat.S_ISREG(st.st_mode) or (allow_dir and stat.S_ISDIR(st.st_mode))
                if not is_valid:
                    raise SDDViolationError(f"non-regular file rejected: '{rel.as_posix()}'")
        return cur

    def check_exists(self, rel_posix: str, *, allow_dir: bool = False) -> PurePosixPath:
        """Verify a repo-relative path exists without symlinks or root escape."""
        rel = self.normalize_rel(rel_posix)
        self._verify_no_symlink_components(rel, allow_dir=allow_dir)
        return rel

    def read_text(self, rel_posix: str) -> str:
        """Read UTF-8 text from a non-symlink file inside root under the 4 MiB total cap."""
        rel = self.normalize_rel(rel_posix)
        key = rel.as_posix()
        if key in self._text_cache:
            return self._text_cache[key]

        target = self._verify_no_symlink_components(rel, allow_dir=False)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(target, flags)
        except OSError as exc:
            raise SDDViolationError(f"cannot open '{key}': {exc}") from exc

        chunks: list[bytes] = []
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                raise SDDViolationError(f"non-regular file rejected: '{key}'")
            remaining = self.max_bytes - self.bytes_read
            if st.st_size > remaining:
                raise SDDViolationError(
                    f"document read limit exceeded ({self.max_bytes} bytes) reading '{key}'"
                )
            total = 0
            while True:
                to_read = (remaining - total) + 1
                chunk = os.read(fd, min(65536, to_read))
                if not chunk:
                    break
                total += len(chunk)
                if total > remaining:
                    raise SDDViolationError(
                        f"document read limit exceeded ({self.max_bytes} bytes) reading '{key}'"
                    )
                chunks.append(chunk)
        finally:
            os.close(fd)

        raw = b"".join(chunks)
        self.bytes_read += len(raw)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SDDViolationError(f"invalid UTF-8 encoding in '{key}': {exc}") from exc
        self._text_cache[key] = text
        return text

    def parse_python_ast(self, rel_posix: str) -> ast.AST:
        """Read and AST-parse a Python file inside root under the 4 MiB read cap."""
        rel = self.normalize_rel(rel_posix)
        key = rel.as_posix()
        if key in self._ast_cache:
            return self._ast_cache[key]
        source = self.read_text(key)
        try:
            tree = ast.parse(source, filename=key)
        except SyntaxError as exc:
            raise SDDViolationError(f"invalid Python syntax in '{key}': {exc}") from exc
        self._ast_cache[key] = tree
        return tree


def github_anchor(title: str) -> str:
    """Compute a GitHub-style Markdown anchor slug for a heading title."""
    cleaned = LINK_RE.sub(r"\1", title)
    cleaned = cleaned.replace("`", "")
    out: list[str] = []
    for ch in cleaned.strip().lower():
        if ch in (" ", "\t"):
            out.append("-")
        elif ch in ("-", "_") or ch.isalnum():
            out.append(ch)
    return "".join(out)


def split_fence_mask(lines: list[str]) -> list[bool]:
    """Return a per-line boolean mask indicating lines strictly inside fenced blocks."""
    in_fence = False
    fence_char = ""
    fence_len = 0
    mask: list[bool] = []
    for line in lines:
        if not in_fence:
            m = FENCE_OPEN_RE.match(line)
            if m:
                marker = m.group(1)
                fence_char = marker[0]
                fence_len = len(marker)
                in_fence = True
                mask.append(True)
                continue
            mask.append(False)
        else:
            mask.append(True)
            close_pat = rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$"
            if re.match(close_pat, line):
                in_fence = False
                fence_char = ""
                fence_len = 0
    return mask


def extract_headings(lines: list[str], fence_mask: list[bool]) -> list[HeadingInfo]:
    """Extract headings and deduplicated GitHub anchors outside fenced code blocks."""
    counts: dict[str, int] = {}
    headings: list[HeadingInfo] = []
    for idx, line in enumerate(lines):
        if fence_mask[idx]:
            continue
        m = HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        raw_title = m.group(2).strip()
        base = github_anchor(raw_title)
        seen = counts.get(base, 0)
        counts[base] = seen + 1
        anchor = base if seen == 0 else f"{base}-{seen}"
        headings.append(HeadingInfo(line_no=idx + 1, level=level, raw_title=raw_title, anchor=anchor))
    return headings


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(bool(re.match(r"^:?-{3,}:?$", c)) for c in cells)


def extract_tables(
    lines: list[str],
    fence_mask: list[bool],
    start_line: int,
    end_line: int,
) -> list[MarkdownTable]:
    """Extract pipe tables within [start_line, end_line] (1-based inclusive)."""
    tables: list[MarkdownTable] = []
    i = start_line - 1
    limit = min(end_line, len(lines))
    while i + 1 < limit:
        if fence_mask[i] or fence_mask[i + 1]:
            i += 1
            continue
        line = lines[i].strip()
        next_line = lines[i + 1].strip()
        if not (line.startswith("|") and next_line.startswith("|")):
            i += 1
            continue
        headers = _split_table_row(line)
        sep = _split_table_row(next_line)
        if len(headers) != len(sep) or not _is_separator_row(sep):
            i += 1
            continue
        rows: list[tuple[int, tuple[str, ...]]] = []
        j = i + 2
        while j < limit and not fence_mask[j] and lines[j].strip().startswith("|"):
            cells = _split_table_row(lines[j])
            if len(cells) < len(headers):
                cells.extend([""] * (len(headers) - len(cells)))
            rows.append((j + 1, tuple(cells[: len(headers)])))
            j += 1
        tables.append(MarkdownTable(start_line=i + 1, headers=tuple(headers), rows=tuple(rows)))
        i = j
    return tables


def extract_text_fences(lines: list[str], start_line: int, end_line: int) -> list[str]:
    """Extract ```text fenced blocks within [start_line, end_line]."""
    blocks: list[str] = []
    in_text = False
    fence_char = ""
    fence_len = 0
    buf: list[str] = []
    for idx in range(start_line - 1, min(end_line, len(lines))):
        line = lines[idx]
        if not in_text:
            m = FENCE_OPEN_RE.match(line)
            if m and m.group(2).strip().lower() in ("text", ""):
                # Only treat ```text blocks starting with Owner / 状态 as handoff blocks later
                marker = m.group(1)
                fence_char = marker[0]
                fence_len = len(marker)
                in_text = True
                buf = []
        else:
            if re.match(rf"^ {{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$", line):
                blocks.append("\n".join(buf))
                in_text = False
            else:
                buf.append(line)
    return blocks


def _mask_inline_code(line: str) -> str:
    return INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), line)


def validate_local_links(
    reader: SafeRepoReader,
    doc_rel_posix: str,
    lines: list[str],
    fence_mask: list[bool],
    own_anchors: set[str],
    errors: list[str],
) -> None:
    """Check every non-HTTP(S) Markdown link in doc stays inside root, non-symlink, with valid anchors."""
    doc_dir = posixpath.dirname(doc_rel_posix)
    anchor_cache: dict[str, set[str]] = {doc_rel_posix: own_anchors}

    for idx, line in enumerate(lines):
        if fence_mask[idx]:
            continue
        masked = _mask_inline_code(line)
        for m in LINK_RE.finditer(masked):
            raw_target = m.group(2).strip()
            target = re.split(r'\s+"', raw_target, maxsplit=1)[0].strip()
            if not target or target.startswith(("https://", "http://")):
                continue
            if ":" in target.split("/", 1)[0]:
                errors.append(f"{doc_rel_posix}:{idx + 1}: disallowed URI scheme in link '{target}'")
                continue
            if target.startswith("#"):
                slug = target[1:]
                if slug not in own_anchors:
                    errors.append(f"{doc_rel_posix}:{idx + 1}: missing local anchor '#{slug}'")
                continue

            file_part, _, fragment = target.partition("#")
            joined = posixpath.join(doc_dir, file_part) if doc_dir else file_part
            try:
                if fragment or file_part.endswith(".md"):
                    rel = reader.normalize_rel(joined)
                    text = reader.read_text(rel.as_posix())
                    if fragment:
                        key = rel.as_posix()
                        if key not in anchor_cache:
                            sub_lines = text.splitlines()
                            sub_mask = split_fence_mask(sub_lines)
                            anchor_cache[key] = {h.anchor for h in extract_headings(sub_lines, sub_mask)}
                        if fragment not in anchor_cache[key]:
                            errors.append(
                                f"{doc_rel_posix}:{idx + 1}: missing anchor '#{fragment}' in '{key}'"
                            )
                else:
                    reader.check_exists(joined, allow_dir=True)
            except SDDViolationError as exc:
                errors.append(f"{doc_rel_posix}:{idx + 1}: {exc}")


def _strip_backticks(val: str) -> str:
    return val.strip().strip("`").strip()


def _is_placeholder(val: str) -> bool:
    return _strip_backticks(val).lower() in PLACEHOLDER_VALUES


def _test_collection_disabled(body: list[ast.stmt]) -> bool:
    for node in body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__test__" for target in node.targets
        ) and isinstance(node.value, ast.Constant) and node.value.value is False:
            return True
    return False


def _python_has_test_node(tree: ast.AST, cls_name: str | None, func_name: str) -> bool:
    if not isinstance(tree, ast.Module) or _test_collection_disabled(tree.body):
        return False
    if not func_name.startswith("test"):
        return False
    body = tree.body
    if cls_name is not None:
        if not cls_name.startswith("Test"):
            return False
        classes = [node for node in body if isinstance(node, ast.ClassDef) and node.name == cls_name]
        if not classes or _test_collection_disabled(classes[0].body):
            return False
        body = classes[0].body
        if any(isinstance(node, ast.FunctionDef) and node.name in {"__init__", "__new__"}
               for node in body):
            return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name
        for node in body
    )


def _verify_test_node(reader: SafeRepoReader, file_rel: str, cls_name: str | None, func_name: str) -> str | None:
    try:
        tree = reader.parse_python_ast(file_rel)
    except SDDViolationError as exc:
        return str(exc)
    if not _python_has_test_node(tree, cls_name, func_name):
        target = f"{cls_name}::{func_name}" if cls_name else func_name
        return f"test node '{target}' not found in '{file_rel}'"
    return None


def _parse_test_nodes(text: str) -> list[tuple[str, str | None, str]]:
    nodes: list[tuple[str, str | None, str]] = []
    for m in TEST_NODE_RE.finditer(text):
        file_rel, first, second = m.group(1), m.group(2), m.group(3)
        if second is not None:
            nodes.append((file_rel, first, second))
        else:
            nodes.append((file_rel, None, first))
    return nodes


def _extract_exec_metadata(
    section_lines: list[str],
    section_tables: list[MarkdownTable],
    handoff_blocks: list[str],
) -> dict[str, str | None]:
    meta: dict[str, str | None] = {
        "base": None,
        "branch": None,
        "pr": None,
        "merge": None,
        "handoff_owner": None,
        "handoff_status": None,
    }
    exec_text = "\n".join(section_lines)
    if m := re.search(r"Base\s*=\s*([^；;\n。]+)", exec_text):
        meta["base"] = _strip_backticks(m.group(1))
    if m := re.search(r"Branch\s*=\s*([^；;\n。]+)", exec_text):
        meta["branch"] = _strip_backticks(m.group(1))
    if m := re.search(r"(?:Merge(?:\s*SHA)?|合并\s*SHA)\s*=\s*([^；;\n。]+)", exec_text):
        meta["merge"] = _strip_backticks(m.group(1))
    if m := re.search(r"(?<![/\w])PR\s*=\s*([^；;\n。]+)", exec_text):
        meta["pr"] = _strip_backticks(m.group(1))

    for tbl in section_tables:
        lowered = [h.lower() for h in tbl.headers]
        if any(k in lowered for k in ("base", "base sha")) and tbl.rows:
            row = tbl.rows[0][1]
            for hdr, cell in zip(lowered, row, strict=True):
                if hdr in ("base", "base sha") and not meta["base"]:
                    meta["base"] = _strip_backticks(cell)
                elif hdr in ("分支", "branch") and not meta["branch"]:
                    meta["branch"] = _strip_backticks(cell)
                elif hdr == "pr" and not meta["pr"]:
                    meta["pr"] = _strip_backticks(cell)
                elif hdr in ("merge sha", "合并 sha", "merge") and not meta["merge"]:
                    meta["merge"] = _strip_backticks(cell)

    for block in handoff_blocks:
        if "Owner / 状态" not in block:
            continue
        if m := re.search(r"^Owner / 状态：\s*(.+?)\s*/\s*(.+?)\s*$", block, re.MULTILINE):
            meta["handoff_owner"] = m.group(1).strip()
            meta["handoff_status"] = m.group(2).strip()
        if (not meta["base"] or _is_placeholder(meta["base"])) and (
            m := re.search(r"^实际 base SHA[^：]*：\s*(.+)$", block, re.MULTILINE)
        ):
            base_slots = re.split(r"\s+/\s+", m.group(1).strip())
            if base_slots:
                meta["base"] = _strip_backticks(base_slots[0])
        if m := re.search(r"^PR / 准确 head CI / merge SHA[^：]*：\s*(.+)$", block, re.MULTILINE):
            slots = [s.strip() for s in re.split(r"\s+/\s+", m.group(1).strip())]
            if slots and (not meta["pr"] or _is_placeholder(meta["pr"])):
                meta["pr"] = slots[0]
            if len(slots) >= 3 and (not meta["merge"] or _is_placeholder(meta["merge"])):
                merge_match = SHA40_RE.search(slots[-1])
                meta["merge"] = merge_match.group(0) if merge_match else _strip_backticks(slots[-1])
    return meta


def _validate_evidence_row(
    reader: SafeRepoReader,
    task_id: str,
    status: str,
    row_line: int,
    row: tuple[str, str, str, str, str],
    valid_acc_ids: set[str],
    errors: list[str],
) -> bool:
    rec_id, ev_acc, nodes_cell, cmd_cell, sha_res_cell = row
    ok = True
    if "模板" in rec_id:
        errors.append(f"docs/SDD.md:{row_line}: template record '{rec_id}' not allowed in {task_id}")
        return False
    if ev_acc not in valid_acc_ids:
        errors.append(
            f"docs/SDD.md:{row_line}: evidence '{rec_id}' references unknown acceptance ID '{ev_acc}'"
        )
        ok = False

    nodes = _parse_test_nodes(nodes_cell)
    if not nodes:
        errors.append(
            f"docs/SDD.md:{row_line}: evidence '{rec_id}' missing real 'file.py::test_name' node"
        )
        ok = False
    for file_rel, cls_name, func_name in nodes:
        if err := _verify_test_node(reader, file_rel, cls_name, func_name):
            errors.append(f"docs/SDD.md:{row_line}: evidence '{rec_id}': {err}")
            ok = False

    for code in INLINE_CODE_RE.findall(nodes_cell):
        if "::" not in code and REPO_PATH_RE.match(code):
            try:
                reader.check_exists(code, allow_dir=False)
            except SDDViolationError as exc:
                errors.append(f"docs/SDD.md:{row_line}: evidence '{rec_id}': {exc}")
                ok = False

    if _is_placeholder(cmd_cell):
        errors.append(
            f"docs/SDD.md:{row_line}: evidence '{rec_id}' missing concrete command/environment"
        )
        ok = False

    if not SHA40_RE.search(sha_res_cell):
        errors.append(
            f"docs/SDD.md:{row_line}: evidence '{rec_id}' missing full 40-hex verified commit SHA"
        )
        ok = False

    lower_res = sha_res_cell.lower()
    if any(marker in sha_res_cell for marker in UNVERIFIED_MARKERS):
        errors.append(
            f"docs/SDD.md:{row_line}: evidence '{rec_id}' cannot claim verified while still unverified"
        )
        ok = False
    if re.search(r"(?<!0\s)\bfailed\b|\bfailure\b", lower_res):
        errors.append(
            f"docs/SDD.md:{row_line}: evidence '{rec_id}' reports failing test result"
        )
        ok = False
    if not any(marker in lower_res for marker in PASS_MARKERS):
        errors.append(
            f"docs/SDD.md:{row_line}: evidence '{rec_id}' missing explicit passing result"
        )
        ok = False
    if status == "Merged" and any(marker in lower_res for marker in (*PENDING_MARKERS, "pending")):
        errors.append(
            f"docs/SDD.md:{row_line}: Merged evidence '{rec_id}' cannot retain pending checks"
        )
        ok = False
    return ok


def _validate_task_section(
    reader: SafeRepoReader,
    entry: QueueEntry,
    lines: list[str],
    fence_mask: list[bool],
    start_line: int,
    end_line: int,
    seen_acc_ids: set[str],
    errors: list[str],
) -> int:
    tables = extract_tables(lines, fence_mask, start_line, end_line)
    acc_table = next((t for t in tables if t.headers and t.headers[0] == "验收 ID"), None)
    if acc_table is None or not acc_table.rows:
        errors.append(f"docs/SDD.md:{start_line}: {entry.task_id} missing acceptance table")
        return 0

    task_acc_ids: set[str] = set()
    for rline, row in acc_table.rows:
        acc_id = _strip_backticks(row[0])
        m = ACC_ID_RE.match(acc_id)
        if not m or m.group(1) != entry.task_id:
            errors.append(
                f"docs/SDD.md:{rline}: acceptance ID '{acc_id}' must start with '{entry.task_id}-A'"
            )
            continue
        if acc_id in seen_acc_ids:
            errors.append(f"docs/SDD.md:{rline}: duplicate acceptance ID '{acc_id}'")
            continue
        seen_acc_ids.add(acc_id)
        task_acc_ids.add(acc_id)

    ev_table = next((t for t in tables if t.headers and t.headers[0] == "记录"), None)
    ev_by_id: dict[str, tuple[int, tuple[str, ...]]] = {}
    if ev_table is not None:
        for rline, row in ev_table.rows:
            rec_id = _strip_backticks(row[0])
            if rec_id in ev_by_id:
                errors.append(
                    f"docs/SDD.md:{rline}: duplicate evidence record '{rec_id}' in {entry.task_id}"
                )
                continue
            padded = (row + ("",) * 5)[:5]
            ev_by_id[rec_id] = (rline, padded)
            _validate_evidence_row(
                reader, entry.task_id, entry.status, rline, padded, task_acc_ids, errors
            )

    handoff_blocks = extract_text_fences(lines, start_line, end_line)
    meta = _extract_exec_metadata(
        lines[start_line - 1 : end_line], tables, handoff_blocks
    )
    if meta["handoff_status"] and meta["handoff_status"] != entry.status:
        errors.append(
            f"docs/SDD.md:{start_line}: handoff status '{meta['handoff_status']}' disagrees with "
            f"queue status '{entry.status}' for {entry.task_id}"
        )
    if meta["handoff_owner"] and meta["handoff_owner"] != entry.owner:
        errors.append(
            f"docs/SDD.md:{start_line}: handoff owner '{meta['handoff_owner']}' disagrees with "
            f"queue owner '{entry.owner}' for {entry.task_id}"
        )

    if entry.status in ACTIVE_STATUSES:
        if _is_placeholder(entry.owner):
            errors.append(
                f"docs/SDD.md:{entry.line_no}: {entry.task_id} status '{entry.status}' requires a real Owner"
            )
        if not meta["base"] or not SHA40_EXACT_RE.match(meta["base"]):
            errors.append(
                f"docs/SDD.md:{start_line}: {entry.task_id} status '{entry.status}' requires a "
                f"40-hex Base SHA (got {meta['base']!r})"
            )

    if entry.status == "Merged":
        if not meta["pr"] or _is_placeholder(meta["pr"]) or not PR_REF_RE.search(meta["pr"]):
            errors.append(
                f"docs/SDD.md:{start_line}: Merged task {entry.task_id} requires a PR reference"
            )
        if not meta["merge"] or not SHA40_EXACT_RE.match(meta["merge"]):
            errors.append(
                f"docs/SDD.md:{start_line}: Merged task {entry.task_id} requires a full 40-hex "
                f"merge SHA (got {meta['merge']!r})"
            )

    for rline, row in acc_table.rows:
        acc_id = _strip_backticks(row[0])
        ev_cell = row[3].strip()
        lower_ev = ev_cell.lower()
        refs = EVIDENCE_REF_RE.findall(ev_cell)
        has_pass = any(marker in lower_ev for marker in PASS_MARKERS)
        has_unverified = any(marker in ev_cell for marker in UNVERIFIED_MARKERS)

        if refs:
            if has_unverified:
                errors.append(
                    f"docs/SDD.md:{rline}: {acc_id} mixes evidence IDs {refs} with unverified marker"
                )
            for ref in refs:
                if ref not in ev_by_id:
                    errors.append(
                        f"docs/SDD.md:{rline}: {acc_id} references missing evidence record '{ref}'"
                    )
                else:
                    ev_line, ev_row = ev_by_id[ref]
                    if ev_row[1] != acc_id:
                        errors.append(
                            f"docs/SDD.md:{ev_line}: evidence '{ref}' belongs to '{ev_row[1]}', "
                            f"not '{acc_id}'"
                        )
        elif has_pass:
            errors.append(
                f"docs/SDD.md:{rline}: {acc_id} claims passed without citing an evidence record"
            )
        elif has_unverified:
            if entry.status == "Merged":
                errors.append(
                    f"docs/SDD.md:{rline}: Merged task {entry.task_id} leaves {acc_id} unverified"
                )
        else:
            errors.append(
                f"docs/SDD.md:{rline}: unrecognized acceptance evidence state in {acc_id}: {ev_cell!r}"
            )

    return len(acc_table.rows)


def check_sdd(root: Path) -> tuple[int, int]:
    """Validate docs/SDD.md under root; return (task_count, acceptance_count) or raise/fail."""
    reader = SafeRepoReader(root)
    sdd_text = reader.read_text("docs/SDD.md")
    lines = sdd_text.splitlines()
    fence_mask = split_fence_mask(lines)
    headings = extract_headings(lines, fence_mask)
    anchors = {h.anchor for h in headings}
    errors: list[str] = []

    validate_local_links(reader, "docs/SDD.md", lines, fence_mask, anchors, errors)

    sec_boundaries = [h for h in headings if h.level == 2]
    sec4 = next((h for h in sec_boundaries if h.raw_title.startswith("4.")), None)
    if sec4 is None:
        raise SDDViolationError("docs/SDD.md missing Section 4 heading ('## 4. ...')")
    sec4_idx = sec_boundaries.index(sec4)
    sec4_end = (
        sec_boundaries[sec4_idx + 1].line_no - 1
        if sec4_idx + 1 < len(sec_boundaries)
        else len(lines)
    )

    sec4_tables = extract_tables(lines, fence_mask, sec4.line_no, sec4_end)
    queue_table = next(
        (t for t in sec4_tables if len(t.headers) >= 5 and t.headers[1] == "任务 ID"), None
    )
    if queue_table is None or not queue_table.rows:
        raise SDDViolationError("docs/SDD.md Section 4 missing task queue table")

    queue_entries: list[QueueEntry] = []
    seen_task_ids: set[str] = set()
    for rline, row in queue_table.rows:
        _, raw_id, status, owner = row[0], row[1].strip(), row[2].strip(), row[3].strip()
        link_m = LINK_RE.fullmatch(raw_id)
        if link_m:
            task_id = _strip_backticks(link_m.group(1))
            link_target = link_m.group(2).strip()
            expected_anchor = f"#{github_anchor(task_id)}"
            if link_target != expected_anchor:
                errors.append(
                    f"docs/SDD.md:{rline}: queue anchor for {task_id} must be '{expected_anchor}', "
                    f"got '{link_target}'"
                )
        else:
            task_id = _strip_backticks(raw_id)

        if not TASK_ID_RE.match(task_id):
            errors.append(f"docs/SDD.md:{rline}: invalid task ID '{task_id}'")
            continue
        if task_id in seen_task_ids:
            errors.append(f"docs/SDD.md:{rline}: duplicate queue task ID '{task_id}'")
            continue
        seen_task_ids.add(task_id)
        if status not in VALID_STATUSES:
            errors.append(
                f"docs/SDD.md:{rline}: invalid task status '{status}' for {task_id}"
            )
        queue_entries.append(
            QueueEntry(line_no=rline, task_id=task_id, status=status, owner=owner)
        )

    sec4_subs = [
        h for h in headings if h.level == 3 and sec4.line_no < h.line_no <= sec4_end
    ]
    spec_ranges: dict[str, tuple[int, int]] = {}
    for idx, sub in enumerate(sec4_subs):
        sub_id = sub.raw_title.strip()
        sub_end = sec4_subs[idx + 1].line_no - 1 if idx + 1 < len(sec4_subs) else sec4_end
        if sub_id in spec_ranges:
            errors.append(f"docs/SDD.md:{sub.line_no}: duplicate spec section '### {sub_id}'")
            continue
        if sub_id not in seen_task_ids:
            errors.append(
                f"docs/SDD.md:{sub.line_no}: orphan spec section '### {sub_id}' absent from queue"
            )
            continue
        spec_ranges[sub_id] = (sub.line_no, sub_end)

    for entry in queue_entries:
        if entry.task_id not in spec_ranges:
            errors.append(
                f"docs/SDD.md:{entry.line_no}: missing spec section '### {entry.task_id}'"
            )

    seen_acc_ids: set[str] = set()
    acceptance_count = 0
    for entry in queue_entries:
        if entry.task_id in spec_ranges:
            s_line, e_line = spec_ranges[entry.task_id]
            acceptance_count += _validate_task_section(
                reader, entry, lines, fence_mask, s_line, e_line, seen_acc_ids, errors
            )

    sec5 = next((h for h in sec_boundaries if h.raw_title.startswith("5.")), None)
    if sec5 is not None:
        sec5_idx = sec_boundaries.index(sec5)
        sec5_end = (
            sec_boundaries[sec5_idx + 1].line_no - 1
            if sec5_idx + 1 < len(sec_boundaries)
            else len(lines)
        )
        for tbl in extract_tables(lines, fence_mask, sec5.line_no, sec5_end):
            if tbl.headers and tbl.headers[0] == "行为":
                for rline, row in tbl.rows:
                    for file_rel, cls_name, func_name in _parse_test_nodes(row[1]):
                        if err := _verify_test_node(reader, file_rel, cls_name, func_name):
                            errors.append(f"docs/SDD.md:{rline}: Section 5 example: {err}")

    if errors:
        shown = errors[:MAX_REPORTED_ERRORS]
        suffix = (
            f"\n... ({len(errors) - len(shown)} more error(s) omitted)"
            if len(errors) > len(shown)
            else ""
        )
        raise SDDViolationError("\n".join(shown) + suffix)

    return len(queue_entries), acceptance_count


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for bounded SDD contract validation."""
    default_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description=(
            "Validate docs/SDD.md structure, statuses, and local references. "
            "Verifies consistency only; does not prove test execution or semantic adequacy."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root,
        help="Repository root containing docs/SDD.md (default: repository root).",
    )
    args = parser.parse_args(argv)
    try:
        task_count, acc_count = check_sdd(args.root)
    except SDDViolationError as exc:
        print(f"SDD contract violation:\n{exc}", file=sys.stderr)
        return 1
    print(
        f"OK: verified {task_count} SDD task(s) and {acc_count} acceptance item(s) "
        "(structural consistency and local reference check only — does not prove test "
        "execution or semantic adequacy)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
