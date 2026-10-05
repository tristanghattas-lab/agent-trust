"""Encrypt integration credentials at rest (Fernet: AES-128-CBC + HMAC).

Key: INTEGRATIONS_KEY if set, otherwise derived from EDGE_SIGNING_SECRET, so
no extra secret is needed to start. Rotating the key makes stored tokens
unreadable; stores then need to reconnect.
"""
from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


def _fernet() -> Fernet:
    raw = os.getenv("INTEGRATIONS_KEY") or os.getenv("EDGE_SIGNING_SECRET")
    if not raw:
        raise RuntimeError("INTEGRATIONS_KEY (or EDGE_SIGNING_SECRET) not configured")
    key = base64.urlsafe_b64encode(hashlib.sha256(f"integrations:{raw}".encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError("stored credential can't be decrypted; reconnect the integration") from exc
