"""Store identity helpers."""
from __future__ import annotations

import os
import re

# Where untagged rows (everything recorded before stores were tagged) and
# untagged test runs belong: the icelabs dev store.
DEFAULT_SHOP = os.getenv("DEFAULT_SHOP", "icelabs-bdy57pfy.myshopify.com")

# Reserved name: the metrics API serves the synthetic dataset for it.
DEMO_SHOP = "demo"

_SHOP_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,252}$")


def normalise_shop(value: str | None) -> str | None:
    """'https://www.Example.com.au:443/x' -> 'example.com.au'. Returns None
    for anything that isn't a plausible hostname, so junk never becomes a
    store."""
    if not value:
        return None
    v = value.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v).split("/")[0].split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v if _SHOP_RE.match(v) and v != DEMO_SHOP else None
