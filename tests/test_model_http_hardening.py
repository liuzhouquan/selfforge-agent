"""Hardening and resilience tests for model HTTP transport (SF-HTTP-01)."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from urllib.error import HTTPError

import pytest

from selfforge_agent.integrations import model_http
from selfforge_agent.integrations.model_http import ProviderHTTPError, request_json


class TestHTTPHeaderValidationAndInjection:
    def test_request_json_rejects_crlf_in_custom_headers(self) -> None:
        bad_headers = [
            {"X-Injected": "value\r\nInjected: true"},
            {"X-Injected\r\nBad": "value"},
            {"X-Injected\nBad": "value"},
            {"X-Injected\rBad": "value"},
            {"X-Null\x00Byte": "value"},
            {"X-Header": "value\x00null"},
        ]
        for headers in bad_headers:
            with pytest.raises(ValueError, match="CRLF|newline|null|header"):
                request_json("https://api.example.com/v1", "key", headers=headers)

    def test_request_json_rejects_hop_by_hop_headers(self) -> None:
        hop_by_hop = [
            {"connection": "close"},
            {"Connection": "keep-alive"},
            {"Upgrade": "websocket"},
            {"Keep-Alive": "timeout=5"},
            {"Proxy-Authorization": "Basic dXNlcjpwYXNz"},
            {"Transfer-Encoding": "chunked"},
        ]
        for headers in hop_by_hop:
            with pytest.raises(ValueError, match="Disallowed header|hop-by-hop"):
                request_json("https://api.example.com/v1", "key", headers=headers)

    def test_request_json_rejects_oversized_headers_count_and_length(self) -> None:
        # 1. More than 50 headers
        too_many = {f"X-Header-{i}": f"val-{i}" for i in range(51)}
        with pytest.raises(ValueError, match="count|exceed"):
            request_json("https://api.example.com/v1", "key", headers=too_many)

        # 2. Key too long (> 128 chars)
        long_key = {"X-" + "k" * 127: "value"}
        with pytest.raises(ValueError, match="length|exceed"):
            request_json("https://api.example.com/v1", "key", headers=long_key)

        # 3. Value too long (> 4096 chars)
        long_val = {"X-Custom": "v" * 4097}
        with pytest.raises(ValueError, match="length|exceed"):
            request_json("https://api.example.com/v1", "key", headers=long_val)

        # 4. Total encoded size exceeds 16 KiB
        huge_headers = {f"X-Hdr-{i:02d}": "x" * 500 for i in range(35)}
        with pytest.raises(ValueError, match="size|exceed"):
            request_json("https://api.example.com/v1", "key", headers=huge_headers)

    def test_request_json_rejects_invalid_header_keys(self) -> None:
        # Invalid characters in header key (spaces, colons, non-ascii, controls)
        invalid_keys = [
            {"Invalid Key": "val"},
            {"Header:Colon": "val"},
            {"Header=Equal": "val"},
            {"": "val"},
            {"Header@Bad": "val"},
        ]
        for headers in invalid_keys:
            with pytest.raises(ValueError, match="header key|Invalid header"):
                request_json("https://api.example.com/v1", "key", headers=headers)

    def test_request_json_accepts_valid_custom_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_headers = {}

        def mock_urlopen(req, timeout=20):
            captured_headers.update(req.headers)
            resp = MagicMock()
            resp.read1.side_effect = [b'{"status": "ok"}', b""]
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        res = request_json(
            "https://api.example.com/v1",
            "api-token",
            headers={"X-Request-Id": "req-12345", "X-Model-Source": "selfforge"},
        )
        assert res == {"status": "ok"}
        assert captured_headers.get("X-request-id") == "req-12345" or captured_headers.get("X-Request-Id") == "req-12345"
        assert captured_headers.get("Authorization") == "Bearer api-token"


class TestPayloadValidationAndBounds:
    def test_request_json_rejects_non_dict_payload(self) -> None:
        bad_payloads = [
            [1, 2, 3],
            "string-payload",
            12345,
            True,
            (1, 2),
        ]
        for bad in bad_payloads:
            with pytest.raises(TypeError, match="payload must be a dict"):
                request_json("https://api.example.com/v1", "key", payload=bad)  # type: ignore[arg-type]

    def test_request_json_rejects_oversized_payload(self) -> None:
        # Payload serializing to > 16 MiB
        large_dict = {"data": "x" * (16 * 1024 * 1024 + 10)}
        with pytest.raises(ValueError, match="payload exceeds|size limit"):
            request_json("https://api.example.com/v1", "key", payload=large_dict)

    def test_request_json_handles_unserializable_payload_gracefully(self) -> None:
        class Unserializable:
            pass

        bad_dict = {"key": Unserializable()}
        with pytest.raises(ValueError, match="Invalid request payload|JSON"):
            request_json("https://api.example.com/v1", "key", payload=bad_dict)  # type: ignore[dict-item]


class TestURLValidationHardening:
    def test_request_json_rejects_url_with_fragment(self) -> None:
        bad_urls = [
            "https://api.example.com/v1#section",
            "https://api.example.com/#frag",
            "http://127.0.0.1:8000/v1#token=secret",
        ]
        for url in bad_urls:
            with pytest.raises(ValueError, match="fragment"):
                request_json(url, "key")

    def test_request_json_accepts_ipv6_loopback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_urlopen(req, timeout=20):
            resp = MagicMock()
            resp.read1.side_effect = [b'{"loopback": true}', b""]
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        res = request_json("http://[::1]:8080/v1/models", "key")
        assert res == {"loopback": True}


class TestTransientRetryAndDeadlineHardening:
    def test_request_json_rejects_invalid_retry_parameters(self) -> None:
        for bad_retries in (-1, 6, 1.5, True, False, "two"):
            with pytest.raises(ValueError, match="max_retries"):
                request_json("https://api.example.com/v1", "key", max_retries=bad_retries)  # type: ignore[arg-type]

        for bad_backoff in (-0.1, float("nan"), float("inf"), True, False, "half"):
            with pytest.raises(ValueError, match="backoff_factor"):
                request_json("https://api.example.com/v1", "key", backoff_factor=bad_backoff)  # type: ignore[arg-type]

    def test_request_json_retries_transient_503_and_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call_count = [0]
        sleeps = []

        monkeypatch.setattr(model_http.time, "sleep", lambda s: sleeps.append(s))

        def mock_urlopen(req, timeout=20):
            call_count[0] += 1
            if call_count[0] < 3:
                raise HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"Busy"))
            resp = MagicMock()
            resp.read1.side_effect = [b'{"retry": "success"}', b""]
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        res = request_json(
            "https://api.example.com/v1",
            "key",
            max_retries=3,
            backoff_factor=0.1,
        )
        assert res == {"retry": "success"}
        assert call_count[0] == 3
        assert len(sleeps) == 2
        assert sleeps[0] == pytest.approx(0.1)
        assert sleeps[1] == pytest.approx(0.2)

    def test_request_json_does_not_retry_401_or_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call_count = [0]

        def mock_urlopen(req, timeout=20):
            call_count[0] += 1
            raise HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"Forbidden"))

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        with pytest.raises(ProviderHTTPError, match="HTTP 401") as exc_info:
            request_json("https://api.example.com/v1", "key", max_retries=3)

        assert exc_info.value.status_code == 401
        assert call_count[0] == 1  # No retry for client error

    def test_request_json_retry_respects_total_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        current_time = [100.0]

        monkeypatch.setattr(model_http, "monotonic", lambda: current_time[0])
        monkeypatch.setattr(model_http.time, "sleep", lambda s: current_time.__setitem__(0, current_time[0] + s))

        def mock_urlopen(req, timeout=20):
            # Each failure simulates taking 4 seconds
            current_time[0] += 4.0
            raise HTTPError(req.full_url, 503, "Service Unavailable", {}, io.BytesIO(b"Busy"))

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        with pytest.raises(ProviderHTTPError, match="timed out"):
            # Timeout is 5 seconds. First call takes 4s, backoff is 0.5s -> 4.5s.
            # Next call would exceed 5s deadline.
            request_json(
                "https://api.example.com/v1",
                "key",
                timeout=5.0,
                max_retries=3,
                backoff_factor=0.5,
            )


class TestStreamingBudgetAndMethodValidation:
    def test_request_json_rejects_invalid_methods(self) -> None:
        bad_methods = ["INVALID", "CONNECT", "TRACE", 123, ""]
        for m in bad_methods:
            with pytest.raises((ValueError, TypeError), match="method"):
                request_json("https://api.example.com/v1", "key", method=m)  # type: ignore[arg-type]

    def test_request_json_accepts_custom_method(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_req = []

        def mock_urlopen(req, timeout=20):
            captured_req.append(req)
            resp = MagicMock()
            resp.read1.side_effect = [b'{"method": "ok"}', b""]
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        res = request_json("https://api.example.com/v1", "key", method="PUT", payload={"updated": True})
        assert res == {"method": "ok"}
        assert captured_req[0].get_method() == "PUT"

    def test_request_json_enforces_max_chunks_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(model_http, "MAX_CHUNKS", 5)

        def mock_urlopen(req, timeout=20):
            resp = MagicMock()
            # Infinite stream of single bytes
            resp.read1.side_effect = [b"{"] + [b" "] * 10
            resp.__enter__.return_value = resp
            resp.__exit__.return_value = None
            return resp

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        with pytest.raises(ProviderHTTPError, match="chunk count"):
            request_json("https://api.example.com/v1", "key")

    def test_request_json_read_fallback_when_read1_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def mock_urlopen(req, timeout=20):
            # BytesIO has read, but not read1
            return io.BytesIO(b'{"fallback": true}')

        monkeypatch.setattr(model_http, "urlopen", mock_urlopen)

        res = request_json("https://api.example.com/v1", "key")
        assert res == {"fallback": True}


def test_huge_backoff_is_controlled_before_network(monkeypatch):
    from selfforge_agent.integrations import model_http

    def unexpected(*args, **kwargs):
        pytest.fail("invalid backoff reached network")

    monkeypatch.setattr(model_http, "urlopen", unexpected)
    with pytest.raises(ValueError, match="backoff_factor"):
        model_http.request_json("http://localhost/v1", "", backoff_factor=10**400)
