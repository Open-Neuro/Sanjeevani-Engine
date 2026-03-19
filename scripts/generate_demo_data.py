"""
scripts/generate_demo_data.py  –  SanjeevaniRxAI demo dataset generator

Run from project root:
    python scripts/generate_demo_data.py

Outputs:
  data/demo_consumer_orders.xlsx   ~300 orders, 20 patients, 15 medicines
  data/demo_products.xlsx          15 products (includes 2 low-stock, 2 near-expiry)
"""

import os
import random
from datetime import datetime, timedelta

import pandas as pd

random.seed(42)

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
os.makedirs(OUT_DIR, exist_ok=True)

ORDERS_PATH = os.path.join(OUT_DIR, "demo_consumer_orders.xlsx")
PRODUCTS_PATH = os.path.join(OUT_DIR, "demo_products.xlsx")

# ── 15 Medicines ──────────────────────────────────────────────────────────────
MEDICINES = [
    {
        "name": "Metformin 500mg",
        "generic": "Metformin",
        "cat": "Antidiabetic",
        "price": 5.5,
        "mrp": 6.5,
        "stock": 850,
        "reorder": 100,
        "rx": "Yes",
    },
    {
        "name": "Metformin 1000mg",
        "generic": "Metformin",
        "cat": "Antidiabetic",
        "price": 9.0,
        "mrp": 11.0,
        "stock": 420,
        "reorder": 80,
        "rx": "Yes",
    },
    {
        "name": "Atorvastatin 10mg",
        "generic": "Atorvastatin",
        "cat": "Statin",
        "price": 8.0,
        "mrp": 10.0,
        "stock": 200,
        "reorder": 50,
        "rx": "Yes",
    },
    {
        "name": "Amlodipine 5mg",
        "generic": "Amlodipine",
        "cat": "Antihypertensive",
        "price": 6.0,
        "mrp": 7.5,
        "stock": 310,
        "reorder": 60,
        "rx": "Yes",
    },
    {
        "name": "Paracetamol 500mg",
        "generic": "Paracetamol",
        "cat": "Analgesic",
        "price": 2.0,
        "mrp": 3.0,
        "stock": 1200,
        "reorder": 200,
        "rx": "No",
    },
    {
        "name": "Amoxicillin 500mg",
        "generic": "Amoxicillin",
        "cat": "Antibiotic",
        "price": 12.0,
        "mrp": 15.0,
        "stock": 180,
        "reorder": 40,
        "rx": "Yes",
    },
    {
        "name": "Omeprazole 20mg",
        "generic": "Omeprazole",
        "cat": "PPI",
        "price": 7.0,
        "mrp": 9.0,
        "stock": 90,
        "reorder": 60,
        "rx": "No",
    },
    {
        "name": "Losartan 50mg",
        "generic": "Losartan",
        "cat": "Antihypertensive",
        "price": 11.0,
        "mrp": 13.5,
        "stock": 25,
        "reorder": 50,
        "rx": "Yes",
    },  # LOW STOCK
    {
        "name": "Glimepiride 2mg",
        "generic": "Glimepiride",
        "cat": "Antidiabetic",
        "price": 14.0,
        "mrp": 17.0,
        "stock": 160,
        "reorder": 40,
        "rx": "Yes",
    },
    {
        "name": "Cetirizine 10mg",
        "generic": "Cetirizine",
        "cat": "Antihistamine",
        "price": 3.5,
        "mrp": 4.5,
        "stock": 400,
        "reorder": 80,
        "rx": "No",
    },
    {
        "name": "Aspirin 75mg",
        "generic": "Aspirin",
        "cat": "Antiplatelet",
        "price": 4.0,
        "mrp": 5.0,
        "stock": 500,
        "reorder": 100,
        "rx": "No",
    },
    {
        "name": "Vitamin D3 60000IU",
        "generic": "Cholecalciferol",
        "cat": "Supplement",
        "price": 18.0,
        "mrp": 22.0,
        "stock": 120,
        "reorder": 30,
        "rx": "No",
    },
    {
        "name": "Pantoprazole 40mg",
        "generic": "Pantoprazole",
        "cat": "PPI",
        "price": 9.5,
        "mrp": 12.0,
        "stock": 0,
        "reorder": 50,
        "rx": "No",
    },  # OUT OF STOCK
    {
        "name": "Telmisartan 40mg",
        "generic": "Telmisartan",
        "cat": "Antihypertensive",
        "price": 13.0,
        "mrp": 16.0,
        "stock": 220,
        "reorder": 45,
        "rx": "Yes",
    },
    {
        "name": "Insulin Glargine",
        "generic": "Insulin",
        "cat": "Antidiabetic",
        "price": 850.0,
        "mrp": 950.0,
        "stock": 50,
        "reorder": 20,
        "rx": "Yes",
    },
]

