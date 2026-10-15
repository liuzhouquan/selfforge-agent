"""Small bounded JSON HTTP transport for configured model providers."""

import json
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderHTTPError(ValueError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def request_json(
    url: str,
    api_key: str,
    *,
    payload: dict[str, object] | None = None,
    timeout: float = 20,
    max_bytes: int = 131_072,
) -> dict[str, object]:
    if timeout <= 0 or max_bytes <= 0:
        raise ValueError("A positive timeout and response limit are required")
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
                    socket.settimeout(remaining)
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
