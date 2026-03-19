"""
scripts/upload_json_to_atlas.py
"""

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app.database.mongo_client import get_db
from app.modules.data_loader import DataLoader


def excel_date_to_datetime(excel_date):
    if not isinstance(excel_date, (int, float)):
        return datetime.now()
    # Excel dates are days since Dec 30, 1899
    try:
        dt = datetime(1899, 12, 30) + timedelta(days=int(excel_date))
        return dt
    except:
        return datetime.now()


def upload_data():
    db = get_db()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    products_file = os.path.join(base_dir, "producst.json")
    patients_file = os.path.join(base_dir, "paitenetid.json")

    print(f"📦 Connecting to database: {db.name}")

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
            for idx, p in enumerate(raw_products):
                cleaned_product = {
                    "Product ID": p.get("product id") or str(idx),
                    "Medicine Name": p.get("product name"),
                    "Generic Name": p.get("product name"),
                    "Unit Price": p.get("price rec") or 10.0,
                    "Current Stock": 100,
                    "Reorder Level": 20,
                    "Category": "General",
                    "Expiry Date": datetime(2026, 12, 31).isoformat(),
                    "Batch Number": f"BATCH-{idx}",
                    "Supplier Name": "Main Supplier",
                    "pzn": p.get("pzn"),
                    "package size": p.get("package size"),
                    "descriptions": p.get("descriptions", ""),
                }
                cleaned_products.append(cleaned_product)

            if cleaned_products:
                db.products.delete_many({})
                db.products.insert_many(cleaned_products)
                print(f"✅ Inserted {len(cleaned_products)} products.")
    else:
        print(f"❌ Products file not found.")

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
                qty = o.get("Quantity") or 1
                price = o.get("Total Price (EUR)") or 10.0
                cleaned_order = {
                    "Patient ID": o.get("Patient ID"),
                    "Patient Name": o.get(
                        "Patient Name", f"Patient {o.get('Patient ID', 'Unknown')}"
                    ),
                    "Age": o.get("Patient Age"),
                    "Gender": o.get("Patient Gender"),
                    "Order Date": excel_date_to_datetime(o.get("Purchase Date")),
                    "Medicine Name": o.get("Product Name"),
                    "Quantity": qty,
                    "Quantity Ordered": qty,
                    "Total Amount": price,
                    "Unit Price": price / qty if qty > 0 else price,
                    "Diagnosis": "General",
                    "Order Status": "Fulfilled",
                    "Order Channel": "Online",
                    "Payment Method": "Credit Card",
                    "Medicine Category": "General",
                    "Is Chronic": "No",
                    "Contact Number": "123456789",
                    "Address": "Sample Address",
                    "Doctor Name": "Dr. Smith",
                    "Insurance Provider": "AOK",
                }
                cleaned_orders.append(cleaned_order)

            if cleaned_orders:
                db.consumer_orders.delete_many({})
                db.consumer_orders.insert_many(cleaned_orders)
                print(f"✅ Inserted {len(cleaned_orders)} consumer orders.")
    else:
        print(f"❌ Patients file not found.")

    print("Deriving patients and inventory...")
    loader = DataLoader()
    loader.derive_patients_collection()
    loader.initialize_inventory()

    print("\n📊 Atlas Database Summary:")
    for coll in ["products", "consumer_orders", "patients", "inventory"]:
        print(f"   {coll} count: {db[coll].count_documents({})}")


if __name__ == "__main__":
    upload_data()