MED_MAP = {m["name"]: m for m in MEDICINES}

# ── 20 Patients ───────────────────────────────────────────────────────────────
PATIENTS = [
    {
        "id": "P001",
        "name": "Ramesh Kumar",
        "age": 58,
        "gender": "Male",
        "phone": "9876543210",
        "diag": "Type 2 Diabetes",
        "channel": "WhatsApp",
        "doctor": "Dr. Sharma",
        "chronic": "Yes",
        "insurance": "StarHealth",
        "meds": ["Metformin 500mg", "Atorvastatin 10mg"],
    },
    {
        "id": "P002",
        "name": "Sunita Devi",
        "age": 62,
        "gender": "Female",
        "phone": "9823456781",
        "diag": "Hypertension",
        "channel": "SMS",
        "doctor": "Dr. Mehta",
        "chronic": "Yes",
        "insurance": "None",
        "meds": ["Amlodipine 5mg", "Losartan 50mg"],
    },
    {
        "id": "P003",
        "name": "Arjun Singh",
        "age": 45,
        "gender": "Male",
        "phone": "9712345678",
        "diag": "Hyperlipidemia",
        "channel": "WhatsApp",
        "doctor": "Dr. Kumar",
        "chronic": "Yes",
        "insurance": "LIC Health",
        "meds": ["Atorvastatin 10mg", "Aspirin 75mg"],
    },
    {
        "id": "P004",
        "name": "Meera Patel",
        "age": 35,
        "gender": "Female",
        "phone": "9845678901",
        "diag": "Seasonal Allergy",
        "channel": "Phone",
        "doctor": "Dr. Joshi",
        "chronic": "No",
        "insurance": "None",
        "meds": ["Cetirizine 10mg"],
    },
    {
        "id": "P005",
        "name": "Vikram Rao",
        "age": 70,
        "gender": "Male",
        "phone": "9934567890",
        "diag": "Type 2 Diabetes",
        "channel": "WhatsApp",
        "doctor": "Dr. Sharma",
        "chronic": "Yes",
        "insurance": "None",
        "meds": ["Metformin 1000mg", "Glimepiride 2mg"],
    },
    {
        "id": "P006",
        "name": "Priya Nair",
        "age": 42,
        "gender": "Female",
        "phone": "9751234567",
        "diag": "GERD",
        "channel": "WhatsApp",
        "doctor": "Dr. Thomas",
        "chronic": "Yes",
        "insurance": "Bajaj",
        "meds": ["Omeprazole 20mg", "Pantoprazole 40mg"],
    },
    {
        "id": "P007",
        "name": "Suresh Yadav",
        "age": 55,
        "gender": "Male",
        "phone": "9867890123",
        "diag": "Hypertension",
        "channel": "SMS",
        "doctor": "Dr. Mehta",
        "chronic": "Yes",
        "insurance": "None",
        "meds": ["Amlodipine 5mg", "Telmisartan 40mg"],
    },
    {
        "id": "P008",
        "name": "Kavitha Iyer",
        "age": 67,
        "gender": "Female",
        "phone": "9890123456",
        "diag": "Type 1 Diabetes",
        "channel": "Phone",
        "doctor": "Dr. Sharma",
        "chronic": "Yes",
        "insurance": "StarHealth",
        "meds": ["Insulin Glargine", "Metformin 500mg"],
    },
    {
        "id": "P009",
        "name": "Rajesh Gupta",
        "age": 50,
        "gender": "Male",
        "phone": "9934567891",
        "diag": "Cardiac Risk",
        "channel": "WhatsApp",
        "doctor": "Dr. Verma",
        "chronic": "Yes",
        "insurance": "None",
        "meds": ["Aspirin 75mg", "Atorvastatin 10mg"],
    },
    {
        "id": "P010",
        "name": "Anita Sharma",
        "age": 29,
        "gender": "Female",
        "phone": "9823456789",
        "diag": "Viral Infection",
        "channel": "Website",
        "doctor": "Dr. Gupta",
        "chronic": "No",
        "insurance": "None",
        "meds": ["Amoxicillin 500mg", "Paracetamol 500mg"],
    },
    {
        "id": "P011",
        "name": "Mohan Lal",
        "age": 73,
        "gender": "Male",
        "phone": "9712312345",
        "diag": "Type 2 Diabetes",
        "channel": "Phone",
        "doctor": "Dr. Sharma",
        "chronic": "Yes",
        "insurance": "LIC Health",
        "meds": ["Metformin 500mg", "Glimepiride 2mg"],
    },
    {
        "id": "P012",
        "name": "Shreya Joshi",
        "age": 38,
        "gender": "Female",
        "phone": "9845671234",
        "diag": "Acid Reflux",
        "channel": "WhatsApp",
        "doctor": "Dr. Thomas",
        "chronic": "No",
        "insurance": "None",
        "meds": ["Omeprazole 20mg"],
    },
    {
        "id": "P013",
        "name": "Deepak Verma",
        "age": 61,
        "gender": "Male",
        "phone": "9756789012",
        "diag": "Hypertension",
        "channel": "SMS",
        "doctor": "Dr. Mehta",
        "chronic": "Yes",
        "insurance": "Bajaj",
        "meds": ["Amlodipine 5mg", "Telmisartan 40mg"],
    },
    {
        "id": "P014",
        "name": "Fatima Sheikh",
        "age": 48,
        "gender": "Female",
        "phone": "9887654321",
        "diag": "Hyperlipidemia",
        "channel": "WhatsApp",
        "doctor": "Dr. Kumar",
        "chronic": "Yes",
        "insurance": "None",
        "meds": ["Atorvastatin 10mg"],
    },
    {
        "id": "P015",
        "name": "Ravi Shankar",
        "age": 54,
        "gender": "Male",
        "phone": "9765432109",
        "diag": "Type 2 Diabetes",
        "channel": "WhatsApp",
        "doctor": "Dr. Sharma",
        "chronic": "Yes",
        "insurance": "StarHealth",
        "meds": ["Metformin 1000mg", "Aspirin 75mg"],
    },
    {
        "id": "P016",
        "name": "Geeta Kumari",
        "age": 44,
        "gender": "Female",
        "phone": "9832109876",
        "diag": "Vitamin Def.",
        "channel": "SMS",
        "doctor": "Dr. Gupta",
        "chronic": "No",
        "insurance": "None",
        "meds": ["Vitamin D3 60000IU"],
    },
    {
        "id": "P017",
        "name": "Anil Kapoor",
        "age": 66,
        "gender": "Male",
        "phone": "9923456701",
        "diag": "Cardiac Risk",
        "channel": "WhatsApp",
        "doctor": "Dr. Verma",
        "chronic": "Yes",
        "insurance": "LIC Health",
        "meds": ["Aspirin 75mg", "Atorvastatin 10mg"],
    },
    {
        "id": "P018",
        "name": "Nirmala Devi",
        "age": 71,
        "gender": "Female",
        "phone": "9891234560",
        "diag": "Type 2 Diabetes",
        "channel": "Phone",
        "doctor": "Dr. Sharma",
        "chronic": "Yes",
        "insurance": "None",
        "meds": ["Metformin 500mg", "Insulin Glargine"],
    },
    {
        "id": "P019",
        "name": "Sanjay Tiwari",
        "age": 39,
        "gender": "Male",
        "phone": "9778901234",
        "diag": "Seasonal Allergy",
        "channel": "Website",
        "doctor": "Dr. Joshi",
        "chronic": "No",
        "insurance": "None",
        "meds": ["Cetirizine 10mg", "Paracetamol 500mg"],
    },
    {
        "id": "P020",
        "name": "Lakshmi Reddy",
        "age": 58,
        "gender": "Female",
        "phone": "9845123456",
        "diag": "Hypertension",
        "channel": "WhatsApp",
        "doctor": "Dr. Mehta",
        "chronic": "Yes",
        "insurance": "StarHealth",
        "meds": ["Amlodipine 5mg", "Losartan 50mg"],
    },
]

