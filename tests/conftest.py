import os
import sys
import tempfile

# Point the app at a throwaway SQLite file before anything imports app.db.
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["SHOPIFY_WEBHOOK_SECRET"] = "test_secret"
os.environ.pop("METRICS_API_KEY", None)
os.environ["ORDER_TAGGING"] = "off"  # never call the Shopify app from tests
os.environ["ALLOWED_SHOPS"] = "*"      # tests ingest for made-up stores
os.environ["RATE_LIMITS"] = "off"      # rate limits have their own tests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
