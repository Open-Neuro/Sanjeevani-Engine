"""
app/api/dashboard.py  –  /api/v1/dashboard
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Depends

from app.modules.dashboard_analytics import DashboardAnalyticsService
from app.utils.logger import get_logger
from app.utils.security import get_current_user

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
logger = get_logger(__name__)
_svc = DashboardAnalyticsService()


@router.get("/overview", summary="Overview KPIs")
def get_overview(user: dict = Depends(get_current_user)):
    """Return high-level KPI metrics for the dashboard header."""
    try:
        return {"status": "ok", "data": _svc.get_overview_metrics()}
    except Exception as exc:
        logger.error("overview error", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/customers", summary="Customer insights")
def get_customer_insights(user: dict = Depends(get_current_user)):
    """Demographics and behaviour analytics."""
    try:
        return {"status": "ok", "data": _svc.get_customer_insights()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/products", summary="Product analytics")
def get_product_analytics(user: dict = Depends(get_current_user)):
    """Top medicines, category breakdown, inventory health."""
    try:
        return {"status": "ok", "data": _svc.get_product_analytics()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/orders", summary="Order analytics")
def get_order_analytics(user: dict = Depends(get_current_user)):
    """Status breakdown, payment methods, average order value."""
    try:
        return {"status": "ok", "data": _svc.get_order_analytics()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/timeseries", summary="Time-series data")
def get_timeseries(
    metric: str = Query(default="orders", regex="^(orders|revenue)$"),
    period: str = Query(default="30d", regex="^(7d|30d|90d|365d)$"),
    user: dict = Depends(get_current_user),
):
    """Daily time-series for orders or revenue."""
    try:
        data = _svc.get_timeseries_data(metric=metric, period=period)
        return {"status": "ok", "metric": metric, "period": period, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/refresh-cache", summary="Force-refresh dashboard cache")
def refresh_cache(user: dict = Depends(get_current_user)):
    """Invalidate and pre-warm the in-memory analytics cache."""
    try:
        return _svc.refresh_dashboard_cache()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
