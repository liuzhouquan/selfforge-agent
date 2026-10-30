"""Explicit, private test-diagnostic snapshots; never model or source archives."""

import json
import os
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from selfforge_agent.models import TestResult

MAX_ARTIFACT_BYTES = 128 * 1024


def prepare_artifacts(directory: Path, repositories: list[Path]) -> Path:
    parent = directory.expanduser().resolve()
    if any(parent.is_relative_to(repository.resolve()) for repository in repositories):
        raise ValueError("Failure artifacts must stay outside evaluated repositories")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root = parent / uuid4().hex
    root.mkdir(mode=0o700)
    return root


def write_failure(root: Path, *, case: str, repeat: int, reason: str,
                  visible: TestResult | None, holdout: TestResult | None) -> str:
    def details(result: TestResult | None) -> dict | None:
        if result is None:
            return None
        return result.model_dump(exclude={"command", "environment", "stdout", "stderr"})

    payload = json.dumps({"version": 1, "case": case, "repeat": repeat, "reason": reason,
                          "visible": details(visible), "holdout": details(holdout)},
                         ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_ARTIFACT_BYTES:
        raise ValueError("Failure artifact exceeds 128 KiB")
    filename = f"{case}-{repeat}.json"
    path = root / filename
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
    except OSError:
        with suppress(OSError):
            path.unlink()
        raise
    return filename
