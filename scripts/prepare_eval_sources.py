"""Provision pinned evaluation source repositories without executing repository code."""

from __future__ import annotations

import argparse
import errno
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT_ENV = "SELFFORGE_EVAL_SOURCE_ROOT"
DEFAULT_RELATIVE_ROOT = Path(".local") / "benchmark" / "repos"
DEFAULT_TIMEOUT_SECONDS = 120.0
MIN_TIMEOUT_SECONDS = 1.0
MAX_TIMEOUT_SECONDS = 300.0
DEFAULT_REF_NAME = "refs/heads/candidate"
STATE_FILENAME = ".selfforge-eval-sources.json"
OWNER_ID = "selfforge-prepare-eval-sources"
MAX_STATE_BYTES = 64 * 1024
MAX_GIT_OUTPUT_BYTES = 64 * 1024
MAX_DIAGNOSTIC_CHARS = 500
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}\Z")
_STRIPPED_GIT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
)


@dataclass(frozen=True)
class PinnedEvalSource:
    name: str
    url: str
    revision: str
    ref_name: str = DEFAULT_REF_NAME


PINNED_SOURCES: tuple[PinnedEvalSource, ...] = (
    PinnedEvalSource(
        name="jmespath",
        url="https://github.com/jmespath/jmespath.py.git",
        revision="2812594e69d43098ef60f81f4efc404c071b0418",
    ),
    PinnedEvalSource(
        name="boltons",
        url="https://github.com/mahmoud/boltons.git",
        revision="d31d821d56c8f7756af9c30a864077f2e5855321",
    ),
)


class PreparationError(RuntimeError):
    """Controlled, bounded failure during evaluation source preparation."""


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise PreparationError("Timeout must be a number between 1 and 300 seconds")
    value = float(timeout)
    if not math.isfinite(value) or value < MIN_TIMEOUT_SECONDS or value > MAX_TIMEOUT_SECONDS:
        raise PreparationError(
            f"--timeout must be between {int(MIN_TIMEOUT_SECONDS)} "
            f"and {int(MAX_TIMEOUT_SECONDS)} seconds"
        )
    return value


