import math
import os
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

EMAIL = "24f3004572@ds.study.iitm.ac.in"
ALLOWED_ORIGIN = "https://app-oav1pw.example.com"
# Exam page origin(s), comma separated, set as an environment variable on the host.
EXAM_ORIGINS = [o.strip() for o in os.getenv("EXAM_ORIGINS", "").split(",") if o.strip()]

LIMIT = 13      # requests
WINDOW = 10.0   # seconds


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.hits = defaultdict(deque)
        self.calls = 0

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":  # preflights are not counted
            return await call_next(request)

        client_id = request.headers.get("x-client-id")
        if not client_id:
            fwd = request.headers.get("x-forwarded-for", "")
            ip = fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")
            client_id = "ip:" + ip

        now = time.monotonic()
        self.calls += 1
        if self.calls % 500 == 0:  # drop idle buckets
            for k in [k for k, q in self.hits.items() if not q or now - q[-1] >= WINDOW]:
                del self.hits[k]

        q = self.hits[client_id]
        while q and now - q[0] >= WINDOW:
            q.popleft()
        if len(q) >= LIMIT:
            retry = max(1, math.ceil(WINDOW - (now - q[0])))
            return JSONResponse(
                {"detail": "rate limit exceeded", "request_id": request.state.request_id},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
        q.append(now)
        return await call_next(request)


app = FastAPI()

# add_middleware: the last one added is the outermost.
# Order of execution: CORS -> RequestContext -> RateLimit -> route,
# so even 429 responses carry the CORS and X-Request-ID headers.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[ALLOWED_ORIGIN, *EXAM_ORIGINS],
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Retry-After"],
    allow_credentials=False,
)


@app.get("/ping")
async def ping(request: Request):
    return {"email": EMAIL, "request_id": request.state.request_id}
