import os
import sys
import tempfile

# Point the app at a throwaway SQLite file before anything imports app.db.
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
os.environ["DATABASE_URL"] = f"sqlite:///{_db.name}"
os.environ["SHOPIFY_WEBHOOK_SECRET"] = "test_secret"
os.environ.pop("METRICS_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
