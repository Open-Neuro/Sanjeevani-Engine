from fastapi import APIRouter, Request
from app.agents.agent_6_orchestrator import orchestrator
from langchain_core.messages import HumanMessage
from datetime import datetime
from app.database.mongo_client import get_db

router = APIRouter()


@router.post("/chatbot/webhook")
async def chatbot_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        data = {}

    user_message = data.get("message", "")
    user_id = data.get("user_id", "guest")
    platform = data.get("platform", "Web Chat")

    # Prepare state for orchestrator
    state = {
        "messages": [HumanMessage(content=user_message)],
        "extracted_meds": [],
        "safety_validated": None,
        "validation_reasons": [],
        "inventory_checked": None,
        "inventory_results": [],
        "fulfillment_status": None,
        "final_response": None,
        "channel": platform,
        "channel_metadata": {"user_id": user_id, "platform": platform},
    }
    result = await orchestrator.ainvoke(state)
    reply = result.get("final_response", "Sorry, something went wrong.")
    return {"reply": reply}


from pydantic import BaseModel, Field


class DemoRequest(BaseModel):
    message: str = Field(
        ...,
        description="Message containing products to order",
        example="I need 2 strips of Paracetamol and 1 cough syrup please",
    )
    patient_name: str = Field(default="Demo User")
    patient_id: str = Field(default="DEMO_12345")
    age: int = Field(default=30)
    gender: str = Field(default="Unknown")
    contact_number: str = Field(default="1234567890")
    address: str = Field(default="123 Demo St, Demo City")
    platform: str = Field(default="Demo Chatbot")


@router.post("/chatbot/demo")
async def chatbot_demo(request: DemoRequest):
    """
    Demo endpoint to test the agents with sample messages.
    Now relies on Agent 5 for all database operations.
    """
    # Run the orchestrator CoT process
    state = {
        "messages": [HumanMessage(content=request.message)],
        "extracted_meds": [],
        "safety_validated": None,
        "validation_reasons": [],
        "inventory_checked": None,
        "inventory_results": [],
        "fulfillment_status": None,
        "final_response": None,
        "channel": request.platform,
        "channel_metadata": {
            "user_id": request.patient_id,
            "patient_name": request.patient_name,
            "address": request.address,
            "contact": request.contact_number,
        },
    }
    result = await orchestrator.ainvoke(state)

    # Serialize message objects before returning as JSON
    if "messages" in result:
        result["messages"] = [
            m.content if hasattr(m, "content") else str(m) for m in result["messages"]
        ]

    return {
        "status": (
            "success"
            if result.get("fulfillment_status") == "SUCCESS"
            else "failed_or_rejected"
        ),
        "reply": result.get("final_response", "Sorry, something went wrong."),
        "agent_cot_details": result,
    }
