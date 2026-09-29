#!/usr/bin/env python3
"""Create all tables. Run once against a fresh Postgres instance.

    python scripts/init_db.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401  (import registers the models on Base)

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("Tables created:", ", ".join(Base.metadata.tables.keys()))
