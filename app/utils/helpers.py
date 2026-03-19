"""
app/utils/helpers.py
General utility helpers for SanjeevaniRxAI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)


def build_pagination_response(
    data: list,
    total: int,
    page: int,
    page_size: int,
    status: str = "ok",
) -> Dict[str, Any]:
    """Construct a standard paginated JSON response body."""
    return {
        "status": status,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": -(-total // page_size),  # ceiling division
        "data": data,
    }
