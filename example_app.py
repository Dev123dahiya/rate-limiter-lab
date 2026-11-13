"""Demo API: 5 requests per 10 s per API key (or per IP without one).

    pip install -e ".[demo]" && uvicorn example_app:app
    for i in 1 2 3 4 5 6 7; do curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/; done
"""
from fastapi import FastAPI

from ratelimit import RateLimitMiddleware, SlidingWindowCounter
from ratelimit.middleware import header_key

app = FastAPI(title="rate-limiter-lab demo")
app.add_middleware(
    RateLimitMiddleware,
    limiter=SlidingWindowCounter(limit=5, window=10),
    key_func=header_key("x-api-key"),
    exempt_paths=("/health",),
)


@app.get("/")
def index() -> dict:
    return {"message": "hello"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