DISPENSERS = ["Riya Sharma", "Amit Patel", "Sonal Verma", "Kedar Singh"]


def generate_orders():
    rows = []
    order_num = 1000
    now = datetime.now()

    for patient in PATIENTS:
        is_chronic = patient["chronic"] == "Yes"
        n_orders_per_med = random.randint(5, 8) if is_chronic else random.randint(1, 2)

        for med_name in patient["meds"]:
            med = MED_MAP.get(med_name)
            if not med:
                continue

            for i in range(n_orders_per_med):
                # Space orders ~30 days apart going backwards in time
                days_ago = (i + 1) * 30 + random.randint(-5, 5)
                order_dt = now - timedelta(days=days_ago)
                qty = (
                    random.choice([30, 60, 90])
                    if is_chronic
                    else random.choice([10, 20])
                )
                total = round(qty * med["price"], 2)
                refill_dt = order_dt + timedelta(days=random.randint(25, 35))
                status = random.choices(
                    ["Fulfilled", "Pending", "Cancelled"], weights=[14, 1, 1], k=1
                )[0]
                payment = (
                    "Insurance"
                    if patient["insurance"] != "None"
                    else random.choice(["Cash", "UPI", "Credit Card"])
                )

                rows.append(
                    {
                        "Order ID": f"ORD-{order_num:04d}",
                        "Order Date": order_dt.strftime("%Y-%m-%d"),
                        "Patient ID": patient["id"],
                        "Patient Name": patient["name"],
                        "Age": patient["age"],
                        "Gender": patient["gender"],
                        "Phone Number": patient["phone"],
                        "Diagnosis": patient["diag"],
                        "Doctor Name": patient["doctor"],
                        "Medicine Name": med_name,
                        "Medicine Category": med["cat"],
                        "Generic Name": med["generic"],
                        "Quantity Ordered": qty,
                        "Unit Price": med["price"],
                        "Total Amount": total,
                        "Order Status": status,
                        "Order Channel": patient["channel"],
                        "Payment Method": payment,
                        "Insurance Provider": patient["insurance"],
                        "Refill Due Date": refill_dt.strftime("%Y-%m-%d"),
                        "Prescription Required": med["rx"],
                        "Is Chronic": patient["chronic"],
                        "Dispensed By": random.choice(DISPENSERS),
                        "Notes": "",
                    }
                )
                order_num += 1

    return pd.DataFrame(rows)


