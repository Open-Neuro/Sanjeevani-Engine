"""
app/api/orders.py  –  /api/v1/orders
"""

from __future__ import annotations

import os
import requests as req
from datetime import datetime
from typing import Any, Dict, Optional

import csv
import io
from fastapi import APIRouter, Body, HTTPException, Query, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING

from app.database.mongo_client import get_db
from app.modules.safety_validation import SafetyValidationService
from app.utils.logger import get_logger
from app.utils.security import get_current_user

router = APIRouter(prefix="/orders", tags=["Orders"])
logger = get_logger(__name__)
_safety = SafetyValidationService()


@router.get("/", summary="List all orders")
def list_orders(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    patient_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    medicine: Optional[str] = Query(default=None),
    channel: Optional[str] = Query(default=None),
    sort_by: str = Query(default="Order Date"),
    sort_order: str = Query(default="desc", regex="^(asc|desc)$"),
    user: dict = Depends(get_current_user),
):
    """
    Paginated order list with filters for:
    patient_id, status, medicine, channel.
    """
    db = get_db()
    query: dict = {"merchant_id": user["merchant_id"]}

    if patient_id:
        query["$or"] = [
            {"Patient ID": {"$regex": patient_id, "$options": "i"}},
            {"Patient Name": {"$regex": patient_id, "$options": "i"}},
        ]
    if status:
        query["Order Status"] = {"$regex": status, "$options": "i"}
    if medicine:
        query["Medicine Name"] = {"$regex": medicine, "$options": "i"}
    if channel:
        query["Order Channel"] = {"$regex": channel, "$options": "i"}

    skip = (page - 1) * page_size
    sort_dir = ASCENDING if sort_order == "asc" else DESCENDING
    total = db["consumer_orders"].count_documents(query)
    items = list(
        db["consumer_orders"]
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


@router.get("/export/csv", summary="Export orders as CSV")
def export_orders_csv(user: dict = Depends(get_current_user)):
    """
    Export all orders for the current merchant as a CSV file.
    Includes patient details, medicine info, amounts, and dates.
    """
    db = get_db()
    
    # 1. Fetch all orders for this merchant, sorted by date (descending)
    query = {"merchant_id": user["merchant_id"]}
    orders_cursor = db["consumer_orders"].find(query).sort("Order Date", DESCENDING)
    
    # 2. Use io.StringIO to create an in-memory string buffer for the CSV
    output = io.StringIO()
    writer = csv.writer(output)
    
    # 3. Write CSV Header
    header = [
        "Order ID", "Patient Name", "Medicine Name", "Quantity", 
        "Total Amount", "Order Status", "Order Date", "Order Channel"
    ]
    writer.writerow(header)
    
    # 4. Write Data Rows
    for order in orders_cursor:
        writer.writerow([
            order.get("Order ID", ""),
            order.get("Patient Name", ""),
            order.get("Medicine Name", ""),
            order.get("Quantity Ordered", order.get("Quantity", "")),
            order.get("Total Amount", ""),
            order.get("Order Status", ""),
            order.get("Order Date", ""),
            order.get("Order Channel", "")
        ])
        
    # 5. Reset buffer position to start
    output.seek(0)
    
    # 6. Return StreamingResponse with CSV headers
    # We use a generator to yield the content from the buffer
    def iter_csv():
        yield output.getvalue()
        
    filename = f"sanjeevani_orders_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    
    response = StreamingResponse(iter_csv(), media_type="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    
    return response


@router.get("/stats", summary="Order statistics summary")
def order_stats(user: dict = Depends(get_current_user)):
    """Aggregate counts by status, channel and payment method."""
    db = get_db()

    def _agg(field: str):
        return [
            {"label": r["_id"] or "Unknown", "count": r["count"]}
            for r in db["consumer_orders"].aggregate(
                [
                    {"$match": {"merchant_id": user["merchant_id"]}},
                    {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ]
            )
        ]

    return {
        "status": "ok",
        "data": {
            "by_status": _agg("Order Status"),
            "by_channel": _agg("Order Channel"),
            "by_payment": _agg("Payment Method"),
            "total": db["consumer_orders"].count_documents({"merchant_id": user["merchant_id"]}),
        },
    }


class ValidateOrderRequest(BaseModel):
    patient_id: str
    medicine_name: str
    quantity: float = Field(..., gt=0)
    prescription_provided: bool = False


@router.post("/validate", summary="Validate an order before placing")
def validate_order(body: ValidateOrderRequest, user: dict = Depends(get_current_user)):
    """
    Run all safety checks on a proposed order.
    Returns ``is_valid``, individual check results, and a summary.
    """
    try:
        result = _safety.validate_order(
            body.patient_id,
            body.medicine_name,
            body.quantity,
            prescription_provided=body.prescription_provided,
        )
        return {"status": "ok", "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{order_id}", summary="Get a single order by Order ID")
def get_order(order_id: str, user: dict = Depends(get_current_user)):
    """Fetch one order record by its ``Order ID`` field."""
    db = get_db()
    order = db["consumer_orders"].find_one(
        {"Order ID": order_id, "merchant_id": user["merchant_id"]}, 
        {"_id": 0}
    )
    if not order:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found.")
    return {"status": "ok", "data": order}


class UpdateOrderStatusRequest(BaseModel):
    status: str


@router.patch("/{order_id}/status", summary="Update order status (Approve/Reject)")
def update_order_status(order_id: str, body: UpdateOrderStatusRequest, user: dict = Depends(get_current_user)):
    """
    Update the status of an order.
    If status is 'Completed' or 'Validated', it checks inventory and deducts stock.
    """
    db = get_db()

    order = db["consumer_orders"].find_one({"Order ID": order_id})
    if not order:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found.")

    medicine_name = order.get("Medicine Name", "")
    quantity = float(order.get("Quantity Ordered", order.get("Quantity", 1)))
    new_status = body.status

    if new_status in ["Completed", "Validated"]:
        product = db["products"].find_one(
            {
                "Medicine Name": {"$regex": f"^{medicine_name}$", "$options": "i"},
                "merchant_id": user["merchant_id"]
            }
        )
        if product:
            current_stock = float(product.get("Current Stock", 0))
            if current_stock < quantity:
                db["consumer_orders"].update_one(
                    {"Order ID": order_id},
                    {
                        "$set": {
                            "Order Status": "Rejected",
                            "Notes": "Insufficient Stock",
                        }
                    },
                )
                return {
                    "status": "error",
                    "message": f"Insufficient stock for {medicine_name}. Current: {current_stock}, Pending: {quantity}. Order rejected.",
                }
            else:
                new_stock = current_stock - quantity
                db["products"].update_one(
                    {"_id": product["_id"]}, {"$set": {"Current Stock": new_stock}}
                )

    db["consumer_orders"].update_one(
        {"Order ID": order_id},
        {"$set": {"Order Status": new_status, "updated_at": datetime.utcnow()}},
    )

    return {
        "status": "ok",
        "message": f"Order {order_id} status updated to {new_status}",
    }


@router.post(
    "/{order_id}/confirm",
    summary="Pharmacist confirms & dispatches order — notifies patient",
)
def confirm_and_dispatch_order(order_id: str, user: dict = Depends(get_current_user)):
    """
    Called when pharmacist clicks 'Place Order' in the dashboard UI.

    1. Validates the order is still pending.
    2. Marks it as 'Completed' in the DB.
    3. Sends a WhatsApp or Telegram confirmation directly to the patient.
    """
    db = get_db()

    # Find the pending order specifically — this avoids false collisions when
    # two orders share the same ID (old bug) where find_one might return a
    # Rejected/Completed one first.
    order = db["consumer_orders"].find_one(
        {
            "Order ID": order_id,
            "merchant_id": user["merchant_id"],
            "Order Status": {"$nin": ["Completed", "Delivered", "Rejected"]},
        }
    )

    if not order:
        # Fall back: check if it exists at all so we can give a clearer error
        exists = db["consumer_orders"].find_one({"Order ID": order_id, "merchant_id": user["merchant_id"]})
        if exists:
            current = exists.get("Order Status", "Unknown")
            raise HTTPException(
                status_code=400,
                detail=f"Order is already '{current}' and cannot be re-confirmed.",
            )
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found.")

    medicine_name = order.get("Medicine Name", "Unknown Medicine")
    quantity = order.get("Quantity Ordered", order.get("Quantity", 1))
    patient_name = order.get("Patient Name", "Customer")
    contact_number = str(order.get("Contact Number", "")).strip()
    channel = (order.get("Order Channel") or "").lower()

    # Mark as Completed in DB
    db["consumer_orders"].update_one(
        {"Order ID": order_id},
        {"$set": {"Order Status": "Completed", "updated_at": datetime.utcnow()}},
    )

    # Compose confirmation message
    confirmation_msg = (
        f"✅ *Order Confirmed & Dispatched!*\n\n"
        f"Hi {patient_name}! 👋\n\n"
        f"Your order has been *approved by our pharmacist* and is being dispatched!\n\n"
        f"📦 *Order Details:*\n"
        f"• Medicine: {medicine_name}\n"
        f"• Quantity: {quantity}\n"
        f"• Order ID: #{order_id}\n\n"
        f"Thank you for choosing SanjeevaniRxAI Pharmacy! 🏥"
    )

    notification_sent = False
    notification_channel = "none"

    # Try WhatsApp
    if "whatsapp" in channel and contact_number:
        WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
        PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
        if WHATSAPP_TOKEN and PHONE_NUMBER_ID:
            WA_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
            headers = {
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": contact_number,
                "text": {"body": confirmation_msg},
            }
            try:
                resp = req.post(WA_URL, headers=headers, json=payload, timeout=10)
                if resp.status_code in [200, 201]:
                    notification_sent = True
                    notification_channel = "WhatsApp"
                    logger.info(
                        f"WhatsApp confirmation sent to {contact_number} for order {order_id}"
                    )
                else:
                    logger.warning(
                        f"WhatsApp send returned {resp.status_code}: {resp.text}"
                    )
            except Exception as e:
                logger.error(f"WhatsApp send error: {e}")

    # Try Telegram
    elif "telegram" in channel and contact_number:
        TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
        if TELEGRAM_BOT_TOKEN:
            TG_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            try:
                resp = req.post(
                    TG_URL,
                    json={
                        "chat_id": contact_number,
                        "text": confirmation_msg,
                        "parse_mode": "Markdown",
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    notification_sent = True
                    notification_channel = "Telegram"
                    logger.info(
                        f"Telegram confirmation sent to {contact_number} for order {order_id}"
                    )
                else:
                    logger.warning(
                        f"Telegram send returned {resp.status_code}: {resp.text}"
                    )
            except Exception as e:
                logger.error(f"Telegram send error: {e}")

    logger.info(
        f"Order {order_id} confirmed. Notification via {notification_channel}: {notification_sent}"
    )

    return {
        "status": "ok",
        "message": f"Order {order_id} confirmed and dispatched successfully.",
        "notification_sent": notification_sent,
        "notification_channel": notification_channel,
        "order_id": order_id,
    }
