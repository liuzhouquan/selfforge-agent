
import pytest

from selfforge_agent.runtime import environment as env
from selfforge_agent.runtime import images


def profile(tmp_path):
    lock = tmp_path / "requirements.lock"
    import hashlib
    lock.write_text("demo==1 --hash=sha256:" + hashlib.sha256(b"demo").hexdigest())
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    (wheels / "demo.whl").write_bytes(b"demo")
    return lock, wheels


@pytest.mark.parametrize("timeout", [float("nan"), float("inf")])
def test_audit_timeout_must_be_finite(tmp_path, timeout):
    lock, wheels = profile(tmp_path)
    with pytest.raises(ValueError):
        env.audit_wheelhouse(wheelhouse=wheels, requirements_file=lock, timeout=timeout)


def test_last_read_cannot_exceed_audit_deadline(tmp_path, monkeypatch):
    lock, wheels = profile(tmp_path)
    now = [0.0]
    original = images._read
    def read(path, limit):
        result = original(path, limit)
        if path.suffix == ".whl":
            now[0] += 1
        return result
    monkeypatch.setattr(env.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(env, "_read", read)
    monkeypatch.setattr(images, "_read", read)
    with pytest.raises(ValueError, match="timed out"):
        env.audit_wheelhouse(wheelhouse=wheels, requirements_file=lock, timeout=0.5)


def test_oversized_wheels_are_rejected_before_reading(tmp_path, monkeypatch):
    lock, wheels = profile(tmp_path)
    (wheels / "demo.whl").unlink()
    for n in range(3): (wheels / f"demo{n}.whl").write_bytes(b"large")
    opened = []
    original = images._read
    def read(path, limit):
        if path.suffix == ".whl": opened.append(path)
        return original(path, limit)
    for module in (env, images):
        monkeypatch.setattr(module, "_read", read)
        monkeypatch.setattr(module, "MAX_WHEEL_BYTES", 4, raising=False)
        monkeypatch.setattr(module, "MAX_CONTEXT_BYTES", len(lock.read_bytes()) + 4, raising=False)
    report = env.audit_wheelhouse(wheelhouse=wheels, requirements_file=lock)
    assert not opened
    assert report["unsupported_count"] == 3 and not report["ready"]
