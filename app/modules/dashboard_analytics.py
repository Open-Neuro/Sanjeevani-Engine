"""
app/modules/dashboard_analytics.py
─────────────────────────────────────────────────────────────────────────────
Dashboard analytics service with in-memory TTL caching (cachetools).

Public API
──────────
    from app.modules.dashboard_analytics import DashboardAnalyticsService
    svc = DashboardAnalyticsService()
    metrics = svc.get_overview_metrics()
    svc.refresh_dashboard_cache()
"""

from __future__ import annotations

import functools
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from cachetools import TTLCache, cached

from app.database.mongo_client import get_db
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Cache: 128 entries, 5-second TTL ───────────────────────────────────────
_CACHE: TTLCache = TTLCache(maxsize=128, ttl=5)


def _cache_key(*args, **kwargs) -> str:
    return str(args) + str(sorted(kwargs.items()))


class DashboardAnalyticsService:
    """Aggregated dashboard metrics with cachetools-based TTL caching."""

    def __init__(self) -> None:
        self._db = None

    @property
    def db(self):
        if self._db is None:
            self._db = get_db()
        return self._db

    # ──────────────────────────────────────────────────────────────────────
    # 1. get_overview_metrics
    # ──────────────────────────────────────────────────────────────────────

    def get_overview_metrics(self, merchant_id: str) -> Dict[str, Any]:
        """
        High-level KPIs for the dashboard header:
        - Total patients, total orders, total products
        - Revenue (sum of Total Amount)
        - Active alerts (unresolved)
        - High-risk refills
        - Low-stock items
        - Expiry-risk items
        """
        cache_key = f"{merchant_id}_overview_metrics"
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        now = datetime.now(tz=timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_patients = self.db["patients"].count_documents({"merchant_id": merchant_id})
        total_orders = self.db["consumer_orders"].count_documents({"merchant_id": merchant_id})
        total_products = self.db["products"].count_documents({"merchant_id": merchant_id})
        
        # Dashboard health
        dashboard_filter = {"merchant_id": merchant_id}

        # Revenue aggregation
        rev_pipeline = [
            {"$match": {"merchant_id": merchant_id}},
            {"$group": {"_id": None, "total": {"$sum": "$Total Amount"}}},
        ]
        rev_res = list(self.db["consumer_orders"].aggregate(rev_pipeline))
        total_revenue = rev_res[0]["total"] if rev_res else 0.0

        # Monthly revenue
        month_rev_pipeline = [
            {"$match": {"Order Date": {"$gte": month_start}, "merchant_id": merchant_id}},
            {"$group": {"_id": None, "total": {"$sum": "$Total Amount"}}},
        ]
        month_rev_res = list(self.db["consumer_orders"].aggregate(month_rev_pipeline))
        monthly_revenue = month_rev_res[0]["total"] if month_rev_res else 0.0

        active_alerts = self.db["alerts"].count_documents({"is_resolved": False, "merchant_id": merchant_id})
        high_risk_preds = self.db["predictions"].count_documents(
            {"risk_level": {"$in": ["critical", "high"]}, "is_actioned": False, "merchant_id": merchant_id}
        )
        low_stock = self.db["inventory"].count_documents({"is_low_stock": True, "merchant_id": merchant_id})
        expiry_risk = self.db["inventory"].count_documents({"is_expiry_risk": True, "merchant_id": merchant_id})

        result = {
            "total_patients": total_patients,
            "total_orders": total_orders,
            "total_products": total_products,
            "total_revenue": round(float(total_revenue), 2),
            "monthly_revenue": round(float(monthly_revenue), 2),
            "active_alerts": active_alerts,
            "high_risk_refills": high_risk_preds,
            "low_stock_items": low_stock,
            "expiry_risk_items": expiry_risk,
            "computed_at": now.isoformat(),
        }
        _CACHE[cache_key] = result
        return result

    # ──────────────────────────────────────────────────────────────────────
    # 2. get_customer_insights
    # ──────────────────────────────────────────────────────────────────────

    def get_customer_insights(self, merchant_id: str) -> Dict[str, Any]:
        """
        Demographics and behaviour stats:
        - Gender breakdown
        - Age distribution (bins)
        - Top order channels
        - Top diagnoses
        - Chronic vs acute split
        """
        cache_key = f"{merchant_id}_customer_insights"
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        def _aggregate(field: str) -> List[Dict]:
            return list(
                self.db["consumer_orders"].aggregate(
                    [
                        {"$match": {field: {"$exists": True, "$ne": None}, "merchant_id": merchant_id}},
                        {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                        {"$sort": {"count": -1}},
                        {"$limit": 10},
                    ]
                )
            )

        gender_data = [
            {"label": r["_id"], "count": r["count"]} for r in _aggregate("Gender")
        ]
        channel_data = [
            {"label": r["_id"], "count": r["count"]}
            for r in _aggregate("Order Channel")
        ]
        diag_data = [
            {"label": r["_id"], "count": r["count"]} for r in _aggregate("Diagnosis")
        ]

        # Age bins
        age_pipeline = [
            {"$match": {"Age": {"$exists": True, "$ne": None, "$gt": 0}, "merchant_id": merchant_id}},
            {
                "$bucket": {
                    "groupBy": "$Age",
                    "boundaries": [0, 18, 30, 45, 60, 75, 120],
                    "default": "Unknown",
                    "output": {"count": {"$sum": 1}},
                }
            },
        ]
        age_bins = list(self.db["consumer_orders"].aggregate(age_pipeline))
        age_labels = ["0-17", "18-29", "30-44", "45-59", "60-74", "75+", "Unknown"]
        age_dist = [
            {
                "label": age_labels[i] if i < len(age_labels) else str(b.get("_id")),
                "count": b["count"],
            }
            for i, b in enumerate(age_bins)
        ]

        # Chronic split
        chronic_y = self.db["consumer_orders"].count_documents({"Is Chronic": "Yes", "merchant_id": merchant_id})
        chronic_n = self.db["consumer_orders"].count_documents({"Is Chronic": "No", "merchant_id": merchant_id})

        result = {
            "gender_distribution": gender_data,
            "age_distribution": age_dist,
            "top_channels": channel_data,
            "top_diagnoses": diag_data,
            "chronic_split": {
                "chronic": chronic_y,
                "acute": chronic_n,
            },
        }
        _CACHE[cache_key] = result
        return result

    # ──────────────────────────────────────────────────────────────────────
    # 3. get_product_analytics
    # ──────────────────────────────────────────────────────────────────────

    def get_product_analytics(self, merchant_id: str) -> Dict[str, Any]:
        """
        Product-level insights:
        - Top 10 medicines by order count
        - Top 10 by revenue
        - Category breakdown
        - Low-stock summary
        - Expiry risk summary
        """
        cache_key = f"{merchant_id}_product_analytics"
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        top_by_orders = list(
            self.db["consumer_orders"].aggregate(
                [
                    {"$match": {"merchant_id": merchant_id}},
                    {
                        "$group": {
                            "_id": "$Medicine Name",
                            "orders": {"$sum": 1},
                            "revenue": {"$sum": "$Total Amount"},
                        }
                    },
                    {"$sort": {"orders": -1}},
                    {"$limit": 10},
                ]
            )
        )
        top_by_revenue = sorted(
            top_by_orders, key=lambda x: x["revenue"], reverse=True
        )[:10]

        category_data = list(
            self.db["consumer_orders"].aggregate(
                [
                    {"$match": {"Medicine Category": {"$exists": True, "$ne": None}, "merchant_id": merchant_id}},
                    {"$group": {"_id": "$Medicine Category", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ]
            )
        )

        result = {
            "top_medicines_by_orders": [
                {
                    "medicine": r["_id"],
                    "orders": r["orders"],
                    "revenue": round(float(r.get("revenue") or 0), 2),
                }
                for r in top_by_orders
            ],
            "top_medicines_by_revenue": [
                {
                    "medicine": r["_id"],
                    "revenue": round(float(r.get("revenue") or 0), 2),
                    "orders": r.get("orders", 0),
                }
                for r in top_by_revenue
            ],
            "category_breakdown": [
                {"category": r["_id"], "count": r["count"]} for r in category_data
            ],
            "low_stock_count": self.db["inventory"].count_documents(
                {"is_low_stock": True, "merchant_id": merchant_id}
            ),
            "expiry_risk_count": self.db["inventory"].count_documents(
                {"is_expiry_risk": True, "merchant_id": merchant_id}
            ),
        }
        _CACHE[cache_key] = result
        return result

    # ──────────────────────────────────────────────────────────────────────
    # 4. get_order_analytics
    # ──────────────────────────────────────────────────────────────────────

    def get_order_analytics(self, merchant_id: str) -> Dict[str, Any]:
        """
        Order-level analytics:
        - Status breakdown (Pending / Fulfilled / Cancelled)
        - Daily order count (last 30 days)
        - Payment method split
        - Average order value
        """
        cache_key = f"{merchant_id}_order_analytics"
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        status_data = list(
            self.db["consumer_orders"].aggregate(
                [
                    {"$match": {"merchant_id": merchant_id}},
                    {"$group": {"_id": "$Order Status", "count": {"$sum": 1}}},
                ]
            )
        )

        payment_data = list(
            self.db["consumer_orders"].aggregate(
                [
                    {"$match": {"Payment Method": {"$exists": True, "$ne": None}, "merchant_id": merchant_id}},
                    {"$group": {"_id": "$Payment Method", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                ]
            )
        )

        avg_pipeline = [
            {"$match": {"merchant_id": merchant_id}},
            {"$group": {"_id": None, "avg_value": {"$avg": "$Total Amount"}}},
        ]
        avg_res = list(self.db["consumer_orders"].aggregate(avg_pipeline))
        avg_order_value = avg_res[0]["avg_value"] if avg_res else 0.0

        since30 = datetime.now(tz=timezone.utc) - timedelta(days=30)
        daily_pipeline = [
            {"$match": {"Order Date": {"$gte": since30}, "merchant_id": merchant_id}},
            {
                "$group": {
                    "_id": {
                        "y": {"$year": "$Order Date"},
                        "m": {"$month": "$Order Date"},
                        "d": {"$dayOfMonth": "$Order Date"},
                    },
                    "count": {"$sum": 1},
                }
            },
            {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}},
        ]
        daily_data = [
            {
                "date": f"{r['_id']['y']}-{r['_id']['m']:02d}-{r['_id']['d']:02d}",
                "count": r["count"],
            }
            for r in self.db["consumer_orders"].aggregate(daily_pipeline)
        ]

        result = {
            "status_breakdown": [
                {"status": r["_id"] or "Unknown", "count": r["count"]}
                for r in status_data
            ],
            "payment_methods": [
                {"method": r["_id"], "count": r["count"]} for r in payment_data
            ],
            "avg_order_value": round(float(avg_order_value or 0), 2),
            "daily_orders_30d": daily_data,
        }
        _CACHE[cache_key] = result
        return result

    # ──────────────────────────────────────────────────────────────────────
    # 5. get_timeseries_data
    # ──────────────────────────────────────────────────────────────────────

    def get_timeseries_data(
        self, metric: str = "orders", period: str = "30d", merchant_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Return daily time-series data for a given metric.

        Parameters
        ──────────
        metric : ``"orders"`` | ``"revenue"``
        period : ``"7d"`` | ``"30d"`` | ``"90d"`` | ``"365d"``

        Returns list of ``{date, value}`` dicts.
        """
        cache_key = f"{merchant_id}_timeseries_{metric}_{period}"
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        days_map = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}
        days = days_map.get(period, 30)
        since = datetime.now(tz=timezone.utc) - timedelta(days=days)

        group_value = {"$sum": "$Total Amount"} if metric == "revenue" else {"$sum": 1}
        pipeline = [
            {"$match": {"Order Date": {"$gte": since}, "merchant_id": merchant_id}},
            {
                "$group": {
                    "_id": {
                        "y": {"$year": "$Order Date"},
                        "m": {"$month": "$Order Date"},
                        "d": {"$dayOfMonth": "$Order Date"},
                    },
                    "value": group_value,
                }
            },
            {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}},
        ]
        data = [
            {
                "date": f"{r['_id']['y']}-{r['_id']['m']:02d}-{r['_id']['d']:02d}",
                "value": round(float(r["value"]), 2),
            }
            for r in self.db["consumer_orders"].aggregate(pipeline)
        ]
        _CACHE[cache_key] = data
        return data

    # ──────────────────────────────────────────────────────────────────────
    # 6. refresh_dashboard_cache
    # ──────────────────────────────────────────────────────────────────────

    def refresh_dashboard_cache(self, merchant_id: str) -> Dict[str, Any]:
        """
        Force-refresh all cached metrics.

        Clears the in-memory TTL cache and pre-warms with current data.
        Returns a summary of the refresh operation.
        """
        logger.info("Refreshing dashboard cache…")
        _CACHE.clear()

        start = time.perf_counter()
        self.get_overview_metrics(merchant_id=merchant_id)
        self.get_customer_insights(merchant_id=merchant_id)
        self.get_product_analytics(merchant_id=merchant_id)
        self.get_order_analytics(merchant_id=merchant_id)
        self.get_timeseries_data("orders", "30d", merchant_id=merchant_id)
        self.get_timeseries_data("revenue", "30d", merchant_id=merchant_id)
        elapsed = round((time.perf_counter() - start) * 1000, 1)

        logger.info("Dashboard cache refreshed", extra={"elapsed_ms": elapsed})
        return {
            "status": "refreshed",
            "elapsed_ms": elapsed,
            "cache_size": len(_CACHE),
            "refreshed_at": datetime.now(tz=timezone.utc).isoformat(),
        }
