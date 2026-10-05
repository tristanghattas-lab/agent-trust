#!/usr/bin/env python3
"""
Print a store's edge key and the commands to configure its Worker.

    EDGE_SIGNING_SECRET=... python -m scripts.edge_key icelabs-bdy57pfy.myshopify.com

Run it wherever EDGE_SIGNING_SECRET is set (the same value as on the API).
The key is derived, not stored: the same store always gets the same key.
"""
import sys

from app.edge import edge_key
from app.shops import normalise_shop


def main() -> None:
    if len(sys.argv) != 2 or not normalise_shop(sys.argv[1]):
        sys.exit("usage: python -m scripts.edge_key <store>.myshopify.com")
    shop = normalise_shop(sys.argv[1])
    key = edge_key(shop)
    print(f"Store:     {shop}\nEdge key:  {key}\n")
    print("In edge/ (with wrangler logged in to the merchant's Cloudflare account):")
    print(f'  set AGENT_TRUST_SHOP = "{shop}" in wrangler.toml, and the routes to the store domain')
    print("  npx wrangler secret put AGENT_TRUST_EDGE_KEY   # paste the key above")
    print("  npx wrangler secret put IP_SALT                # any long random string")
    print("  npx wrangler deploy")


if __name__ == "__main__":
    main()
