#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/load_data.py  -  SanjeevaniRxAI End-to-end data ingestion script.

Usage:
    python scripts/load_data.py \
        --orders  data/consumer_orders.xlsx \
        --products data/products.xlsx \
        [--replace]      # drop existing docs before loading
        [--skip-indexes] # skip index creation
"""

from __future__ import annotations

import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.utils.logger import setup_logging, get_logger
from app.modules.data_loader import DataLoader

setup_logging()
logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load Excel data into MongoDB for SanjeevaniRxAI"
    )
    parser.add_argument(
        "--orders", required=True, help="Path to Consumer Orders (.xlsx)"
    )
    parser.add_argument("--products", required=True, help="Path to Products (.xlsx)")
    parser.add_argument(
        "--replace",
        action="store_true",
        default=False,
        help="Drop existing docs before loading",
    )
    parser.add_argument(
        "--skip-indexes", action="store_true", default=False, help="Skip index creation"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loader = DataLoader()

    print("=" * 55)
    print("  SanjeevaniRxAI - Data Loader")
    print("=" * 55)

    # Step 1: Load raw Excel sheets
    logger.info("Step 1: Loading consumer orders from %s", args.orders)
    orders_count = loader.load_consumer_orders(args.orders, replace=args.replace)

    logger.info("Step 2: Loading products from %s", args.products)
    products_count = loader.load_products(args.products, replace=args.replace)

    # Step 2: Derive patients
    logger.info("Step 3: Deriving patients from orders...")
    patients_count = loader.derive_patients_collection()

    # Step 3: Seed inventory
    logger.info("Step 4: Seeding inventory from products...")
    inventory_count = loader.initialize_inventory()

    # Step 4: Indexes
    if not args.skip_indexes:
        logger.info("Step 5: Creating indexes...")
        loader.create_indexes()

    # Step 5: Validate
    logger.info("Step 6: Validating data integrity...")
    report = loader.validate_data_integrity()

    # Summary
    print("\n" + "=" * 55)
    print("  Load Summary")
    print("=" * 55)
    print(f"  Consumer orders  : {orders_count}")
    print(f"  Products         : {products_count}")
    print(f"  Patients derived : {patients_count}")
    print(f"  Inventory seeded : {inventory_count}")
    print(f"  Integrity passed : {report.get('validation_passed')}")
    print("=" * 55)

    if not report.get("validation_passed"):
        print("ERROR: Data integrity validation FAILED.")
        sys.exit(1)

    print("  Data load completed successfully!")
    print("=" * 55)


if __name__ == "__main__":
    main()
