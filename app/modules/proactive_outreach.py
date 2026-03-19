import logging
import json
from datetime import datetime, timezone
from typing import Dict, Any, List

from app.database.mongo_client import get_db
from app.config import settings
from langchain_groq import ChatGroq
from app.api.whatsapp import send_whatsapp_text

logger = logging.getLogger(__name__)


class ProactiveOutreachService:
    """AI-powered proactive patient outreach for medication refills."""

    def __init__(self):
        self.db = get_db()
        # Initialize Groq for high-quality outreach generation
        self.llm = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model_name=settings.GROQ_MODEL,
            temperature=0.7,
        )

    async def scan_and_reach_out(self) -> Dict[str, Any]:
        """
        Scans binary high-risk refill alerts and reaches out to patients
        automatically using personalized AI messages.
        """
        logger.info("🚀 Starting AI Proactive Outreach scan...")

        # 1. Fetch high-risk alerts that haven't been actioned yet
        alerts = list(
            self.db["alerts"].find(
                {
                    "alert_type": "refill_due",
                    "severity": "high",
                    "is_resolved": False,
                    "outreach_sent": {"$ne": True},
                }
            )
        )

        if not alerts:
            logger.info(
                "✅ No high-risk refill alerts requiring outreach at this time."
            )
            return {"status": "ok", "outreach_count": 0, "message": "No alerts found."}

        outreach_results = []

        for alert in alerts:
            patient_id = alert.get("patient_id")
            # Extract medicine name from record
            medicine_name = alert.get("medicine_name") or alert.get("metadata", {}).get(
                "medicine_name"
            )

            if not medicine_name:
                # Fallback extraction from message text
                msg = alert.get("message", "")
                if ":" in msg:
                    medicine_name = msg.split(":")[-1].strip()

            # 2. Get Patient Contact Data
            patient = self.db["patients"].find_one({"patient_id": patient_id})
            if not patient or not patient.get("contact_number"):
                logger.warning(
                    f"⚠️ Patient {patient_id} has no contact number. Skipping."
                )
                continue

            phone = str(patient["contact_number"])
            # Clean phone (remove spaces/dashes for WhatsApp)
            phone = "".join(filter(str.isdigit, phone))

            patient_name = patient.get("name", "there")

            # 3. Draft Personalized AI Message via Groq
            logger.info(
                f"🧠 Drafting AI outreach message for {patient_name} regarding {medicine_name}..."
            )
            outreach_message = await self._draft_message(patient_name, medicine_name)

            # 4. Dispatch Outreach (WhatsApp)
            logger.info(f"📤 Sending WhatsApp outreach to {phone}...")
            try:
                # We use the existing WhatsApp utility
                send_whatsapp_text(phone, outreach_message)

                # 5. Track Outreach & Mark Resolved
                self.db["alerts"].update_one(
                    {"_id": alert["_id"]},
                    {
                        "$set": {
                            "outreach_sent": True,
                            "outreach_at": datetime.now(tz=timezone.utc),
                            "is_resolved": True,
                            "resolved_by": "RefillAgent",
                            "resolution_note": f"AI Proactive Outreach sent to {phone}",
                            "updated_at": datetime.now(tz=timezone.utc),
                        }
                    },
                )

                outreach_results.append(
                    {
                        "patient_id": patient_id,
                        "patient_name": patient_name,
                        "medicine": medicine_name,
                        "phone": phone,
                        "status": "Success",
                    }
                )

            except Exception as send_err:
                logger.error(f"❌ Failed to send outreach to {phone}: {send_err}")

        return {
            "status": "ok",
            "outreach_count": len(outreach_results),
            "details": outreach_results,
        }

    async def _draft_message(self, name: str, medicine: str) -> str:
        """Uses Groq to create a warm, non-spammy refill reminder."""
        prompt = f"""
        Draft a friendly, professional WhatsApp outreach message from "Sanjeevani" at SanjeevaniRxAI Pharmacy.
        Recipient Name: {name}
        Condition: Their supply of {medicine} is estimated to run out within 3 days.
        
        GOAL:
        1. Warn them warmly that they are running low on {medicine}.
        2. Ask if they would like Sanjeevani to prepare a refill order for them now.
        3. Mention: "Just say 'Confirm' or 'Yes' and I'll handle the rest for you."
        4. Keep it human, empathetic, and under 50 words.
        5. Use one or two relevant emojis.
        
        DO NOT use placeholders like [Your Pharmacy Name]. Use "SanjeevaniRxAI".
        ONLY RETURN THE MESSAGE TEXT.
        """

        try:
            response = await self.llm.ainvoke(prompt)
            content = response.content.strip()
            # Remove any wrapping quotes if the AI added them
            if content.startswith('"') and content.endswith('"'):
                content = content[1:-1]
            return content
        except Exception as e:
            logger.error(f"Groq generation failed for outreach: {e}")
            # Robust fallback
            return f"Hi {name}, I'm Sanjeevani from SanjeevaniRxAI. 😊 Your {medicine} supply is running low soon. Would you like me to prepare a refill for you? Just say 'Yes' to confirm and I'll take care of it!"
