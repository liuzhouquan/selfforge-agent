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


def test_cross_origin_redirect_never_receives_provider_credentials() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from selfforge_agent.integrations.model_http import ProviderHTTPError

    received = []

    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/models")
            self.end_headers()

        def log_message(self, *_args):
            pass

    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    workers = [Thread(target=server.serve_forever, daemon=True) for server in (source, sink)]
    for worker in workers:
        worker.start()
    try:
        with pytest.raises(ProviderHTTPError, match="redirect"):
            request_json(f"http://127.0.0.1:{source.server_port}/models", "fixture-key")
        assert received == []
    finally:
        for server in (source, sink):
            server.shutdown()
            server.server_close()
        for worker in workers:
            worker.join(timeout=2)


def test_oversized_provider_update_preserves_readable_previous_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "providers.json"
    monkeypatch.setenv("SELFFORGE_PROVIDER_CONFIG_PATH", str(path))
    providers.save_provider_settings("deepseek", api_key="fixture-key", base_url="https://api.example.com", model="fixture")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="64 KiB"):
        providers.save_provider_settings("deepseek", api_key=None,
                                         base_url="https://api.example.com/" + "x" * 65536, model="fixture")
    assert path.read_bytes() == before


def test_request_json_rejects_timeout_too_large_for_float_conversion() -> None:
    with pytest.raises(ValueError, match="timeout"):
        request_json("https://api.example.com", "fixture", timeout=10**1000)


def test_http_parser_recursion_is_a_controlled_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from selfforge_agent.integrations import model_http

    monkeypatch.setattr(model_http, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"{}"))

    def recursive_parser(*_args, **_kwargs):
        raise RecursionError("deep provider JSON")

    monkeypatch.setattr(model_http.json, "loads", recursive_parser)
    with pytest.raises(model_http.ProviderHTTPError, match="invalid JSON"):
        request_json("https://api.example.com", "fixture")


def test_redirect_chain_uses_one_absolute_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib.request import Request

    from selfforge_agent.integrations import model_http

    clock = [3.0]
    monkeypatch.setattr(model_http, "monotonic", lambda: clock[0])
    request = Request("https://provider.example/first", headers={"Authorization": "Bearer fixture"})
    request.timeout = 5.0
    request._provider_deadline = 5.0
    handler = model_http._RestrictedRedirectHandler()
    redirected = handler.redirect_request(request, None, 302, "Found", {}, "https://provider.example/second")
    assert request.timeout == 2.0
    assert redirected.get_header("Authorization") == "Bearer fixture"
    clock[0] = 6.0
    with pytest.raises(model_http.ProviderHTTPError, match="timed out"):
        handler.redirect_request(redirected, None, 302, "Found", {}, "https://provider.example/third")


def test_settings_read_error_does_not_close_reused_descriptor(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text('{}')
    monkeypatch.setenv("SELFFORGE_PROVIDER_CONFIG_PATH", str(settings))
    original_fdopen = os.fdopen
    replacement = []

    class FailingReader:
        def __init__(self, fd, mode):
            self.file = original_fdopen(fd, mode)

        def __enter__(self):
            return self

        def read(self, _limit):
            raise OSError("fixture read failure")

        def __exit__(self, *_args):
            self.file.close()
            replacement.append(os.open(tmp_path / "sentinel", os.O_CREAT | os.O_RDWR, 0o600))

    monkeypatch.setattr(providers.os, "fdopen", FailingReader)
    try:
        with pytest.raises((OSError, ValueError)):
            providers._settings()
        assert replacement
        os.fstat(replacement[0])
    finally:
        for fd in replacement:
            try:
                os.close(fd)
            except OSError:
                pass
