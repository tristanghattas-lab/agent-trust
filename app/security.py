"""
API security: who may call what, how often, and how much.

Keys (server-side only, never in a browser or the repo):
    APP_API_KEYS    the Shopify app's server. Store-level reads and writes.
                    (METRICS_API_KEY is still accepted here, so existing
                    deploys keep working until it's rotated.)
    ADMIN_API_KEYS  internal tools (the threat-test harness, ops scripts).
                    Everything the app can do, plus admin-only endpoints.
Each holds a comma-separated list, so a key is rotated by adding the new
one, switching callers over, then removing the old one. No keys configured
means every authenticated request is refused (fails closed).

Rate limits: a token bucket per client IP on every request, tighter on the
public ingestion endpoints, plus a per-store budget on ingestion and a
per-key budget on authenticated calls. Over the limit -> 429.
Request bodies over MAX_BODY_BYTES are refused (413).
Every authenticated call is logged with its role and path (never the key).
"""
from __future__ import annotations

import hmac
import logging
import os
import threading
import time

from fastapi import Header, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

logger = logging.getLogger("agent_trust.security")

MAX_BODY_BYTES = int(os.getenv("MAX_BODY_BYTES", str(2 * 1024 * 1024)))


# ---------------------------------------------------------------------------
# Keys and roles
# ---------------------------------------------------------------------------
def _keys(*names: str) -> list[str]:
    out = []
    for n in names:
        out += [k.strip() for k in os.getenv(n, "").split(",") if k.strip()]
    return out


def role_for(token: str) -> str | None:
    """'admin', 'app', or None. Constant-time against every configured key."""
    if not token:
        return None
    role = None
    for k in _keys("ADMIN_API_KEYS", "ADMIN_API_KEY"):
        if hmac.compare_digest(token.encode(), k.encode()):
            role = "admin"
    if role is None:
        for k in _keys("APP_API_KEYS", "APP_API_KEY", "METRICS_API_KEY"):
            if hmac.compare_digest(token.encode(), k.encode()):
                role = "app"
    return role


def _check(request: Request, authorization: str | None, allowed: set[str]) -> str:
    if not _keys("ADMIN_API_KEYS", "ADMIN_API_KEY", "APP_API_KEYS", "APP_API_KEY", "METRICS_API_KEY"):
        raise HTTPException(status_code=503, detail="API keys not configured")
    token = (authorization or "").removeprefix("Bearer ").strip()
    role = role_for(token)
    if role is None:
        logger.warning("auth refused path=%s ip=%s", request.url.path, client_ip(request))
        raise HTTPException(status_code=401, detail="invalid or missing API key")
    if role not in allowed:
        logger.warning("auth forbidden role=%s path=%s", role, request.url.path)
        raise HTTPException(status_code=403, detail="not allowed for this key")
    if not KEY_LIMITER.allow(f"key:{role}"):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    logger.info("auth ok role=%s %s %s shop=%s", role, request.method, request.url.path,
                request.query_params.get("shop", "-"))
    request.state.role = role
    return role


def require_app(request: Request, authorization: str | None = Header(default=None)) -> str:
    """The Shopify app's server or an admin tool."""
    return _check(request, authorization, {"app", "admin"})


def require_admin(request: Request, authorization: str | None = Header(default=None)) -> str:
    """Internal tools only."""
    return _check(request, authorization, {"admin"})


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
def _limits_on() -> bool:
    return os.getenv("RATE_LIMITS", "on").lower() != "off"


class TokenBucket:
    """In-memory token buckets keyed by string. `rate` tokens per second,
    up to `burst`. One process on Render, so in-memory is enough; move to
    Redis if the API is ever scaled out."""

    def __init__(self, rate: float, burst: int, max_keys: int = 50_000):
        self.rate, self.burst, self.max_keys = rate, burst, max_keys
        self._b: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        if not _limits_on():
            return True
        now = time.monotonic()
        with self._lock:
            tokens, last = self._b.get(key, (float(self.burst), now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            ok = tokens >= cost
            self._b[key] = (tokens - cost if ok else tokens, now)
            if len(self._b) > self.max_keys:  # drop the stalest half
                for k, _ in sorted(self._b.items(), key=lambda kv: kv[1][1])[: self.max_keys // 2]:
                    self._b.pop(k, None)
            return ok


IP_LIMITER = TokenBucket(rate=5.0, burst=120)            # ~300/min per IP, any path
INGEST_IP_LIMITER = TokenBucket(rate=2.0, burst=60)      # ~120/min per IP on public ingestion
SHOP_LIMITER = TokenBucket(rate=40.0, burst=400)         # ~2,400/min per store on ingestion
KEY_LIMITER = TokenBucket(rate=20.0, burst=200)          # ~1,200/min per key role

PUBLIC_INGEST = ("/ingest", "/journey", "/pixel/events", "/edge/ingest")
WEBHOOKS = ("/webhooks/",)


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown"))


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY_BYTES:
            return JSONResponse({"detail": "request too large"}, status_code=413)
        ip = client_ip(request)
        is_hook = path.startswith(WEBHOOKS)
        # Shopify sends webhooks in bursts from a few IPs: give them room.
        if not IP_LIMITER.allow(f"ip:{ip}", cost=0.2 if is_hook else 1.0):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "10"})
        if path in PUBLIC_INGEST and not INGEST_IP_LIMITER.allow(f"ingest:{ip}"):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "10"})
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response


def shop_allowed(shop: str | None) -> bool:
    """Per-store budget on public ingestion (called once the shop is parsed)."""
    return bool(shop) and SHOP_LIMITER.allow(f"shop:{shop}")


# ---------------------------------------------------------------------------
# Registered stores: public ingestion is only kept for stores that installed
# the app (or are listed in ALLOWED_SHOPS), so nobody can fill the database
# with made-up stores.
# ---------------------------------------------------------------------------
_registered: dict[str, float] = {}
_REG_TTL = 300.0


def is_registered(shop: str | None) -> bool:
    if not shop:
        return False
    allowed = {s.strip().lower() for s in os.getenv("ALLOWED_SHOPS", "").split(",") if s.strip()}
    default = os.getenv("DEFAULT_SHOP", "").strip().lower()
    # "*" accepts every store: local development and tests only.
    if "*" in allowed or shop in allowed or (default and shop == default) or shop == "demo":
        return True
    now = time.monotonic()
    if _registered.get(shop, 0) > now:
        return True
    try:
        from app.db import SessionLocal
        from app.models import ShopPlan
        with SessionLocal() as db:
            ok = db.get(ShopPlan, shop) is not None
    except Exception:  # noqa: BLE001 - database hiccup: don't drop real data
        return True
    if ok:
        _registered[shop] = now + _REG_TTL
    return ok


def register(shop: str) -> None:
    _registered[shop] = time.monotonic() + _REG_TTL
