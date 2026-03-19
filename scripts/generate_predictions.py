#!/usr/bin/env python3
"""
scripts/generate_predictions.py
─────────────────────────────────────────────────────────────────────────────
Run the batch refill prediction pipeline across all patient-medicine pairs.

Usage
─────
    python scripts/generate_predictions.py [--min-orders N]

Run from the project root:
    cd "SanjeevaniRxAI System"
    python scripts/generate_predictions.py
"""

from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.logger import setup_logging, get_logger
from app.modules.refill_prediction import RefillPredictionService

setup_logging()
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate batch refill predictions for all patients"
    )
    parser.add_argument(
        "--min-orders",
        type=int,
        default=2,
        help="Minimum order count required to generate a prediction (default: 2)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logger.info("═" * 60)
    logger.info("SanjeevaniRxAI — Batch Prediction Runner")
    logger.info("Min orders threshold: %d", args.min_orders)
    logger.info("═" * 60)

    svc = RefillPredictionService()
    summary = svc.batch_predict_all_patients()

    logger.info("═" * 60)
    logger.info("Prediction Summary")
    logger.info("  Total pairs      : %d", summary["total_pairs"])
    logger.info("  Predictions OK   : %d", summary["predictions_ok"])
    logger.info("  Failed           : %d", summary["failed"])
    logger.info("  High-risk alerts : %d", summary["high_risk"])
    logger.info("  Completed at     : %s", summary["completed_at"])
    logger.info("═" * 60)

    if summary["failed"] > 0:
        logger.warning("%d predictions failed — check logs.", summary["failed"])


if __name__ == "__main__":
    main()
