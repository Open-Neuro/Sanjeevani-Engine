"""
scripts/upload_to_atlas.py
──────────────────────────────────────────────────────────────────────────────
Uploads local JSON data (producst.json + paitenetid.json) to MongoDB Atlas.

Run from project root:
    python scripts/upload_to_atlas.py
"""

import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.database.mongo_client import get_db


def upload_data():
    db = get_db()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    products_file = os.path.join(base_dir, "producst.json")
    patients_file = os.path.join(base_dir, "paitenetid.json")

    print(f"📦 Connecting to database: {db.name}")

    # ── 1. Load Products ──────────────────────────────────────────────────────
    if os.path.exists(products_file):
        with open(products_file, "r", encoding="utf-8") as f:
            products_data = json.load(f)

        if (
            isinstance(products_data, list)
            and len(products_data) > 0
            and "data" in products_data[0]
        ):
            raw_products = products_data[0]["data"]
            cleaned_products = []
            for p in raw_products:
                cleaned_product = {
                    "product_id": p.get("product id"),
                    "product_name": p.get("product name"),
                    "pzn": p.get("pzn"),
                    "price_rec": p.get("price rec"),
                    "package_size": p.get("package size"),
                    "description": p.get("descriptions", ""),
                    "descriptions": p.get("descriptions", ""),
                }
                cleaned_products.append(cleaned_product)

            if cleaned_products:
                db.products.delete_many({})
                result = db.products.insert_many(cleaned_products)
                print(
                    f"✅ Inserted {len(result.inserted_ids)} products into Atlas [{db.name}.products]"
                )
            else:
                print("⚠️  No products found to insert.")
        else:
            print("⚠️  Unexpected products.json format.")
    else:
        print(f"❌ Products file not found: {products_file}")

    # ── 2. Load Consumer Orders (patients) ──────────────────────────────────
    if os.path.exists(patients_file):
        with open(patients_file, "r", encoding="utf-8") as f:
            patients_data = json.load(f)

        if (
            isinstance(patients_data, list)
            and len(patients_data) > 0
            and "data" in patients_data[0]
        ):
            raw_orders = patients_data[0]["data"]
            cleaned_orders = []
            for o in raw_orders:
                cleaned_order = {
                    "patient_id": o.get("Patient ID"),
                    "patient_name": o.get("Patient Name", ""),
                    "patient_age": o.get("Patient Age"),
                    "patient_gender": o.get("Patient Gender"),
                    "purchase_date": o.get("Purchase Date"),
                    "product_name": o.get("Product Name"),
                    "quantity": o.get("Quantity"),
                    "total_price_eur": o.get("Total Price (EUR)"),
                    "dosage_frequency": o.get("Dosage Frequency"),
                    "prescription_required": o.get("Prescription Required"),
                }
                cleaned_orders.append(cleaned_order)

            if cleaned_orders:
                db.consumer_orders.delete_many({})
                result = db.consumer_orders.insert_many(cleaned_orders)
                print(
                    f"✅ Inserted {len(result.inserted_ids)} consumer orders into Atlas [{db.name}.consumer_orders]"
                )
            else:
                print("⚠️  No orders found to insert.")
        else:
            print("⚠️  Unexpected paitenetid.json format.")
    else:
        print(f"❌ Patients file not found: {patients_file}")

    # ── 3. Summary ───────────────────────────────────────────────────────────
    print("\n📊 Atlas Database Summary:")
    print(f"   products count       : {db.products.count_documents({})}")
    print(f"   consumer_orders count: {db.consumer_orders.count_documents({})}")
    print("\n🎉 Data upload complete! The dashboard should now show real data.")


if __name__ == "__main__":
    upload_data()
