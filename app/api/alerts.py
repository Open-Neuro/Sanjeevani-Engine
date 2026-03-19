from __future__ import annotations

"""
app/api/alerts.py  –  /api/v1/alerts
"""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, Body, HTTPException, Path, Query
from fastapi.params import Query as QueryParam
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING

from app.database.mongo_client import get_db
from app.modules.inventory_intelligence import InventoryIntelligenceService
from app.modules.safety_validation import SafetyValidationService
from app.modules.proactive_outreach import ProactiveOutreachService
from app.utils.logger import get_logger

router = APIRouter(prefix="/alerts", tags=["Alerts"])
logger = get_logger(__name__)
_inv_svc = InventoryIntelligenceService()
_saf_svc = SafetyValidationService()
_outreach_svc = ProactiveOutreachService()


@router.get("/", summary="List alerts")
def list_alerts(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    alert_type: Optional[str] = Query(
        None,
        description="refill_due | low_stock | expiry_risk | interaction_warning | proactive_outreach",
    ),
    severity: Optional[str] = Query(None, regex="^(low|medium|high|critical)$"),
    is_resolved: Optional[bool] = Query(None),
    patient_id: Optional[str] = Query(None),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
):
    """
    Paginated alert list with filtering by type, severity, resolution
    status, and patient.
    """

    # Coerce Query parameters to their actual values if they are QueryParam objects
    # This handles cases where the function might be called internally with QueryParam objects
    # instead of the resolved values.
    def _res(v, fallback):
        if hasattr(v, "default"):
            return v.default if v.default is not ... else fallback
        return v

    p = int(_res(page, 1))
    ps = int(_res(page_size, 20))
    alert_type = _res(alert_type, None)
    severity = _res(severity, None)
    is_resolved = _res(is_resolved, None)
    patient_id = _res(patient_id, None)
    sort_by = _res(sort_by, "created_at")
    sort_order = _res(sort_order, "desc")

    db = get_db()
    query: dict = {}
    if alert_type:
        query["alert_type"] = alert_type
    if severity:
        query["severity"] = severity
    if is_resolved is not None:
        query["is_resolved"] = is_resolved
    if patient_id:
        query["patient_id"] = {"$regex": patient_id, "$options": "i"}

    # Redundant but kept for safety in case of non-int strings from API
    skip = (p - 1) * ps

    sort_dir = ASCENDING if sort_order == "asc" else DESCENDING
    total = db["alerts"].count_documents(query)
    items = list(
        db["alerts"]
        .find(query, {"_id": 0})
        .sort(sort_by, sort_dir)
        .skip(skip)
        .limit(ps)
    )
    return {
        "status": "ok",
        "page": p,
        "page_size": ps,
        "total": total,
        "total_pages": -(-total // ps),
        "data": items,
    }


@router.get("/refills", summary="Get refill alerts")
def get_refill_alerts():
    """Shortcut for refill_due alerts."""
    return list_alerts(page=1, page_size=100, alert_type="refill_due")


@router.get("/inventory", summary="Get inventory alerts")
def get_inventory_alerts():
    """Shortcut for low_stock alerts."""
    return list_alerts(page=1, page_size=100, alert_type="low_stock")


@router.get("/summary", summary="Alert counts by type and severity")
def alert_summary():
    """Quick telemetry: counts grouped by alert_type and severity."""
    db = get_db()

    by_type = list(
        db["alerts"].aggregate(
            [
                {
                    "$group": {
                        "_id": "$alert_type",
                        "count": {"$sum": 1},
                        "unresolved": {
                            "$sum": {"$cond": [{"$eq": ["$is_resolved", False]}, 1, 0]}
                        },
                    }
                },
                {"$sort": {"count": -1}},
            ]
        )
    )
    by_severity = list(
        db["alerts"].aggregate(
            [
                {"$group": {"_id": "$severity", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]
        )
    )
    total_unresolved = db["alerts"].count_documents({"is_resolved": False})

    return {
        "status": "ok",
        "data": {
            "total_unresolved": total_unresolved,
            "by_type": [
                {"type": r["_id"], "count": r["count"], "unresolved": r["unresolved"]}
                for r in by_type
            ],
            "by_severity": [
                {"severity": r["_id"], "count": r["count"]} for r in by_severity
            ],
        },
    }


@router.post("/generate/inventory", summary="Generate inventory alerts on-demand")
def generate_inventory_alerts():
    """Scan inventory and upsert low-stock + expiry-risk alerts."""
    try:
        counts = _inv_svc.generate_inventory_alerts()
        return {"status": "ok", "data": counts}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/generate/safety", summary="Generate safety alerts on-demand")
def generate_safety_alerts():
    """Scan pending orders and create interaction-warning alerts."""
    try:
        result = _saf_svc.generate_safety_alerts()
        return {"status": "ok", "data": result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post(
    "/run-outreach", summary="Trigger proactive outreach for high-risk refills"
)
async def run_outreach():
    """
    Search for high-risk refill alerts and send AI-drafted messages via WhatsApp.
    """
    try:
        results = await _outreach_svc.scan_and_reach_out()
        return results
    except Exception as exc:
        logger.error(f"Outreach failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


class ResolveRequest(BaseModel):
    resolved_by: str = "pharmacist"
    resolution_note: str = ""


@router.patch("/{alert_id}/resolve", summary="Mark an alert as resolved")
def resolve_alert(
    alert_id: str = Path(..., description="Hex ObjectId of the alert"),
    body: ResolveRequest = Body(default_factory=ResolveRequest),
):
    """
    Mark an alert as resolved by a pharmacist.
    Send JSON body: ``{"resolved_by": "pharmacist", "resolution_note": "..."}``
    Returns the updated document (without _id).
    """
    db = get_db()
    if not ObjectId.is_valid(alert_id):
        raise HTTPException(
            status_code=400, detail=f"'{alert_id}' is not a valid ObjectId."
        )

    now = datetime.now(tz=timezone.utc)
    result = db["alerts"].find_one_and_update(
        {"_id": ObjectId(alert_id)},
        {
            "$set": {
                "is_resolved": True,
                "resolved_by": body.resolved_by,
                "resolution_note": body.resolution_note,
                "resolved_at": now,
                "updated_at": now,
            }
        },
        projection={"_id": 0},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return {"status": "ok", "data": result}


@router.get("/{alert_id}", summary="Get single alert by ID")
def get_alert(alert_id: str):
    """Fetch one alert by its MongoDB ObjectId."""
    db = get_db()
    if not ObjectId.is_valid(alert_id):
        raise HTTPException(status_code=400, detail="Invalid alert ID format.")
    doc = db["alerts"].find_one({"_id": ObjectId(alert_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found.")
    return {"status": "ok", "data": doc}
