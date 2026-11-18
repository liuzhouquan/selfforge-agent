"""Small bounded JSON HTTP transport for configured model providers."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import time
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_HTTP_TIMEOUT = 300
MAX_HEADERS_COUNT = 50
MAX_HEADER_KEY_LENGTH = 128
MAX_HEADER_VALUE_LENGTH = 4096
MAX_HEADERS_BYTES = 16 * 1024  # 16 KiB total custom headers
MAX_REQUEST_PAYLOAD_BYTES = 16 * 1024 * 1024  # 16 MiB
MAX_CHUNKS = 100_000

DISALLOWED_HEADERS = frozenset({
    "connection",
    "upgrade",
    "keep-alive",
    "proxy-authorization",
    "transfer-encoding",
    "content-length",
    "te",
    "trailer",
    "proxy-authenticate",
})

ALLOWED_METHODS = frozenset({
    "GET",
    "POST",
    "PUT",
    "DELETE",
    "PATCH",
    "HEAD",
})

HEADER_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})


class ProviderHTTPError(ValueError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _validate_model_url(url: str) -> None:
    if not isinstance(url, str) or not url.strip() or "\x00" in url:
        raise ValueError("Invalid URL: must be a non-empty string without null bytes")
    if any(char.isspace() for char in url):
        raise ValueError("Invalid URL: must not contain whitespace")
    if "#" in url:
        raise ValueError("Invalid URL: URL must not contain fragment identifier")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise ValueError(f"Invalid URL structure: {exc}") from exc
    if parts.fragment:
        raise ValueError("Invalid URL: URL must not contain fragment identifier")
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


def _validate_headers(headers: dict[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise TypeError("headers must be a dict or None")
    if len(headers) > MAX_HEADERS_COUNT:
        raise ValueError(f"headers count {len(headers)} exceeds maximum of {MAX_HEADERS_COUNT}")

    total_bytes = 0
    validated: dict[str, str] = {}
    for k, v in headers.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise TypeError("Header keys and values must be strings")
        if any(c in k for c in ("\r", "\n", "\x00")) or any(c in v for c in ("\r", "\n", "\x00")):
            raise ValueError("Header key and value must not contain CRLF or null bytes")
        if not HEADER_KEY_PATTERN.match(k):
            raise ValueError(f"Invalid header key {k!r}: must match {HEADER_KEY_PATTERN.pattern}")
        if len(k) > MAX_HEADER_KEY_LENGTH:
            raise ValueError(f"Header key exceeds maximum length of {MAX_HEADER_KEY_LENGTH}")
        if len(v) > MAX_HEADER_VALUE_LENGTH:
            raise ValueError(f"Header value for {k!r} exceeds maximum length of {MAX_HEADER_VALUE_LENGTH}")
        if k.lower() in DISALLOWED_HEADERS:
            raise ValueError(f"Disallowed header: {k!r} is a forbidden hop-by-hop header")

        k_bytes = k.encode("utf-8")
        v_bytes = v.encode("utf-8")
        total_bytes += len(k_bytes) + len(v_bytes)
        if total_bytes > MAX_HEADERS_BYTES:
            raise ValueError(f"Total headers size exceeds maximum limit of {MAX_HEADERS_BYTES} bytes")

        validated[k] = v
    return validated


def _validate_method(method: str | None, has_data: bool) -> str:
    if method is None:
        return "POST" if has_data else "GET"
    if not isinstance(method, str):
        raise TypeError("method must be a string or None")
    m = method.strip().upper()
    if not m or m not in ALLOWED_METHODS:
        raise ValueError(f"Disallowed HTTP method: {method!r}")
    return m


class _RestrictedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_model_url(newurl)
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        def origin(parts):
            return (parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        if origin(old) != origin(new):
            raise ProviderHTTPError("Cross-origin provider redirect is not allowed")
        deadline = getattr(req, "_provider_deadline", None)
        if deadline is not None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ProviderHTTPError("Provider redirect timed out")
            # urllib's redirect dispatcher opens the next request using req.timeout.
            req.timeout = remaining
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and deadline is not None:
            redirected._provider_deadline = deadline
        return redirected


_OPENER = build_opener(_RestrictedRedirectHandler())


def _safe_urlopen(request: Request, timeout: float):
    return _OPENER.open(request, timeout=timeout)


urlopen = _safe_urlopen


def request_json(
    url: str,
    api_key: str,
    *,
    payload: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
    timeout: float = 20,
    max_bytes: int = 131_072,
    max_retries: int = 0,
    backoff_factor: float = 0.5,
) -> dict[str, object]:
    _validate_model_url(url)
    _validate_api_key(api_key)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or timeout <= 0
        or timeout > MAX_HTTP_TIMEOUT
        or not math.isfinite(timeout)
    ):
        raise ValueError("timeout must be a finite positive number not exceeding 300 seconds")
    if (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes <= 0
        or max_bytes > 16 * 1024 * 1024
    ):
        raise ValueError("max_bytes must be a positive integer not exceeding 16 MiB")
    if (
        isinstance(max_retries, bool)
        or not isinstance(max_retries, int)
        or max_retries < 0
        or max_retries > 5
    ):
        raise ValueError("max_retries must be an integer between 0 and 5")
    if (
        isinstance(backoff_factor, bool)
        or not isinstance(backoff_factor, (int, float))
        or backoff_factor < 0
        or not math.isfinite(backoff_factor)
    ):
        raise ValueError("backoff_factor must be a finite non-negative number")

    custom_headers = _validate_headers(headers)

    data = None
    if payload is not None:
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dict or None")
        try:
            serialized = json.dumps(payload)
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError(f"Invalid request payload: {exc}") from exc
        data = serialized.encode("utf-8")
        if len(data) > MAX_REQUEST_PAYLOAD_BYTES:
            raise ValueError(
                f"Request payload exceeds maximum size limit of {MAX_REQUEST_PAYLOAD_BYTES} bytes"
            )

    resolved_method = _validate_method(method, data is not None)

    req_headers: dict[str, str] = {}
    if api_key:
        req_headers["Authorization"] = f"Bearer {api_key}"
    if data is not None:
        req_headers["Content-Type"] = "application/json"

    for k, v in custom_headers.items():
        for existing in list(req_headers.keys()):
            if existing.lower() == k.lower():
                del req_headers[existing]
        req_headers[k] = v

    deadline = monotonic() + timeout
    remaining = timeout

    for attempt in range(max_retries + 1):
        if attempt > 0:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ProviderHTTPError("Provider response timed out")

        request = Request(url, data=data, headers=req_headers, method=resolved_method)
        request._provider_deadline = deadline

        try:
            with urlopen(request, timeout=remaining) as response:
                chunks: list[bytes] = []
                size = 0
                chunk_count = 0
                socket = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
                read_fn = getattr(response, "read1", None)
                if read_fn is None:
                    read_fn = getattr(response, "read", None)
                if read_fn is None:
                    raise ProviderHTTPError("Provider response stream is not readable")
                while True:
                    remaining = deadline - monotonic()
                    if remaining <= 0:
                        raise ProviderHTTPError("Provider response timed out")
                    if socket is not None:
                        try:
                            socket.settimeout(remaining)
                        except OSError:
                            socket = None
                    chunk = read_fn(min(8192, max_bytes + 1 - size))
                    if not chunk:
                        break
                    chunk_count += 1
                    if chunk_count > MAX_CHUNKS:
                        raise ProviderHTTPError("Provider response exceeded maximum chunk count")
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        raise ProviderHTTPError("Provider response exceeded size limit")

            try:
                result = json.loads(b"".join(chunks))
            except (ValueError, UnicodeDecodeError, RecursionError) as exc:
                raise ProviderHTTPError("Provider returned invalid JSON") from exc
            if not isinstance(result, dict):
                raise ProviderHTTPError("Provider returned a non-object JSON response")
            return result

        except HTTPError as exc:
            if exc.code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                delay = backoff_factor * (2 ** attempt)
                remaining = deadline - monotonic()
                if remaining <= delay:
                    raise ProviderHTTPError("Provider response timed out")
                time.sleep(delay)
                continue
            raise ProviderHTTPError(f"Provider returned HTTP {exc.code}", status_code=exc.code) from exc
        except (OSError, URLError) as exc:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ProviderHTTPError("Provider response timed out")
            if attempt < max_retries:
                delay = backoff_factor * (2 ** attempt)
                remaining = deadline - monotonic()
                if remaining <= delay:
                    raise ProviderHTTPError("Provider response timed out")
                time.sleep(delay)
                continue
            raise ProviderHTTPError(f"Provider request failed: {exc}") from exc

    raise ProviderHTTPError("Provider response timed out")
