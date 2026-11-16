"""Security and robustness tests for model HTTP transport and provider settings (SF-PROV-02)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import pytest

from selfforge_agent import providers
from selfforge_agent.integrations.model_http import request_json


def test_request_json_rejects_non_http_schemes_and_prevents_file_leak(tmp_path: Path) -> None:
    secret_file = tmp_path / "secret.json"
    secret_file.write_text('{"secret": "leaked"}', encoding="utf-8")

    for bad_url in (
        f"file://{secret_file}",
        "file:///etc/passwd",
        "ftp://example.com/api",
        "gopher://127.0.0.1:6379",
        "javascript:alert(1)",
        "data:text/plain;base64,SGVsbG8=",
        "http://remote.example.com/api",  # remote plain http disallowed
    ):
        with pytest.raises(ValueError, match="scheme|HTTPS"):
            request_json(bad_url, "api-key")


def test_request_json_rejects_crlf_and_null_bytes_in_api_key() -> None:
    for bad_key in (
        "secret\r\nInjected-Header: evil",
        "secret\nInjected-Header: evil",
        "secret\rInjected-Header: evil",
        "secret\x00nullbyte",
        "x" * 4097,
    ):
        with pytest.raises(ValueError, match="API key|header"):
            request_json("https://api.example.com/v1", bad_key)


def test_request_json_rejects_invalid_timeout_and_max_bytes() -> None:
    for bad_timeout in (0, -1, float("nan"), float("inf"), True, False, -0.0, 300.1):
        with pytest.raises(ValueError, match="timeout"):
            request_json("https://api.example.com/v1", "key", timeout=bad_timeout)  # type: ignore[arg-type]

    for bad_max_bytes in (0, -1, float("nan"), True, False, 16 * 1024 * 1024 + 1):
        with pytest.raises(ValueError, match="max_bytes|response limit"):
            request_json("https://api.example.com/v1", "key", max_bytes=bad_max_bytes)  # type: ignore[arg-type]


def test_request_json_validates_url_hostname_and_port() -> None:
    for bad_url in (
        "",
        "   ",
        "https:///path",
        "https://user:pass@example.com/api",
        "https://example.com:0/api",
        "https://example.com:65536/api",
        "https://example.com:abc/api",
        "https://example .com/api",
        "https://example\x00.com/api",
    ):
        with pytest.raises(ValueError, match="URL|host|port"):
            request_json(bad_url, "key")


def test_providers_settings_rejects_symlink_and_fifo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings_path = tmp_path / "providers.json"
    monkeypatch.setenv("SELFFORGE_PROVIDER_CONFIG_PATH", str(settings_path))

    # 1. Symlink settings file
    target = tmp_path / "real_providers.json"
    target.write_text('{"deepseek": {"model": "test"}}', encoding="utf-8")
    settings_path.symlink_to(target)

    with pytest.raises(ValueError, match="symlink|regular file"):
        providers._settings()

    settings_path.unlink()

    # 2. FIFO settings file
    os.mkfifo(settings_path)
    with pytest.raises(ValueError, match="regular file|FIFO"):
        providers._settings()

    settings_path.unlink()

    # 3. Oversized settings file (> 64 KiB)
    settings_path.write_text(" " * (64 * 1024 + 1), encoding="utf-8")
    with pytest.raises(ValueError, match="size|exceed"):
        providers._settings()


def test_providers_settings_lock_rejects_symlink(tmp_path: Path) -> None:
    settings_path = tmp_path / "providers.json"
    lock_path = tmp_path / "providers.json.lock"
    victim = tmp_path / "victim.txt"
    victim.write_text("sensitive", encoding="utf-8")
    lock_path.symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"), providers._settings_lock(settings_path):
        pass

    assert victim.read_text(encoding="utf-8") == "sensitive"


def test_providers_save_settings_rejects_symlink_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    sym_dir = tmp_path / "sym_dir"
    sym_dir.symlink_to(real_dir)
    settings_path = sym_dir / "providers.json"
    monkeypatch.setenv("SELFFORGE_PROVIDER_CONFIG_PATH", str(settings_path))

    with pytest.raises(ValueError, match="symlink"):
        providers.save_provider_settings(
            "deepseek", api_key="test-key", base_url="https://api.deepseek.com", model="deepseek-flash"
        )


def test_validate_base_url_port_and_control_chars() -> None:
    for bad_url in (
        "https://api.deepseek.com:99999",
        "https://api.deepseek.com:0",
        "https://api.deepseek.com\x00",
        "https://api.deepseek.com:abc",
        "https://api.deepseek.com:-1",
    ):
        with pytest.raises(ValueError, match="Base URL|port|URL"):
            providers.validate_base_url(bad_url)
