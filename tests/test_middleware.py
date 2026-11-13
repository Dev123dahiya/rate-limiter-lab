import asyncio
import json

from ratelimit import RateLimitMiddleware, TokenBucket
from ratelimit.middleware import header_key


async def hello_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def call(app, path="/", headers=(), client=("10.0.0.1", 1234)):
    sent = []

    async def run():
        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            sent.append(message)

        await app({"type": "http", "path": path, "headers": list(headers), "client": client}, receive, send)

    asyncio.run(run())
    start = sent[0]
    return start["status"], dict(start["headers"]), sent[1]["body"]


def test_passes_through_and_adds_headers(clock):
    app = RateLimitMiddleware(hello_app, TokenBucket(3, 1.0, clock))
    status, headers, body = call(app)
    assert status == 200 and body == b"ok"
    assert headers[b"x-ratelimit-limit"] == b"3"
    assert headers[b"x-ratelimit-remaining"] == b"2"


def test_returns_429_with_retry_after(clock):
    app = RateLimitMiddleware(hello_app, TokenBucket(2, 0.5, clock))
    assert [call(app)[0] for _ in range(3)] == [200, 200, 429]
    status, headers, body = call(app)
    assert status == 429
    assert headers[b"retry-after"] == b"2"
    assert json.loads(body)["error"] == "rate_limited"


def test_keys_by_api_key_header(clock):
    app = RateLimitMiddleware(hello_app, TokenBucket(1, 0.01, clock), key_func=header_key("x-api-key"))
    assert call(app, headers=[(b"x-api-key", b"alice")])[0] == 200
    assert call(app, headers=[(b"x-api-key", b"alice")])[0] == 429
    assert call(app, headers=[(b"x-api-key", b"bob")])[0] == 200


def test_exempt_paths_are_not_limited(clock):
    app = RateLimitMiddleware(hello_app, TokenBucket(1, 0.01, clock), exempt_paths=("/health",))
    assert [call(app, path="/health")[0] for _ in range(5)] == [200] * 5
