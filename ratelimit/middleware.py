"""ASGI middleware: works with FastAPI, Starlette or any ASGI app. No dependencies."""
from __future__ import annotations

import json
import math
from typing import Awaitable, Callable

from .core import Limiter

Scope = dict
KeyFunc = Callable[[Scope], str]


def client_ip_key(scope: Scope) -> str:
    client = scope.get("client")
    return client[0] if client else "unknown"


def header_key(name: str, fallback: KeyFunc = client_ip_key) -> KeyFunc:
    """Key on a header such as ``x-api-key``; fall back to the client IP."""
    wanted = name.lower().encode()

    def key(scope: Scope) -> str:
        for k, v in scope.get("headers", []):
            if k == wanted:
                return v.decode()
        return fallback(scope)

    return key


class RateLimitMiddleware:
    def __init__(self, app, limiter: Limiter, key_func: KeyFunc = client_ip_key, exempt_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.limiter = limiter
        self.key_func = key_func
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Callable[[], Awaitable], send: Callable[[dict], Awaitable]) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        decision = self.limiter.check(self.key_func(scope))
        headers = [
            (b"x-ratelimit-limit", str(decision.limit).encode()),
            (b"x-ratelimit-remaining", str(decision.remaining).encode()),
            (b"x-ratelimit-reset", str(math.ceil(decision.reset_after)).encode()),
        ]

        if not decision.allowed:
            body = json.dumps({"error": "rate_limited", "retry_after": round(decision.retry_after, 3)}).encode()
            headers += [
                (b"retry-after", str(max(math.ceil(decision.retry_after), 1)).encode()),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ]
            await send({"type": "http.response.start", "status": 429, "headers": headers})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                message = {**message, "headers": list(message.get("headers", [])) + headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
