"""
app/api/products.py  –  /api/v1/products
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pymongo import ASCENDING, DESCENDING

from pydantic import BaseModel
from app.database.mongo_client import get_db
from app.modules.inventory_intelligence import InventoryIntelligenceService
from app.utils.logger import get_logger

router = APIRouter(prefix="/products", tags=["Products"])
logger = get_logger(__name__)
_inv = InventoryIntelligenceService()


class ProductCreate(BaseModel):
    medicine_name: str
    category: Optional[str] = "General"
    stock: Optional[int] = 0
    generic_name: Optional[str] = None
    brand_name: Optional[str] = None
    batch_no: Optional[str] = None
    expiry_date: Optional[str] = None
    mrp: Optional[float] = 0.0
    selling_price: Optional[float] = 0.0
    schedule: Optional[str] = "OTC"
    prescription_required: Optional[bool] = False


@router.post("/", summary="Add a new product")
def add_product(product: ProductCreate):
    """Manually add a product to the catalog."""
    db = get_db()

    # Check for duplicates
    existing = db["products"].find_one(
        {"Medicine Name": {"$regex": f"^{product.medicine_name}$", "$options": "i"}}
    )
    if existing:
        raise HTTPException(
            status_code=400, detail=f"Product '{product.medicine_name}' already exists."
        )

    new_doc = {
        "Medicine Name": product.medicine_name,
        "Category": product.category,
        "Current Stock": product.stock,
        "Generic Name": product.generic_name,
        "Brand Name": product.brand_name,
        "Batch Number": product.batch_no,
        "Expiry Date": product.expiry_date,
        "MRP": product.mrp,
        "Selling Price": product.selling_price,
        "Schedule": product.schedule,
        "Prescription Required": product.prescription_required,
        "Product ID": f"M-{db['products'].count_documents({}) + 1000}",
        "Safety Check": "Validated",
        "last_updated": datetime.utcnow(),
    }

    db["products"].insert_one(new_doc)
    return {
        "status": "ok",
        "message": "Product added successfully",
        "product_id": new_doc["Product ID"],
    }


@router.post("/bulk", summary="Bulk add products")
def bulk_add_products(products: list[ProductCreate]):
    """Bulk add products to the catalog."""
    db = get_db()
    count = db["products"].count_documents({})

    new_docs = []
    for i, p in enumerate(products):
        # Check for duplicates (optional but good for safety)
        existing = db["products"].find_one(
            {"Medicine Name": {"$regex": f"^{p.medicine_name}$", "$options": "i"}}
        )
        if existing:
            continue  # Skip existing

        new_docs.append({
            "Medicine Name": p.medicine_name,
            "Category": p.category,
            "Current Stock": p.stock,
            "Generic Name": p.generic_name,
            "Brand Name": p.brand_name,
            "Batch Number": p.batch_no,
            "Expiry Date": p.expiry_date,
            "MRP": p.mrp,
            "Selling Price": p.selling_price,
            "Schedule": p.schedule,
            "Prescription Required": p.prescription_required,
            "Product ID": f"M-{count + 1000 + len(new_docs)}",
            "Safety Check": "Validated",
            "last_updated": datetime.utcnow(),
        })

    if new_docs:
        db["products"].insert_many(new_docs)

    return {
        "status": "ok",
        "message": f"Successfully added {len(new_docs)} products. {len(products) - len(new_docs)} skipped (duplicates).",
    }


@router.get("/", summary="List all products")
def list_products(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    sort_by: str = Query(default="Medicine Name"),
    sort_order: str = Query(default="asc", regex="^(asc|desc)$"),
):
    """Paginated product catalogue with search + category filter."""
    db = get_db()
    query: dict = {}
    if search:
        query["$or"] = [
            {"Medicine Name": {"$regex": search, "$options": "i"}},
            {"Generic Name": {"$regex": search, "$options": "i"}},
            {"Brand Name": {"$regex": search, "$options": "i"}},
        ]
    if category:
        query["Category"] = {"$regex": category, "$options": "i"}

    skip = (page - 1) * page_size
    sort_dir = ASCENDING if sort_order == "asc" else DESCENDING
    total = db["products"].count_documents(query)
    items = list(
        db["products"]
        .find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip(skip)
        .limit(page_size)
    )
    return {
        "status": "ok",
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": -(-total // page_size),
        "data": items,
    }


@router.get("/low-stock", summary="Low-stock items")
def low_stock():
    """Return products where current stock is < average weekly sales"""
    db = get_db()

    # Calculate average weekly sales from consumer_orders
    orders = list(db["consumer_orders"].find())
    product_sales = {}
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    for o in orders:
        p_name = o.get("product_name") or o.get("Medicine Name")
        if not p_name:
            continue

        try:
            qty = float(o.get("quantity") or o.get("Quantity", 1))
        except (ValueError, TypeError):
            qty = 1.0

        raw_date = o.get("purchase_date") or o.get("Order Date")

        dt = None
        if isinstance(raw_date, (int, float)):
            dt = datetime(1899, 12, 30) + timedelta(days=float(raw_date))
        elif isinstance(raw_date, datetime):
            dt = raw_date
        elif isinstance(raw_date, str):
            try:
                dt = datetime.fromisoformat(str(raw_date))
            except ValueError:
                dt = now
        else:
            dt = now

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        if p_name not in product_sales:
            product_sales[p_name] = {"total": 0, "min_dt": dt, "max_dt": dt}

        product_sales[p_name]["total"] += qty
        if dt < product_sales[p_name]["min_dt"]:
            product_sales[p_name]["min_dt"] = dt
        if dt > product_sales[p_name]["max_dt"]:
            product_sales[p_name]["max_dt"] = dt

    weekly_sales = {}
    for p_name, data in product_sales.items():
        days = (data["max_dt"] - data["min_dt"]).days
        weeks = max(days / 7.0, 1.0)
        weekly_sales[p_name] = data["total"] / weeks

    low_stock_items = []
    items = list(db["inventory"].find())
    if not items:
        items = list(db["products"].find())

    for item in items:
        name = item.get("medicine_name") or item.get("product_name")
        if not name:
            continue

        avg_weekly = weekly_sales.get(name, 0)
        stock = float(item.get("current_stock") or 0)

        if stock < avg_weekly:
            item["urgency"] = "critical" if stock == 0 else "high"
            item["avg_weekly_sales"] = avg_weekly
            item["_id"] = str(item.get("_id", ""))
            low_stock_items.append(item)

    return {"status": "ok", "data": low_stock_items}


@router.get("/expiry-risk", summary="Expiry risk items")
def expiry_risk(days: int = Query(default=90, ge=1, le=365)):
    """Return products that have more stock than can be sold before expiry based on avg weekly sales."""
    db = get_db()
    orders = list(db["consumer_orders"].find())

    product_sales = {}
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    for o in orders:
        p_name = o.get("product_name") or o.get("Medicine Name")
        if not p_name:
            continue

        try:
            qty = float(o.get("quantity") or o.get("Quantity", 1))
        except (ValueError, TypeError):
            qty = 1.0

        raw_date = o.get("purchase_date") or o.get("Order Date")

        dt = None
        if isinstance(raw_date, (int, float)):
            dt = datetime(1899, 12, 30) + timedelta(days=float(raw_date))
        elif isinstance(raw_date, datetime):
            dt = raw_date
        elif isinstance(raw_date, str):
            try:
                dt = datetime.fromisoformat(str(raw_date))
            except ValueError:
                dt = now
        else:
            dt = now

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        if p_name not in product_sales:
            product_sales[p_name] = {"total": 0, "min_dt": dt, "max_dt": dt}

        product_sales[p_name]["total"] += qty
        if dt < product_sales[p_name]["min_dt"]:
            product_sales[p_name]["min_dt"] = dt
        if dt > product_sales[p_name]["max_dt"]:
            product_sales[p_name]["max_dt"] = dt

    weekly_sales = {}
    for p_name, data in product_sales.items():
        days_span = (data["max_dt"] - data["min_dt"]).days
        weeks = max(days_span / 7.0, 1.0)
        weekly_sales[p_name] = data["total"] / weeks

    risk_items = []
    items = list(db["inventory"].find())
    if not items:
        items = list(db["products"].find())

    for item in items:
        name = item.get("medicine_name") or item.get("product_name")
        if not name:
            continue

        stock = float(item.get("current_stock") or 0)
        if stock <= 0:
            continue

        exp_raw = item.get("expiry_date")
        if not exp_raw:
            continue

        try:
            if isinstance(exp_raw, str):
                exp_dt = datetime.fromisoformat(exp_raw.replace("/", "-"))
            elif isinstance(exp_raw, datetime):
                exp_dt = exp_raw
            else:
                continue
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)

            days_left = (exp_dt - now).days
            if days_left <= 0:
                item["urgency"] = "critical"
                item["_id"] = str(item.get("_id", ""))
                risk_items.append(item)
                continue

            weeks_left = days_left / 7.0
            avg_weekly = weekly_sales.get(name, 0)

            projected_sales = weeks_left * avg_weekly
            if stock > projected_sales:
                item["urgency"] = "high"
                item["projected_sales"] = projected_sales
                item["days_until_expiry"] = days_left
                item["_id"] = str(item.get("_id", ""))
                risk_items.append(item)

        except (ValueError, TypeError):
            continue

    return {"status": "ok", "data": risk_items}


@router.get("/reorder-recommendations", summary="Reorder recommendations")
def reorder_recommendations():
    """Recommended restocking quantities for all low-stock items."""
    return {"status": "ok", "data": _inv.get_reorder_recommendations()}


@router.get("/movement-patterns", summary="Sales velocity classification")
def movement_patterns():
    """Classify products as fast / medium / slow / no movement."""
    return {"status": "ok", "data": _inv.analyze_movement_patterns()}


@router.get("/{product_id}", summary="Get single product")
def get_product(product_id: str):
    """Fetch one product by Product ID or Medicine Name."""
    db = get_db()
    prod = db["products"].find_one(
        {"$or": [{"Product ID": product_id}, {"Medicine Name": product_id}]},
        {"_id": 0},
    )
    if not prod:
        raise HTTPException(
            status_code=404, detail=f"Product '{product_id}' not found."
        )
    return {"status": "ok", "data": prod}


@router.get("/{product_id}/forecast", summary="Demand forecast")
def demand_forecast(product_id: str, days: int = Query(default=30, ge=1, le=365)):
    """SMA-based demand forecast for a product."""
    try:
        data = _inv.forecast_demand(product_id, days=days)
        return {"status": "ok", "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{product_id}/trend", summary="Demand trend")
def demand_trend(product_id: str):
    """Monthly demand trend (increasing / stable / decreasing)."""
    try:
        return {"status": "ok", "data": _inv.analyze_demand_trend(product_id)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
