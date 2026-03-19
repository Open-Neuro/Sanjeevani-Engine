import os
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import httpx

from app.database.mongo_client import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/voice", tags=["Voice Assistant"])

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8000/api/v1")

# ─────────────────────────────────────────────────────────────────────────────
# Assistant system prompt  (PharmAI personality)
# ─────────────────────────────────────────────────────────────────────────────

PHARMA_SYSTEM_PROMPT = """
You are Sanjeevani, a friendly AI pharmacist for SanjeevaniRxAI Pharmacy. You speak warmly and naturally.

YOUR BEHAVIOR:
  1. Greet the caller warmly and ask what they need.
  2. If they name a medicine (e.g., "I need paracetamol"), ask for the quantity if they didn't say it.
  3. Once they give the medicine and quantity, summarize the order and ask "Would you like me to place this order? Please say confirm."
  4. If they say "confirm" or "yes", say "Processing your order now..." and then call the `check_order_availability` tool.
  5. After the tool returns:
     - SUCCESS: Say "Order confirmed! I've placed your order. We'll notify you on this number when it's ready. Would you like anything else or should I end the call?"
     - PRESCRIPTION NEEDED: Say "I see that this medicine requires a doctor's prescription. Please visit us at the store with your prescription. Anything else?"
     - OUT OF STOCK: Say "I'm sorry, we are currently out of stock for that. Anything else?"
  6. If they say "nothing else" or "goodbye", end the call.

RULES:
  - ALWAYS ask for confirmation ("Please say confirm") before placing the order.
  - End the call naturally after the user is satisfied.
  - Keep responses under 2 clear sentences for voice clarity.
"""

# ─────────────────────────────────────────────────────────────────────────────
# Tool definition sent to Vapi
# ─────────────────────────────────────────────────────────────────────────────

ORDER_TOOL = {
    "type": "function",
    "function": {
        "name": "check_order_availability",
        "description": (
            "Check if the requested medicines are safe and in stock, "
            "then place the order if everything is valid."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "patient_name": {
                    "type": "string",
                    "description": "Optional name of the caller, if they gave it.",
                },
                "medicines": {
                    "type": "array",
                    "description": "List of medicines the caller wants to order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "quantity": {"type": "number", "default": 1},
                        },
                        "required": ["name", "quantity"],
                    },
                },
            },
            "required": ["medicines"],
        },
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Build Vapi Assistant Config
# ─────────────────────────────────────────────────────────────────────────────


