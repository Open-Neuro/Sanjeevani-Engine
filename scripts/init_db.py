#!/usr/bin/env python3
"""
scripts/init_db.py
─────────────────────────────────────────────────────────────────────────────
One-time database initialisation:
  1. Creates all required indexes
  2. Validates connectivity

Run from project root:
    python scripts/init_db.py
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.logger import setup_logging, get_logger
from app.database.mongo_client import health_check
from app.modules.data_loader import DataLoader

setup_logging()
logger = get_logger(__name__)


def main() -> None:
    logger.info("SanjeevaniRxAI — DB Init")

    health = health_check()
    if health["status"] != "ok":
        logger.critical("MongoDB not reachable: %s", health.get("detail"))
        sys.exit(1)

    logger.info("MongoDB health: OK (%.1f ms)", health.get("latency_ms", 0))

    logger.info("Creating indexes…")
    loader = DataLoader()
    loader.create_indexes()

    logger.info("DB initialisation complete ✓")


if __name__ == "__main__":
    main()