def _parse_timeout(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid timeout value: {raw!r}") from exc
    try:
        return _validate_timeout(value)
    except PreparationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _check_no_symlink_components(path: Path) -> Path:
    temp_ignored = set(Path(tempfile.gettempdir()).parents)
    cur = Path(path.anchor) if path.is_absolute() else Path(os.getcwd())
    for anc in reversed([cur, *cur.parents]):
        if anc not in temp_ignored and anc.is_symlink():
            raise PreparationError(f"Refusing symlink path component: {anc}")
    parts = path.parts[1:] if path.is_absolute() else path.parts
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if cur not in temp_ignored and cur.is_symlink():
                raise PreparationError(f"Refusing symlink path component: {cur}")
            cur = cur.parent
            continue
        cur = cur / part
        if cur not in temp_ignored and cur.is_symlink():
            raise PreparationError(f"Refusing symlink path: {cur}")
    return cur


def _resolve_root_arg(root_arg: Path | str | None) -> Path:
    if root_arg is not None:
        raw = str(root_arg).strip()
        if not raw:
            raise PreparationError("--root path must not be empty")
        candidate = Path(raw).expanduser()
    else:
        env_root = os.environ.get(DEFAULT_ROOT_ENV)
        if env_root is not None:
            if not env_root.strip():
                raise PreparationError(f"{DEFAULT_ROOT_ENV} must not be empty")
            candidate = Path(env_root.strip()).expanduser()
        else:
            candidate = REPO_ROOT / DEFAULT_RELATIVE_ROOT
    return _check_no_symlink_components(candidate)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    for var in _STRIPPED_GIT_ENV:
        env.pop(var, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    return env


def _terminate_process_group(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    if hasattr(os, "killpg"):
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    else:
        proc.terminate()
    try:
        proc.wait(timeout=0.3)
        return
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is None:
        if hasattr(os, "killpg"):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        else:
            proc.kill()
        proc.wait(timeout=1.0)


def _append_bounded_tail(buf: bytearray, chunk: bytes, limit: int) -> None:
    if len(chunk) >= limit:
        buf[:] = chunk[-limit:]
        return
    overflow = len(buf) + len(chunk) - limit
    if overflow > 0:
        del buf[:overflow]
    buf.extend(chunk)


def _run_git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    deadline: float,
    max_output_bytes: int = MAX_GIT_OUTPUT_BYTES,
    fail_on_stdout_overflow: bool = True,
) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise PreparationError("Timed out before starting git operation")
    cmd = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "submodule.recurse=false",
        "-c",
        "protocol.ext.allow=never",
        *(["-C", str(cwd)] if cwd is not None else []),
        *args,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
            start_new_session=True,
        )
    except OSError as exc:
        raise PreparationError(f"Failed to invoke git: {exc}") from exc

    stdout_buf = bytearray()
    stderr_buf = bytearray()
    assert proc.stdout is not None and proc.stderr is not None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ)
            selector.register(proc.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_process_group(proc)
                    raise PreparationError(f"Timed out during git {args[0]}")
                ready = selector.select(remaining)
                if not ready:
                    _terminate_process_group(proc)
                    raise PreparationError(f"Timed out during git {args[0]}")
                for key, _ in ready:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is proc.stdout:
                        if len(stdout_buf) + len(chunk) > max_output_bytes:
                            if fail_on_stdout_overflow:
                                _terminate_process_group(proc)
                                raise PreparationError(
                                    f"git {args[0]} stdout exceeded {max_output_bytes} bytes"
                                )
                            _append_bounded_tail(stdout_buf, chunk, max_output_bytes)
                        else:
                            stdout_buf.extend(chunk)
                    else:
                        _append_bounded_tail(stderr_buf, chunk, max_output_bytes)

        remaining = deadline - time.monotonic()
        if remaining <= 0 and proc.poll() is None:
            _terminate_process_group(proc)
            raise PreparationError(f"Timed out waiting for git {args[0]}")
        try:
            proc.wait(timeout=max(0.05, remaining))
        except subprocess.TimeoutExpired as exc:
            _terminate_process_group(proc)
            raise PreparationError(f"Timed out waiting for git {args[0]}") from exc
    finally:
        if proc.poll() is None:
            _terminate_process_group(proc)
        proc.stdout.close()
        proc.stderr.close()

    stdout = stdout_buf.decode("utf-8", errors="replace")
    stderr = stderr_buf.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        detail = stderr[-MAX_DIAGNOSTIC_CHARS:] if stderr else f"exit code {proc.returncode}"
        raise PreparationError(f"git {args[0]} failed: {detail}")
    return stdout


def _normalize_url(raw: str) -> str:
    value = raw.strip()
    if "://" in value:
        parts = urlsplit(value)
        if parts.username or parts.password:
            raise PreparationError("Refusing credential-bearing repository URL")
        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/").removesuffix(".git")
        if scheme == "file":
            return f"file://{path}"
        return f"{scheme}://{netloc}{path}"
    path = value.rstrip("/").removesuffix(".git")
    return str(Path(path).resolve()) if path else ""


def _read_state(root: Path) -> dict[str, Any] | None:
    state_path = root / STATE_FILENAME
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(state_path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise PreparationError(f"Refusing symlink state file: {state_path}") from exc
        raise PreparationError(f"Cannot open ownership state file {state_path}: {exc}") from exc

    try:
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise PreparationError(f"Ownership state path is not a regular file: {state_path}")
            raw = handle.read(MAX_STATE_BYTES + 1)
    except PreparationError:
        raise
    except OSError as exc:
        raise PreparationError(f"Failed reading ownership state file: {exc}") from exc

    if len(raw) > MAX_STATE_BYTES:
        raise PreparationError(f"Ownership state file exceeds {MAX_STATE_BYTES} bytes")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PreparationError(f"Malformed ownership state file {state_path}: {exc}") from exc

    if (
        not isinstance(data, dict)
        or data.get("owner") != OWNER_ID
        or data.get("version") != 1
        or not isinstance(data.get("sources"), dict)
    ):
        raise PreparationError(f"Invalid ownership state structure in {state_path}")
    for key, entry in data["sources"].items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise PreparationError(f"Invalid source entry in {state_path}")
        if not all(
            isinstance(field, str) and (val is None or isinstance(val, (str, bool)))
            for field, val in entry.items()
        ):
            raise PreparationError(f"Invalid nested field in {state_path}")
    return data


def _write_state(root: Path, state: dict[str, Any]) -> None:
    dir_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        dir_fd = os.open(root, dir_flags)
    except OSError as exc:
        raise PreparationError(f"Cannot verify target root directory {root}: {exc}") from exc
    os.close(dir_fd)

    state_path = root / STATE_FILENAME
    if state_path.is_symlink():
        raise PreparationError(f"Refusing symlink state file: {state_path}")
    payload = json.dumps(state, indent=2, sort_keys=True) + "\n"
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=".selfforge-eval-sources.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = handle.name
            handle.write(payload)
        os.replace(tmp_path, state_path)
    except OSError as exc:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise PreparationError(f"Failed writing ownership state file: {exc}") from exc


def _try_git(args: Sequence[str], *, cwd: Path, deadline: float) -> str | None:
    try:
        return _run_git(args, cwd=cwd, deadline=deadline)
    except PreparationError:
        return None


def _has_commit(repo: Path, revision: str, *, deadline: float) -> bool:
    verified = _try_git(["rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
                        cwd=repo, deadline=deadline)
    return verified is not None and verified.strip() == revision


def _named_refs_for_commit(repo: Path, revision: str, *, deadline: float) -> list[str]:
    out = _run_git(
        ["for-each-ref", "--format=%(refname)", "--contains", revision, "refs/heads", "refs/tags"],
        cwd=repo,
        deadline=deadline,
    )
    return [line.strip() for line in out.splitlines() if line.strip()]


def _inspect_target(
    source: PinnedEvalSource,
    target: Path,
    *,
    offline: bool,
    state_entry: dict[str, Any] | None,
    deadline: float,
) -> tuple[str, list[str]]:
    if target.is_symlink():
        raise PreparationError(f"Refusing symlink target directory: {target}")
    if not target.exists():
        if offline:
            raise PreparationError(f"Missing eval source cache in offline mode: {target}")
        return "create_new", []
    if not target.is_dir():
        raise PreparationError(f"Target path exists and is not a directory: {target}")

    git_dir = target / ".git"
    if git_dir.is_symlink():
        raise PreparationError(f"Refusing symlink .git directory: {git_dir}")

    is_owned = isinstance(state_entry, dict) and state_entry.get("owned") is True
    is_failed = isinstance(state_entry, dict) and state_entry.get("status") == "failed"

    if not git_dir.exists():
        if not is_owned:
            raise PreparationError(
                f"Refusing existing non-repository directory not owned by {OWNER_ID}: {target}"
            )
        if offline:
            raise PreparationError(f"Incomplete owned eval source cache (missing .git): {target}")
        unexpected = [p.name for p in target.iterdir()]
        if unexpected:
            raise PreparationError(
                f"Owned incomplete directory has unexpected files, refusing overwrite: {target}"
            )
        return "init_owned", []

    if not git_dir.is_dir():
        raise PreparationError(f"Expected .git directory, found non-directory: {git_dir}")

    toplevel = _run_git(["rev-parse", "--show-toplevel"], cwd=target, deadline=deadline).strip()
    if Path(toplevel).resolve() != target.resolve():
        raise PreparationError(f"Target path is not a Git repository root: {target}")

    actual_origin = _try_git(["config", "--get", "remote.origin.url"], cwd=target, deadline=deadline)
    if actual_origin is None or not actual_origin.strip():
        if is_owned and not offline:
            remotes = _run_git(["remote"], cwd=target, deadline=deadline).strip()
            if not remotes:
                return "configure_origin_and_fetch", []
        raise PreparationError(f"Cache repository missing expected origin remote: {target}")

    if _normalize_url(actual_origin) != _normalize_url(source.url):
        raise PreparationError(
            f"Cache repository origin mismatch for {source.name}: "
            f"expected {source.url}, got {actual_origin.strip()}"
        )

    commit_present = _has_commit(target, source.revision, deadline=deadline)
    refs = _named_refs_for_commit(target, source.revision, deadline=deadline) if commit_present else []

    if offline:
        if is_failed:
            reason = str(state_entry.get("error") or "prior preparation failed")
            raise PreparationError(f"Eval source {source.name} is in failed state: {reason}")
        if not commit_present:
            raise PreparationError(
                f"Missing pinned commit {source.revision} in offline cache: {target}"
            )
        if not refs:
            raise PreparationError(
                f"Pinned commit {source.revision} has no clone-accessible named ref in {target}"
            )
        return "ready", refs

    if commit_present and refs and not is_failed:
        return "ready", refs
    return "fetch_existing", refs


def _ensure_named_ref(repo: Path, source: PinnedEvalSource, *, deadline: float) -> list[str]:
    refs = _named_refs_for_commit(repo, source.revision, deadline=deadline)
    if refs:
        return refs

    candidate_exists = _try_git(
        ["show-ref", "--verify", "--quiet", source.ref_name], cwd=repo, deadline=deadline
    )
    if candidate_exists is None:
        head_exists = _try_git(
            ["rev-parse", "--verify", "--quiet", "HEAD"], cwd=repo, deadline=deadline
        )
        only_git_dir = [p.name for p in repo.iterdir()] == [".git"]
        if head_exists is None and only_git_dir:
            branch_name = source.ref_name.removeprefix("refs/heads/")
            _run_git(
                ["checkout", "--quiet", "-b", branch_name, source.revision],
                cwd=repo,
                deadline=deadline,
                fail_on_stdout_overflow=False,
            )
            return [source.ref_name]
        _run_git(["update-ref", source.ref_name, source.revision], cwd=repo, deadline=deadline)
        return [source.ref_name]

    fallback_ref = f"refs/heads/selfforge-eval-{source.revision[:12]}"
    _run_git(["update-ref", fallback_ref, source.revision], cwd=repo, deadline=deadline)
    return [fallback_ref]


def _default_state(existing: dict[str, Any] | None, *, root_created: bool) -> dict[str, Any]:
    if existing is not None:
        return {
            "owner": OWNER_ID,
            "version": 1,
            "root_created": bool(existing.get("root_created", False) or root_created),
            "sources": dict(existing.get("sources", {})),
        }
    return {
        "owner": OWNER_ID,
        "version": 1,
        "root_created": root_created,
        "sources": {},
    }


def _prepare_single_source(
    source: PinnedEvalSource,
    target: Path,
    action: str,
    *,
    root_path: Path,
    mutable_state: dict[str, Any],
    deadline: float,
) -> list[str]:
    prior_entry = mutable_state["sources"].get(source.name)
    owned = action in {"create_new", "init_owned"} or (
        isinstance(prior_entry, dict) and prior_entry.get("owned") is True
    )
    mutable_state["sources"][source.name] = {
        "owned": owned,
        "url": source.url,
        "revision": source.revision,
        "ref": source.ref_name,
        "status": "preparing",
        "error": None,
    }
    _write_state(root_path, mutable_state)

    try:
        if action in {"create_new", "init_owned"}:
            if action == "create_new":
                target.mkdir(exist_ok=False)
            _run_git(
                ["init", "--quiet"],
                cwd=target,
                deadline=deadline,
                fail_on_stdout_overflow=False,
            )
            _run_git(["remote", "add", "origin", source.url], cwd=target, deadline=deadline)
        elif action == "configure_origin_and_fetch":
            _run_git(["remote", "add", "origin", source.url], cwd=target, deadline=deadline)

        if not _has_commit(target, source.revision, deadline=deadline):
            _run_git(
                [
                    "fetch",
                    "--quiet",
                    "--no-progress",
                    "--no-tags",
                    "--depth=1",
                    "origin",
                    source.revision,
                ],
                cwd=target,
                deadline=deadline,
                fail_on_stdout_overflow=False,
            )
            if not _has_commit(target, source.revision, deadline=deadline):
                raise PreparationError(
                    f"Fetched repository {source.name} still lacks commit {source.revision}"
                )

        final_refs = _ensure_named_ref(target, source, deadline=deadline)
        if not _has_commit(target, source.revision, deadline=deadline) or not final_refs:
            raise PreparationError(f"Failed verifying prepared repository {source.name}")

        mutable_state["sources"][source.name] = {
            "owned": owned,
            "url": source.url,
            "revision": source.revision,
            "ref": final_refs[0],
            "status": "ready",
            "error": None,
        }
        _write_state(root_path, mutable_state)
        return final_refs
    except (PreparationError, OSError) as exc:
        err_msg = str(exc)[-MAX_DIAGNOSTIC_CHARS:]
        mutable_state["sources"][source.name] = {
            "owned": owned,
            "url": source.url,
            "revision": source.revision,
            "ref": source.ref_name,
            "status": "failed",
            "error": err_msg,
        }
        try:
            _write_state(root_path, mutable_state)
        except PreparationError:
            pass
        if isinstance(exc, PreparationError):
            raise
        raise PreparationError(f"Failed preparing {source.name}: {err_msg}") from exc


def prepare_sources(
    root: Path | str | None = None,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    offline: bool = False,
    sources: Sequence[PinnedEvalSource] | None = None,
) -> dict[str, list[str]]:
    """Prepare or validate pinned evaluation repositories under root."""
    validated_timeout = _validate_timeout(timeout)
    deadline = time.monotonic() + validated_timeout
    selected_sources = tuple(PINNED_SOURCES if sources is None else sources)
    for source in selected_sources:
        if not _COMMIT_RE.fullmatch(source.revision):
            raise PreparationError(f"Invalid pinned revision for {source.name}: {source.revision}")

    root_path = _resolve_root_arg(root)
    if root_path.exists():
        if not root_path.is_dir():
            raise PreparationError(f"Root path exists and is not a directory: {root_path}")
        state = _read_state(root_path)
        root_created = False
    else:
        if offline:
            raise PreparationError(f"Eval source root does not exist in offline mode: {root_path}")
        state = None
        root_created = True

    plan: list[tuple[PinnedEvalSource, Path, str, list[str]]] = []
    for source in selected_sources:
        target = root_path / source.name
        entry = state["sources"].get(source.name) if state else None
        if root_path.exists():
            action, refs = _inspect_target(
                source,
                target,
                offline=offline,
                state_entry=entry,
                deadline=deadline,
            )
        else:
            action, refs = "create_new", []
        plan.append((source, target, action, refs))

    results: dict[str, list[str]] = {}
    if all(action == "ready" for _, _, action, _ in plan):
        for source, _, _, refs in plan:
            results[source.name] = refs
        return results

    if not root_path.exists():
        try:
            root_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PreparationError(f"Failed creating root directory {root_path}: {exc}") from exc

    mutable_state = _default_state(state, root_created=root_created)
    if state is None and root_created:
        _write_state(root_path, mutable_state)

    for source, target, action, refs in plan:
        if action == "ready":
            results[source.name] = refs
            continue
        results[source.name] = _prepare_single_source(
            source,
            target,
            action,
            root_path=root_path,
            mutable_state=mutable_state,
            deadline=deadline,
        )

    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="prepare_eval_sources.py",
        description="Provision or validate pinned jmespath and boltons evaluation repositories.",
    )
    parser.add_argument(
        "--root",
        type=str,
        default=None,
        help=(
            f"Parent directory for pinned caches (defaults to ${DEFAULT_ROOT_ENV} or "
            f"{DEFAULT_RELATIVE_ROOT.as_posix()})"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=_parse_timeout,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Total timeout budget in seconds (1-300, default 120)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Validate existing caches offline without fetching or modifying HEAD/files",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        prepared = prepare_sources(args.root, timeout=args.timeout, offline=args.offline)
    except PreparationError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    for source in PINNED_SOURCES:
        refs = ", ".join(prepared.get(source.name, []))
        print(f"[ready] {source.name}: {source.revision} (refs={refs})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
