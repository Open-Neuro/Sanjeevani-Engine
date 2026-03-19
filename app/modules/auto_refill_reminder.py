"""
Automatic Refill Reminder System
─────────────────────────────────────────────────────────────────────────────
Automatically sends WhatsApp reminders to users when their medicine is about to run out.
Calculates based on order date, quantity, and typical usage patterns.

Usage:
    from app.modules.auto_refill_reminder import AutoRefillReminderService
    service = AutoRefillReminderService()
    await service.check_and_send_reminders()
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from app.database.mongo_client import get_db
from app.config import settings
from langchain_groq import ChatGroq
from app.api.whatsapp import send_whatsapp_text

logger = logging.getLogger(__name__)


class AutoRefillReminderService:
    """Automatic medicine refill reminder system with AI-powered messages"""

    def __init__(self):
        self.db = get_db()
        self.llm = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model_name=settings.GROQ_MODEL,
            temperature=0.7,
        )
        # Reminder settings
        self.REMINDER_DAYS_BEFORE = 5  # Send reminder 5 days before medicine runs out
        self.DEFAULT_DAILY_DOSAGE = 1  # Default: 1 tablet per day

    async def check_and_send_reminders(self) -> Dict[str, Any]:
        """
        Main function: Check all orders and send reminders for medicines about to run out
        """
        logger.info("🔍 Starting automatic refill reminder check...")

        # Get all orders from last 90 days
        ninety_days_ago = datetime.now(timezone.utc) - timedelta(days=90)
        
        # Query orders collection
        orders = list(
            self.db["orders"].find({
                "created_at": {"$gte": ninety_days_ago},
                "order_status": {"$in": ["confirmed", "delivered", "address_confirmed"]}
            })
        )

        logger.info(f"📦 Found {len(orders)} orders to check")

        reminders_sent = []
        errors = []

        for order in orders:
            try:
                # Calculate if reminder is needed
                reminder_needed = await self._should_send_reminder(order)
                
                if reminder_needed:
                    # Send reminder
                    result = await self._send_refill_reminder(order)
                    if result["success"]:
                        reminders_sent.append(result)
                    else:
                        errors.append(result)
                        
            except Exception as e:
                logger.error(f"❌ Error processing order {order.get('order_id')}: {e}")
                errors.append({
                    "order_id": order.get("order_id"),
                    "error": str(e)
                })

        summary = {
            "status": "completed",
            "total_orders_checked": len(orders),
            "reminders_sent": len(reminders_sent),
            "errors": len(errors),
            "details": reminders_sent,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        logger.info(f"✅ Reminder check complete: {len(reminders_sent)} reminders sent")
        return summary

    async def _should_send_reminder(self, order: Dict[str, Any]) -> bool:
        """
        Determine if a reminder should be sent for this order
        """
        order_id = order.get("order_id")
        user_id = order.get("user_id")
        medicine_name = order.get("medicine_name")
        
        # Check if reminder already sent for this order
        existing_reminder = self.db["refill_reminders"].find_one({
            "order_id": order_id,
            "reminder_sent": True
        })
        
        if existing_reminder:
            logger.debug(f"⏭️ Reminder already sent for order {order_id}")
            return False

        # Calculate when medicine will run out
        run_out_date = self._calculate_run_out_date(order)
        
        if not run_out_date:
            return False

        # Calculate days until medicine runs out
        today = datetime.now(timezone.utc)
        days_remaining = (run_out_date - today).days

        logger.debug(
            f"📊 Order {order_id}: {medicine_name} - "
            f"{days_remaining} days remaining until run out"
        )

        # Send reminder if medicine will run out in next 3-7 days
        if 3 <= days_remaining <= 7:
            return True

        return False

    def _calculate_run_out_date(self, order: Dict[str, Any]) -> Optional[datetime]:
        """
        Calculate when the medicine will run out based on order date and quantity
        """
        try:
            # Get order date
            order_date = order.get("created_at")
            if not order_date:
                return None
            
            if not isinstance(order_date, datetime):
                order_date = datetime.fromisoformat(str(order_date))
            
            if order_date.tzinfo is None:
                order_date = order_date.replace(tzinfo=timezone.utc)

            # Get quantity ordered
            quantity = float(order.get("quantity", 1))
            
            # Get dosage frequency (default: once daily)
            # You can enhance this by storing dosage info in orders
            daily_dosage = self.DEFAULT_DAILY_DOSAGE
            
            # Check if medicine name has dosage hints
            medicine_name = str(order.get("medicine_name", "")).lower()
            if "twice" in medicine_name or "2x" in medicine_name:
                daily_dosage = 2
            elif "thrice" in medicine_name or "3x" in medicine_name:
                daily_dosage = 3

            # Calculate days supply
            days_supply = quantity / daily_dosage
            
            # Calculate run out date
            run_out_date = order_date + timedelta(days=days_supply)
            
            return run_out_date
            
        except Exception as e:
            logger.error(f"Error calculating run out date: {e}")
            return None

    async def _send_refill_reminder(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """
        Send WhatsApp reminder to user about medicine refill
        """
        user_id = order.get("user_id")
        order_id = order.get("order_id")
        medicine_name = order.get("medicine_name")
        
        try:
            # Get user profile for personalization
            user = self.db["users"].find_one({"user_id": user_id})
            user_name = user.get("name", "there") if user else "there"
            
            # Calculate days remaining
            run_out_date = self._calculate_run_out_date(order)
            days_remaining = (run_out_date - datetime.now(timezone.utc)).days if run_out_date else 5
            
            # Generate AI-powered personalized message
            message = await self._generate_reminder_message(
                user_name, 
                medicine_name, 
                days_remaining
            )
            
            # Send WhatsApp message
            send_whatsapp_text(user_id, message)
            
            # Log reminder in database
            reminder_record = {
                "order_id": order_id,
                "user_id": user_id,
                "medicine_name": medicine_name,
                "reminder_sent": True,
                "reminder_sent_at": datetime.now(timezone.utc),
                "days_remaining": days_remaining,
                "message": message,
                "created_at": datetime.now(timezone.utc)
            }
            
            self.db["refill_reminders"].insert_one(reminder_record)
            
            logger.info(
                f"✅ Reminder sent to {user_name} ({user_id}) for {medicine_name}"
            )
            
            return {
                "success": True,
                "user_id": user_id,
                "user_name": user_name,
                "medicine": medicine_name,
                "days_remaining": days_remaining,
                "order_id": order_id
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to send reminder for order {order_id}: {e}")
            return {
                "success": False,
                "order_id": order_id,
                "error": str(e)
            }

    async def _generate_reminder_message(
        self, 
        name: str, 
        medicine: str, 
        days_remaining: int
    ) -> str:
        """
        Use AI to generate personalized, friendly reminder message
        """
        prompt = f"""
        Create a friendly WhatsApp reminder message from "Sanjeevani" at SanjeevaniRxAI Pharmacy.
        
        Context:
        - Recipient Name: {name}
        - Medicine: {medicine}
        - Days until medicine runs out: {days_remaining} days
        
        Requirements:
        1. Warm, caring tone (like a helpful friend)
        2. Remind them their {medicine} will run out in {days_remaining} days
        3. Ask if they want to reorder now
        4. Mention: "Just reply 'Yes' or 'Reorder' and I'll help you!"
        5. Keep it under 60 words
        6. Use 1-2 relevant emojis
        7. Sound natural and conversational
        8. Use "SanjeevaniRxAI" as pharmacy name
        
        ONLY return the message text, no quotes or extra formatting.
        """

        try:
            response = await self.llm.ainvoke(prompt)
            content = response.content.strip()
            
            # Remove wrapping quotes if present
            if content.startswith('"') and content.endswith('"'):
                content = content[1:-1]
            if content.startswith("'") and content.endswith("'"):
                content = content[1:-1]
                
            return content
            
        except Exception as e:
            logger.error(f"AI message generation failed: {e}")
            # Fallback message
            return (
                f"Hi {name}! 💊 This is Sanjeevani from SanjeevaniRxAI. "
                f"Your {medicine} will run out in about {days_remaining} days. "
                f"Would you like to reorder now? Just reply 'Yes' and I'll help you! 😊"
            )

    async def get_reminder_stats(self) -> Dict[str, Any]:
        """
        Get statistics about sent reminders
        """
        total_reminders = self.db["refill_reminders"].count_documents({})
        
        # Reminders sent in last 7 days
        week_ago = datetime.now(timezone.utc) - timedelta(days=7)
        recent_reminders = self.db["refill_reminders"].count_documents({
            "reminder_sent_at": {"$gte": week_ago}
        })
        
        # Get top medicines with reminders
        pipeline = [
            {"$group": {
                "_id": "$medicine_name",
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}},
            {"$limit": 5}
        ]
        
        top_medicines = list(self.db["refill_reminders"].aggregate(pipeline))
        
        return {
            "total_reminders_sent": total_reminders,
            "reminders_last_7_days": recent_reminders,
            "top_medicines": top_medicines,
            "last_updated": datetime.now(timezone.utc).isoformat()
        }