def build_assistant_config(call_id: str, caller_phone: str) -> Dict[str, Any]:
    # Build public webhook URL correctly
    base = SERVER_URL.rstrip("/")
    # Avoid double /api/v1 if SERVER_URL already ends with it
    if base.endswith("/api/v1"):
        webhook_url = f"{base}/voice/inbound"
    else:
        webhook_url = f"{base}/api/v1/voice/inbound"

    return {
        "name": "SanjeevaniRx PharmAI",
        "firstMessage": (
            "Hello! You've reached SanjeevaniRxAI Pharmacy. "
            "I'm Sanjeevani, your AI pharmacist. "
            "Which medicines would you like to order today?"
        ),
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "messages": [{"role": "system", "content": PHARMA_SYSTEM_PROMPT}],
            "tools": [ORDER_TOOL],
            "temperature": 0.3,
        },
        "voice": {
            # openai TTS is built into Vapi — no PlayHT/ElevenLabs account needed
            "provider": "openai",
            "voiceId": "nova",  # warm, clear female voice
        },
        "recordingEnabled": True,
        "endCallMessage": "Thank you for calling SanjeevaniRxAI Pharmacy. Get well soon! Goodbye.",
        "endCallFunctionEnabled": True,
        "maxDurationSeconds": 300,
        "serverUrl": webhook_url,
        "serverUrlSecret": os.getenv("VAPI_WEBHOOK_SECRET", ""),
        "metadata": {
            "caller_phone": caller_phone,
            "call_id": call_id,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 1: Vapi Inbound Webhook  (POST /api/v1/voice/inbound)
# Vapi calls this for every event during the call lifecycle.
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/inbound", summary="Main Vapi webhook for inbound pharmacy calls")
async def vapi_inbound_webhook(request: Request):
    """
    Single endpoint that handles ALL Vapi events:
      - assistant-request: (When call starts) Returns the assistant config.
      - function-call: (During call) LLM wants to check/place the medicine order.
      - end-of-call-report: (When call ends) Saves transcript and recording to DB.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    message = payload.get("message", payload)
    msg_type = message.get("type")

    # 1. Start of call: Provide Assistant Configuration
    if msg_type == "assistant-request":
        call_id = message.get("call", {}).get("id", "unknown")
        caller_phone = (
            message.get("call", {}).get("customer", {}).get("number", "Unknown")
        )
        logger.info(f"[Voice] 📞 Incoming call from {caller_phone} (ID: {call_id})")

        config = build_assistant_config(call_id, caller_phone)
        return {"assistant": config}

    # 2. During call: Handle LLM Function Calling (Order Placement)
    if msg_type == "tool-calls" or msg_type == "function-call":
        tool_calls = message.get("toolCalls", message.get("toolWithToolCallList", []))
        if not tool_calls and "functionCall" in message:
            tool_calls = [message["functionCall"]]

        results = []
        for call_data in tool_calls:
            func = call_data.get("function", call_data)
            func_name = func.get("name")
            call_id_val = call_data.get("id", "none")

            if func_name == "check_order_availability":
                params = func.get("arguments", {})
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except json.JSONDecodeError:
                        params = {}

                # Caller info sent via metadata during assistant config
                meta = message.get("call", {}).get("metadata", {})
                call_id_log = message.get("call", {}).get("id", "unknown")
                caller_phone = meta.get("caller_phone", "Unknown")

                logger.info(
                    f"[Voice] 🩺 Tool check_order_availability called for {caller_phone}"
                )

                # Run the 6-agent backend evaluation
                response_str = await _handle_order_tool_call(
                    call_id=call_id_log,
                    caller_phone=caller_phone,
                    parameters=params,
                )

                results.append(
                    {
                        "toolCallId": call_id_val,
                        "result": response_str,
                    }
                )

        return {"results": results}

    # 3. End of call: Save Transcript and Data to Dashboard
    if msg_type == "end-of-call-report":
        await _persist_call_report(message)
        return {"status": "success"}

    # Catch-all for unsupported events
    return {"status": "ignored", "type": msg_type}


@router.post("/tool-call", summary="Vapi tool-call handler (serverUrl target)")
async def vapi_tool_call(request: Request):
    """Alias — all Vapi events go through the same inbound handler."""
    return await vapi_inbound_webhook(request)


@router.get(
    "/debug/config", summary="Preview the assistant config that will be sent to Vapi"
)
def debug_assistant_config():
    """
    Call this in your browser or Swagger to see exactly what assistant config
    your server will return to Vapi on assistant-request.
    Useful for catching bad field names before a real call.
    """
    config = build_assistant_config(call_id="debug-test", caller_phone="+910000000000")
    return {
        "status": "ok",
        "webhook_url_that_vapi_will_hit": config.get("serverUrl"),
        "assistant_config": config,
    }


@router.post("/debug/simulate", summary="Simulate a Vapi assistant-request webhook")
async def debug_simulate_webhook():
    """
    Simulates what Vapi sends when someone calls your number.
    Use this to verify your webhook responds with a valid assistant config.
    """
    fake_payload = {
        "type": "assistant-request",
        "call": {"id": "debug-call-001", "customer": {"number": "+919876543210"}},
    }
    from fastapi import Request as FR
    from starlette.testclient import TestClient

    config = build_assistant_config("debug-call-001", "+919876543210")
    return {
        "status": "ok",
        "message": "This is what your server returns to Vapi on assistant-request",
        "response": {"assistant": config},
    }


@router.post(
    "/setup",
    summary="⚙️ One-click: Create SanjeevaniRx assistant on Vapi and assign to phone number",
)
async def setup_vapi_assistant():
    """
    Run this ONCE to:
    1. Create the pharmacy assistant on Vapi (with our system prompt + tools).
    2. Assign it to the VAPI_PHONE_NUMBER_ID in your .env.

    After this, anyone who calls the Vapi number gets the SanjeevaniRx AI pharmacist.
    Replaces any old assistant previously on that number.
    """
    if not VAPI_API_KEY or VAPI_API_KEY == "your_vapi_key_here":
        raise HTTPException(status_code=503, detail="VAPI_API_KEY not set in .env")
    if not VAPI_PHONE_NUMBER_ID or VAPI_PHONE_NUMBER_ID == "your_vapi_phone_id":
        raise HTTPException(
            status_code=503, detail="VAPI_PHONE_NUMBER_ID not set in .env"
        )

    base = SERVER_URL.rstrip("/")
    webhook_url = (
        f"{base}/api/v1/voice/inbound"
        if not base.endswith("/api/v1")
        else f"{base}/voice/inbound"
    )

    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }

    # ── Step 1: Create the assistant on Vapi ────────────────────────────────
    assistant_payload = {
        "name": "SanjeevaniRx PharmAI",
        "firstMessage": (
            "Hello! You've reached SanjeevaniRxAI Pharmacy. "
            "I'm Sanjeevani, your AI pharmacist. "
            "Which medicines would you like to order today?"
        ),
        "model": {
            "provider": "openai",
            "model": "gpt-4o-mini",  # Updated to gpt-4o-mini
            "messages": [{"role": "system", "content": PHARMA_SYSTEM_PROMPT}],
            "tools": [ORDER_TOOL],
            "temperature": 0.4,
        },
        "voice": {
            "provider": "openai",
            "voiceId": "nova",
        },
        "recordingEnabled": True,
        "endCallMessage": "Thank you for calling SanjeevaniRxAI Pharmacy. Get well soon! Goodbye.",
        "endCallFunctionEnabled": True,
        "maxDurationSeconds": 300,
        "serverUrl": webhook_url,
        "serverUrlSecret": os.getenv("VAPI_WEBHOOK_SECRET", ""),
    }

    async with httpx.AsyncClient(timeout=20) as client:
        # Create assistant
        create_resp = await client.post(
            "https://api.vapi.ai/assistant",
            headers=headers,
            json=assistant_payload,
        )

    if create_resp.status_code not in [200, 201]:
        raise HTTPException(
            status_code=create_resp.status_code,
            detail=f"Failed to create Vapi assistant: {create_resp.text}",
        )

    assistant_data = create_resp.json()
    assistant_id = assistant_data.get("id")
    logger.info(f"[Voice] ✅ Vapi assistant created: {assistant_id}")

    # ── Step 2: Assign assistant to the phone number ─────────────────────────
    async with httpx.AsyncClient(timeout=20) as client:
        assign_resp = await client.patch(
            f"https://api.vapi.ai/phone-number/{VAPI_PHONE_NUMBER_ID}",
            headers=headers,
            json={"assistantId": assistant_id},
        )

    if assign_resp.status_code not in [200, 201]:
        raise HTTPException(
            status_code=assign_resp.status_code,
            detail=f"Assistant created (id={assistant_id}) but failed to assign to phone: {assign_resp.text}",
        )

    logger.info(
        f"[Voice] ✅ Assistant {assistant_id} assigned to phone {VAPI_PHONE_NUMBER_ID}"
    )

    return {
        "status": "ok",
        "message": "✅ SanjeevaniRx Pharmacy AI is now live on your Vapi phone number!",
        "assistant_id": assistant_id,
        "phone_number_id": VAPI_PHONE_NUMBER_ID,
        "webhook_url": webhook_url,
        "next_step": "Call your Vapi phone number — the AI will answer and take medicine orders.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Core: Run the 6-agent pipeline for the voice order
# ─────────────────────────────────────────────────────────────────────────────


async def _handle_order_tool_call(
    call_id: str,
    caller_phone: str,
    parameters: Dict[str, Any],
) -> str:
    """
    Complete agent-based order placement using the 6-agent system.
    Provides accurate prescription checking, inventory validation, and real prices.
    """
    from langchain_core.messages import HumanMessage
    from app.agents.state import AgentState
    from app.agents.agent_6_orchestrator import orchestrator

    medicines = parameters.get("medicines", [])
    patient_name = parameters.get("patient_name") or "Voice Caller"

    if not medicines:
        return "I didn't catch that. Could you tell me the medicine name and quantity?"

    # Build natural language message for agents
    med_list = []
    for med in medicines:
        name = med.get("name", "").strip()
        qty = med.get("quantity", 1)
        if name:
            med_list.append(f"{qty} {name}")

    user_message = f"I need {', '.join(med_list)}"

    logger.info(f"[Voice] Processing order via agents: {user_message}")

    try:
        # Run through complete agent system
        initial_state = AgentState(
            messages=[HumanMessage(content=user_message)],
            extracted_meds=[],
            safety_validated=False,
            prescription_required=False,
            prescription_uploaded=False,
            validation_reasons=[],
            inventory_checked=False,
            inventory_results=[],
            fulfillment_status=None,
            final_response=None,
            channel="Voice",
            steps=[],
            current_agent=None,
            channel_metadata={
                "user_id": caller_phone,
                "patient_name": patient_name,
                "call_id": call_id,
            },
        )

        orchestrator_output = await orchestrator.ainvoke(initial_state)

        # Check results
        safety_validated = orchestrator_output.get("safety_validated", False)
        inventory_checked = orchestrator_output.get("inventory_checked", False)
        fulfillment_status = orchestrator_output.get("fulfillment_status")

        # Handle prescription requirement
        if not safety_validated:
            validation_reasons = orchestrator_output.get("validation_reasons", [])
            reasons_text = ", ".join(validation_reasons)
            return (
                f"I'm sorry, but {reasons_text}. "
                f"Please visit our pharmacy with your prescription. Thank you for calling!"
            )

        # Handle out of stock
        if not inventory_checked:
            inventory_results = orchestrator_output.get("inventory_results", [{}])
            out_of_stock = (
                inventory_results[0].get("out_of_stock", [])
                if inventory_results
                else []
            )

            if out_of_stock:
                med_names = [m.get("name", "") for m in out_of_stock]
                meds_text = ", ".join(med_names)
                return (
                    f"Sorry, {meds_text} is currently out of stock. "
                    f"Please check back soon. Thank you for calling!"
                )

        # Success - get order details with REAL prices
        if fulfillment_status == "SUCCESS":
            inventory_results = orchestrator_output.get("inventory_results", [{}])
            in_stock = (
                inventory_results[0].get("in_stock", []) if inventory_results else []
            )

            if in_stock:
                total_amount = sum([item.get("total_price", 0) for item in in_stock])
                meds_str = ", ".join(
                    [f"{item['qty']} {item['name']}" for item in in_stock]
                )

                return (
                    f"Order confirmed! Your order for {meds_str} is placed. "
                    f"Total is {total_amount} rupees. "
                    f"We'll notify you when it's ready. Thank you for calling SanjeevaniRxAI. Bye!"
                )

        # Fallback if something went wrong
        return "I wasn't able to process that order. Please try again or visit us directly."

    except Exception as e:
        logger.error(f"[Voice] Order processing error: {e}", exc_info=True)
        return "I'm having trouble processing your order right now. Please call back or visit us directly."


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Persist call report to MongoDB
# ─────────────────────────────────────────────────────────────────────────────


async def _persist_call_report(data: Dict[str, Any]):
    """Save the Vapi end-of-call report to MongoDB for dashboard display."""
    try:
        db = get_db()
        call = data.get("call", {})
        call_id = call.get("id", "")

        # Duration calculation
        started_at = data.get("startedAt", "")
        ended_at = data.get("endedAt", "")
        duration_seconds = 0
        if started_at and ended_at:
            try:
                start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                end_dt = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                duration_seconds = int((end_dt - start_dt).total_seconds())
            except Exception:
                pass

        doc = {
            "call_id": call_id,
            "caller_number": call.get("customer", {}).get("number", "Unknown"),
            "status": call.get("status", "unknown"),
            "duration_seconds": duration_seconds,
            "started_at": started_at,
            "ended_at": ended_at,
            "cost": data.get("cost", 0.0),
            "recording_url": data.get("recordingUrl", ""),
            "transcript": data.get("transcript", ""),
            "summary": data.get("summary", ""),
            "created_at": datetime.utcnow(),
        }

        db["voice_calls"].insert_one(doc)
        logger.info(
            f"[Voice] ✅ Saved call report for {call_id} (duration: {duration_seconds}s)"
        )
    except Exception as e:
        logger.error(f"[Voice] Failed to save call report: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint 2: Outbound Call (Trigger a call to a user)
# ─────────────────────────────────────────────────────────────────────────────


class OutboundCallReq(BaseModel):
    phone_number: str


@router.post("/call", summary="Trigger an outbound call to a patient via Vapi")
async def trigger_outbound_call(body: OutboundCallReq):
    """
    Useful for testing — makes Vapi call the given number with the PharmAI assistant.
    Requires VAPI_API_KEY and VAPI_PHONE_NUMBER_ID to be set.
    """
    if not VAPI_API_KEY or VAPI_API_KEY == "your_vapi_key_here":
        raise HTTPException(
            status_code=503,
            detail="VAPI_API_KEY is not configured. Add it to your .env file from vapi.ai → Dashboard → API Keys.",
        )
    if not VAPI_PHONE_NUMBER_ID or VAPI_PHONE_NUMBER_ID == "your_vapi_phone_id":
        raise HTTPException(
            status_code=503,
            detail="VAPI_PHONE_NUMBER_ID is not configured. Add the UUID from vapi.ai → Phone Numbers → copy the ID.",
        )

    phone = body.phone_number.strip()
    if not phone.startswith("+"):
        raise HTTPException(
            status_code=400, detail="Phone number must start with '+' and country code."
        )

    # We send the same config as inbound, but wrapped in an outbound payload
    call_id_placeholder = f"outbound-{datetime.utcnow().timestamp()}"
    assistant_config = build_assistant_config(call_id_placeholder, phone)

    url = "https://api.vapi.ai/call"
    headers = {
        "Authorization": f"Bearer {VAPI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer": {"number": phone},
        "assistant": assistant_config,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, json=payload)

        if resp.status_code not in [200, 201]:
            logger.error(f"Vapi outbound call failed: {resp.text}")
            raise HTTPException(
                status_code=resp.status_code,
                detail=f"Failed to start call via Vapi: {resp.text}",
            )

        data = resp.json()
        logger.info(f"Outbound call started. Vapi Call ID: {data.get('id')}")
        return {"status": "success", "call_id": data.get("id"), "vapi_response": data}

    except httpx.RequestError as e:
        logger.error(f"Network error calling Vapi API: {e}")
        raise HTTPException(status_code=500, detail=f"Network error: {str(e)}")
