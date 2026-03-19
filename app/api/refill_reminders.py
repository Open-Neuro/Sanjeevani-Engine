"""
Refill Reminder API Endpoints
─────────────────────────────────────────────────────────────────────────────
API endpoints for managing automatic medicine refill reminders
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, Any, Optional
from datetime import datetime
import logging

from app.modules.auto_refill_reminder import AutoRefillReminderService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Refill Reminders"])


class ReminderResponse(BaseModel):
    status: str
    message: str
    data: Optional[Dict[str, Any]] = None


@router.post("/api/refill-reminders/check", response_model=ReminderResponse)
async def check_and_send_reminders(background_tasks: BackgroundTasks):
    """
    Manually trigger refill reminder check and send WhatsApp messages
    
    This endpoint:
    1. Checks all recent orders
    2. Calculates which medicines are about to run out
    3. Sends WhatsApp reminders to users
    
    Can be called manually or scheduled via cron job
    """
    try:
        service = AutoRefillReminderService()
        
        # Run in background to avoid timeout
        result = await service.check_and_send_reminders()
        
        return ReminderResponse(
            status="success",
            message=f"Reminder check completed. {result['reminders_sent']} reminders sent.",
            data=result
        )
        
    except Exception as e:
        logger.error(f"Error in refill reminder check: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to check reminders: {str(e)}"
        )


@router.get("/api/refill-reminders/stats", response_model=ReminderResponse)
async def get_reminder_stats():
    """
    Get statistics about sent refill reminders
    
    Returns:
    - Total reminders sent
    - Reminders sent in last 7 days
    - Top medicines with reminders
    """
    try:
        service = AutoRefillReminderService()
        stats = await service.get_reminder_stats()
        
        return ReminderResponse(
            status="success",
            message="Reminder statistics retrieved successfully",
            data=stats
        )
        
    except Exception as e:
        logger.error(f"Error getting reminder stats: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get stats: {str(e)}"
        )


@router.get("/api/refill-reminders/test/{user_id}")
async def test_reminder_for_user(user_id: str):
    """
    Test reminder system for a specific user
    
    Useful for demo purposes - shows what reminder would be sent
    """
    try:
        service = AutoRefillReminderService()
        
        # Get user's recent orders
        orders = list(
            service.db["orders"].find({
                "user_id": user_id,
                "order_status": {"$in": ["confirmed", "delivered"]}
            }).sort("created_at", -1).limit(5)
        )
        
        if not orders:
            return {
                "status": "no_orders",
                "message": f"No orders found for user {user_id}",
                "user_id": user_id
            }
        
        # Check each order
        results = []
        for order in orders:
            run_out_date = service._calculate_run_out_date(order)
            if run_out_date:
                days_remaining = (run_out_date - datetime.now()).days
                
                results.append({
                    "order_id": order.get("order_id"),
                    "medicine": order.get("medicine_name"),
                    "order_date": order.get("created_at").isoformat() if order.get("created_at") else None,
                    "quantity": order.get("quantity"),
                    "run_out_date": run_out_date.isoformat(),
                    "days_remaining": days_remaining,
                    "reminder_needed": 3 <= days_remaining <= 7
                })
        
        return {
            "status": "success",
            "user_id": user_id,
            "orders_checked": len(orders),
            "results": results
        }
        
    except Exception as e:
        logger.error(f"Error testing reminder for user {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Test failed: {str(e)}"
        )


@router.post("/api/refill-reminders/schedule")
async def schedule_daily_reminders():
    """
    Setup information for scheduling daily reminder checks
    
    Returns instructions for setting up cron job or scheduler
    """
    return {
        "status": "info",
        "message": "Refill reminder scheduling information",
        "instructions": {
            "manual_trigger": "POST /api/refill-reminders/check",
            "recommended_schedule": "Daily at 10:00 AM",
            "cron_expression": "0 10 * * *",
            "setup_options": [
                {
                    "method": "Linux Cron",
                    "command": "0 10 * * * curl -X POST http://your-server/api/refill-reminders/check"
                },
                {
                    "method": "Python APScheduler",
                    "code": "scheduler.add_job(check_reminders, 'cron', hour=10)"
                },
                {
                    "method": "Cloud Function",
                    "description": "Use AWS Lambda, Google Cloud Functions, or Azure Functions with daily trigger"
                }
            ]
        },
        "demo_note": "For hackathon demo, call POST /api/refill-reminders/check manually to show the feature"
    }
