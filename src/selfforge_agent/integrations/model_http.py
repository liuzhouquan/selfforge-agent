"""Small bounded JSON HTTP transport for configured model providers."""

from __future__ import annotations

import ipaddress
import json
import math
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ProviderHTTPError(ValueError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _validate_model_url(url: str) -> None:
    if not isinstance(url, str) or not url.strip() or "\x00" in url:
        raise ValueError("Invalid URL: must be a non-empty string without null bytes")
    if any(char.isspace() for char in url):
        raise ValueError("Invalid URL: must not contain whitespace")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid URL structure: {exc}") from exc
    if not parts.scheme:
        raise ValueError("Invalid URL: missing scheme")
    if parts.scheme not in {"https", "http"}:
        raise ValueError(f"Disallowed scheme {parts.scheme!r}; must be HTTPS or loopback HTTP")
    if not parts.hostname:
        raise ValueError("Invalid URL: missing hostname")
    if parts.username or parts.password:
        raise ValueError("Invalid URL: credentials in URL are not allowed")

    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL port: {exc}") from exc
    if port is not None and (port < 1 or port > 65535):
        raise ValueError(f"Invalid URL port {port}; must be between 1 and 65535")

    if parts.scheme == "http":
        try:
            is_loopback = ipaddress.ip_address(parts.hostname).is_loopback
        except ValueError:
            is_loopback = parts.hostname in {"localhost", "localhost.localdomain"}
        if not is_loopback:
            raise ValueError("HTTP scheme is only permitted for localhost/loopback addresses")


def _validate_api_key(api_key: str) -> None:
    if not isinstance(api_key, str):
        raise TypeError("API key must be a string")
    if any(c in api_key for c in ("\r", "\n", "\x00")):
        raise ValueError("API key must not contain newline, carriage return, or null bytes")
    if len(api_key) > 4096:
        raise ValueError("API key exceeds maximum length of 4096 characters")


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_model_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(_RestrictedRedirectHandler())


def _safe_urlopen(request: Request, timeout: float):
    return _OPENER.open(request, timeout=timeout)


urlopen = _safe_urlopen


def request_json(
    url: str,
    api_key: str,
    *,
    payload: dict[str, object] | None = None,
    timeout: float = 20,
    max_bytes: int = 131_072,
) -> dict[str, object]:
    _validate_model_url(url)
    _validate_api_key(api_key)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0 or timeout > 300):
        raise ValueError("timeout must be a finite positive number not exceeding 300 seconds")
    if (isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
            or max_bytes <= 0 or max_bytes > 16 * 1024 * 1024):
        raise ValueError("max_bytes must be a positive integer not exceeding 16 MiB")

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers, method="POST" if data else "GET")
    deadline = monotonic() + timeout
    try:
        with urlopen(request, timeout=timeout) as response:
            chunks: list[bytes] = []
            size = 0
            socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise ProviderHTTPError("Provider response timed out")
                if socket is not None:
                    try:
                        socket.settimeout(remaining)
                    except OSError:
                        # Some proxy transports close their socket after buffering the body.
                        socket = None
                chunk = response.read1(min(8192, max_bytes + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    raise ProviderHTTPError("Provider response exceeded size limit")
    except HTTPError as exc:
        raise ProviderHTTPError(f"Provider returned HTTP {exc.code}", status_code=exc.code) from exc
    except (OSError, URLError) as exc:
        raise ProviderHTTPError(f"Provider request failed: {exc}") from exc
    try:
        result = json.loads(b"".join(chunks))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderHTTPError("Provider returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ProviderHTTPError("Provider returned a non-object JSON response")
    return result