def generate_products():
    rows = []
    now = datetime.now()
    for i, med in enumerate(MEDICINES, start=1):
        # Purposely set some near-expiry for demo alerts
        if med["name"] == "Pantoprazole 40mg":
            expiry = (now + timedelta(days=12)).strftime("%Y-%m-%d")
        elif med["name"] == "Amoxicillin 500mg":
            expiry = (now + timedelta(days=40)).strftime("%Y-%m-%d")
        else:
            expiry = (now + timedelta(days=random.randint(180, 730))).strftime(
                "%Y-%m-%d"
            )

        rows.append(
            {
                "Product ID": f"MED{i:03d}",
                "Medicine Name": med["name"],
                "Generic Name": med["generic"],
                "Brand Name": med["name"].split()[0],
                "Manufacturer": random.choice(
                    ["Sun Pharma", "Cipla", "Dr. Reddy's", "Lupin", "Zydus"]
                ),
                "Category": med["cat"],
                "Form": "Injection" if "Insulin" in med["name"] else "Tablet",
                "Strength": (
                    med["name"].split()[-1] if len(med["name"].split()) > 1 else "NA"
                ),
                "Unit Price": med["price"],
                "MRP": med["mrp"],
                "Current Stock": med["stock"],
                "Reorder Level": med["reorder"],
                "Expiry Date": expiry,
                "Batch Number": f"BT{random.randint(2024001, 2024999)}",
                "Supplier Name": random.choice(
                    ["MedSupply Co", "PharmaDistrib", "HealthMart"]
                ),
                "Location": random.choice(
                    ["Shelf A1", "Shelf B2", "Cold Storage", "Shelf C3"]
                ),
                "Requires Prescription": med["rx"],
                "Controlled Substance": "No",
            }
        )

    return pd.DataFrame(rows)


if __name__ == "__main__":
    print("=" * 55)
    print("  SanjeevaniRxAI — Demo Dataset Generator")
    print("=" * 55)

    print("\n Generating orders...")
    orders_df = generate_orders()
    orders_df.to_excel(ORDERS_PATH, index=False, engine="openpyxl")
    print(f"   OK  {len(orders_df)} orders  ->  {ORDERS_PATH}")

    print("\n Generating products...")
    products_df = generate_products()
    products_df.to_excel(PRODUCTS_PATH, index=False, engine="openpyxl")
    print(f"   OK  {len(products_df)} products  ->  {PRODUCTS_PATH}")

    print("\n Data Summary:")
    print(f"   Patients  : {orders_df['Patient ID'].nunique()}")
    print(f"   Medicines : {orders_df['Medicine Name'].nunique()}")
    print(f"   Orders    : {len(orders_df)}")
    print(f"   Revenue   : Rs.{orders_df['Total Amount'].sum():,.2f}")
    print(f"   Channels  : {orders_df['Order Channel'].unique().tolist()}")

    low = products_df[products_df["Current Stock"] <= products_df["Reorder Level"]]
    print(f"\n   Low/Zero Stock  : {low['Medicine Name'].tolist()}")

    print("\n Next — Load into MongoDB:")
    print("   python scripts/load_data.py \\")
    print("     --orders  data/demo_consumer_orders.xlsx \\")
    print("     --products data/demo_products.xlsx --replace")
    print("\n" + "=" * 55)
