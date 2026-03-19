import os
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum
from fastapi import APIRouter, Request, HTTPException, Form, Response
from pydantic import BaseModel, Field
import requests
from dotenv import load_dotenv
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
import httpx

# Load environment variables
load_dotenv()

# =============================
# CONFIGURATION
# =============================
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "verify_me")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
OCR_SPACE_API_KEY = os.getenv("OCR_SPACE_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")

WA_URL = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

# Logging Configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

router = APIRouter(tags=["WhatsApp Bot"])


# =============================
# DATA MODELS (FOR NEW ENDPOINTS)
# =============================
from app.database.models import ConversationState, OrderChannel, OrderRequest
from app.agents.state import AgentState
from app.agents.agent_6_orchestrator import orchestrator
from app.modules.dashboard_analytics import _CACHE as DASHBOARD_CACHE


# =============================
# DATABASE & AI SETUP
# =============================

# MongoDB Setup
try:
    mongo_client = AsyncIOMotorClient(MONGODB_URL)
    db = mongo_client.pharmacy_db
    users_collection = db.users
    orders_collection = db.orders
    addresses_collection = db.addresses  # New collection for addresses
    conversations_collection = db.conversations  # New collection for state management

    # Also connect to the main dashboard database
    pharmacy_management_db = mongo_client.pharmacy_management
    consumer_orders_collection = pharmacy_management_db.consumer_orders
    unified_orders_collection = (
        pharmacy_management_db.unified_orders
    )  # New collection for unified source orders
    logger.info("✅ Connected to MongoDB (pharmacy_db & pharmacy_management)")
except Exception as e:
    logger.error(f"❌ MongoDB Connection Failed: {e}")
    mongo_client = None
    users_collection = None
    orders_collection = None
    addresses_collection = None
    conversations_collection = None
    consumer_orders_collection = None
    unified_orders_collection = None

# Groq Setup
if GROQ_API_KEY:
    groq_client = Groq(api_key=GROQ_API_KEY, http_client=httpx.Client())
    logger.info("✅ Groq AI Configured")
else:
    logger.error("⚠️ GROQ_API_KEY is missing!")
    groq_client = None

# =============================
# UPDATED SYSTEM INSTRUCTIONS
# =============================
SYSTEM_INSTRUCTION = """
You are the Official Pharmacy Assistant for SanjeevaniRxAI, operating on WhatsApp.
Your goal is to provide a world-class AI experience to our customers.

You operate as part of an intelligent system using:
- FastAPI
- WhatsApp Native API
- MongoDB (user, order & address storage)
- Dual-LLM architecture (Groq for speed, Claude for reasoning)

════════════════════════════════════
STRICT ONBOARDING FLOW
════════════════════════════════════

For a NEW USER (User Profile is "Unknown"):
You must collect **Name**, **Language**, **Gender**, and **Age** in this EXACT order. Do not ask for everything at once.

**Step 1: Ask Name**
If you have NO info:
Reply Text: "👋 Welcome to Pharmastic! I see you are a new customer. Let's set up your profile.\n\n*What is your Name?*"
Intent: "ASK_NAME"

**Step 2: Ask Language**
If you have Name but NO Language:
Reply Text: "Nice to meet you, {name}!\n\n*Which language do you prefer?*"
Intent: "ASK_LANGUAGE"

**Step 3: Ask Gender**
If you have Name and Language but NO Gender:
Reply Text: "Got it.\n\n*What is your Gender?*"
Intent: "ASK_GENDER"

**Step 4: Ask Age**
If you have Name, Language, and Gender but NO Age:
Reply Text: "Almost done.\n\n*Please type your Age (e.g., 25):*"
Intent: "ASK_AGE"

**Step 5: Profile Complete**
If you just received the Age (and now have all 4 fields):
Reply Text: "✅ Profile Complete!\n\nWelcome {name}.\n\n*Which medicine do you want to order today?*\n(Type the medicine name)"
Intent: "PROFILE_COMPLETE"

════════════════════════════════════
ORDERING & ADDRESS FLOW
════════════════════════════════════

1. **Order Request**:
   - User: "I want Paracetamol"
   - You: Ask for quantity if missing. (e.g., "How many Paracetamol do you want?")
   - Intent: "ORDER_MEDICINE"

2. **Quantity Confirmation**:
   - User provides quantity
   - You: Show price and ask to confirm
   - Intent: "CONFIRM_ORDER"

3. **Address Collection**:
   AFTER order confirmation, you MUST collect delivery address:

   **Check for Existing Addresses**:
   If user has saved addresses:
   - Reply Text: "Please select delivery address:"
   - Intent: "SELECT_ADDRESS"
   (System will show buttons with saved addresses)

   If NO saved addresses OR user selects "Add New":
   - Step 1: Ask for Address Line 1
     Reply Text: "Please enter your *Address Line 1* (Street, building, etc.):"
     Intent: "ASK_ADDRESS_LINE1"
   
   - Step 2: Ask for Address Line 2 (optional)
     Reply Text: "Enter *Address Line 2* (Area/locality) or type 'Skip':"
     Intent: "ASK_ADDRESS_LINE2"
   
   - Step 3: Ask for City
     Reply Text: "Enter your *City*:"
     Intent: "ASK_CITY"
   
   - Step 4: Ask for State
     Reply Text: "Enter your *State*:"
     Intent: "ASK_STATE"
   
   - Step 5: Ask for Pincode
     Reply Text: "Enter your *Pincode* (6 digits):"
     Intent: "ASK_PINCODE"
   
   - Step 6: Ask for Landmark (optional)
     Reply Text: "Enter a *Landmark* for easy delivery or type 'Skip':"
     Intent: "ASK_LANDMARK"
   
   - Step 7: Ask to Save Address
     Reply Text: "Would you like to save this address for future orders?"
     Intent: "SAVE_ADDRESS"
     (Buttons: Yes, Save | No, Use Once)

4. **Address Confirmation**:
   After address collection:
   - Show full address summary
   - Intent: "ADDRESS_CONFIRMED"

5. **Final Order**:
   Reply Text: "✅ *Order Placed Successfully!*\n\nYour order will be delivered to:\n{address}\n\nWe will notify you when it ships. *Order ID: #{order_id}*"
   Intent: "ORDER_PLACED"

════════════════════════════════════
ORDER TRACKING
════════════════════════════════════

If user asks about order status:
- Show recent orders with status
- Intent: "TRACK_ORDER"

════════════════════════════════════
OUTPUT FORMAT (MANDATORY JSON)
════════════════════════════════════

Always respond ONLY in JSON.

{
  "intent": "ASK_NAME" | "ASK_LANGUAGE" | "ASK_GENDER" | "ASK_AGE" | "PROFILE_COMPLETE" | "ORDER_MEDICINE" | "CONFIRM_ORDER" | "SELECT_ADDRESS" | "ASK_ADDRESS_LINE1" | "ASK_ADDRESS_LINE2" | "ASK_CITY" | "ASK_STATE" | "ASK_PINCODE" | "ASK_LANDMARK" | "SAVE_ADDRESS" | "ADDRESS_CONFIRMED" | "ORDER_PLACED" | "TRACK_ORDER" | "GENERAL",
  "user_info": {
    "name": "Extracted Name",
    "age": 25,
    "gender": "Male",
    "language": "English"
  },
  "medicine_name": "...",
  "quantity": "...",
  "price": 250,
  "address_info": {
    "address_line1": "...",
    "address_line2": "...",
    "city": "...",
    "state": "...",
    "pincode": "...",
    "landmark": "...",
    "address_type": "Home/Office/Other",
    "save_address": true/false
  },
  "order_id": "...",
  "reply_text": "The exact string you want to send to the user"
}
"""

# =============================
# ENHANCED DATABASE HELPERS
# =============================


async def get_user_profile(phone: str) -> Optional[Dict]:
    if users_collection is None:
        return None
    return await users_collection.find_one({"user_id": phone})


async def update_user_profile(phone: str, user_data: Dict):
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": phone})

    # Clean None values
    update_data = {k: v for k, v in user_data.items() if v is not None}
    update_data["user_id"] = phone

    if not existing:
        update_data["created_at"] = datetime.utcnow()
        await users_collection.insert_one(update_data)
    else:
        await users_collection.update_one({"user_id": phone}, {"$set": update_data})


async def get_conversation_state(phone: str) -> Dict:
    """Get or create conversation state for user"""
    if conversations_collection is None:
        return {"state": ConversationState.GENERAL, "temp_data": {}}

    state = await conversations_collection.find_one({"user_id": phone})
    if not state:
        state = {
            "user_id": phone,
            "state": ConversationState.GENERAL,
            "temp_data": {},
            "updated_at": datetime.utcnow(),
        }
        await conversations_collection.insert_one(state)
    return state


async def update_conversation_state(phone: str, new_state: str, temp_data: Dict = None):
    """Update conversation state for user"""
    if conversations_collection is None:
        return

    update = {"state": new_state, "updated_at": datetime.utcnow()}
    if temp_data is not None:
        update["temp_data"] = temp_data

    await conversations_collection.update_one(
        {"user_id": phone}, {"$set": update}, upsert=True
    )


async def save_user_address(phone: str, address_data: Dict) -> str:
    """Save user address and return address ID"""
    if addresses_collection is None:
        return None

    address = {
        "user_id": phone,
        "address_line1": address_data.get("address_line1"),
        "address_line2": address_data.get("address_line2"),
        "city": address_data.get("city"),
        "state": address_data.get("state"),
        "pincode": address_data.get("pincode"),
        "landmark": address_data.get("landmark"),
        "address_type": address_data.get("address_type", "Home"),
        "is_default": address_data.get("is_default", False),
        "created_at": datetime.utcnow(),
    }

    # If this is set as default, remove default from others
    if address["is_default"]:
        await addresses_collection.update_many(
            {"user_id": phone, "is_default": True}, {"$set": {"is_default": False}}
        )

    result = await addresses_collection.insert_one(address)
    return str(result.inserted_id)


async def get_user_addresses(phone: str) -> List[Dict]:
    """Get all addresses for user"""
    if addresses_collection is None:
        return []

    cursor = addresses_collection.find({"user_id": phone}).sort("is_default", -1)
    addresses = await cursor.to_list(length=10)
    return addresses


async def get_default_address(phone: str) -> Optional[Dict]:
    """Get user's default address"""
    if addresses_collection is None:
        return None

    return await addresses_collection.find_one({"user_id": phone, "is_default": True})


async def create_order(phone: str, order_info: Dict):
    """Create a new order with REAL database prices"""
    if orders_collection is None:
        return None

    # Generate order ID
    order_id = f"ORD{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{phone[-4:]}"

    order_data = {
        "order_id": order_id,
        "user_id": phone,
        "medicine_name": order_info.get("medicine_name"),
        "product_id": order_info.get("product_id", ""),
        "quantity": (
            float(order_info.get("quantity", 1))
            if order_info.get("quantity") and str(order_info["quantity"]).strip()
            else 1.0
        ),
        "price": (
            float(order_info.get("price", 0))
            if order_info.get("price") and str(order_info["price"]).strip()
            else 0.0
        ),  # Real price from Agent 4
        "total_amount": (
            float(order_info.get("total_amount", 0))
            if order_info.get("total_amount")
            and str(order_info["total_amount"]).strip()
            else 0.0
        ),
        "delivery_address": order_info.get("delivery_address"),
        "address_id": order_info.get("address_id"),
        "order_status": order_info.get("order_status", "confirmed"),
        "payment_status": "pending",
        "requires_prescription": order_info.get("requires_prescription", "No"),
        "available_stock": order_info.get("available_stock", 0),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await orders_collection.insert_one(order_data)
    logger.info(f"📦 Order saved to orders collection: {order_id}")
    order_ts = datetime.utcnow()

    # Also insert into pharmacy_management.consumer_orders (Dashboard)
    if consumer_orders_collection is not None:
        user_profile = await get_user_profile(phone)
        patient_name = (
            user_profile.get("name", "Unknown Patient")
            if user_profile
            else "Unknown Patient"
        )
        age = user_profile.get("age", 0) if user_profile else 0
        gender = user_profile.get("gender", "Unknown") if user_profile else "Unknown"

        # Build address string
        addr_dict = order_info.get("delivery_address", {})
        addr_str = f"{addr_dict.get('address_line1', '')} {addr_dict.get('address_line2', '')} {addr_dict.get('city', '')} {addr_dict.get('state', '')} {addr_dict.get('pincode', '')}".strip()

        consumer_order_data = {
            "Patient Name": patient_name,
            "Patient ID": phone,
            "Age": age,
            "Gender": gender,
            "Contact Number": phone,
            "Address": addr_str,
            "Order ID": order_id,
            "Order Date": order_ts,
            "Order Channel": "WhatsApp",
            "Order Status": "Pending",
            "Medicine Name": order_info.get("medicine_name"),
            "Product ID": order_info.get("product_id", ""),
            "Quantity Ordered": (
                float(order_info.get("quantity", 1))
                if order_info.get("quantity") and str(order_info["quantity"]).strip()
                else 1.0
            ),
            "Unit Price": (
                float(order_info.get("price", 0))
                if order_info.get("price") and str(order_info["price"]).strip()
                else 0.0
            ),  # Real price!
            "Total Amount": (
                float(order_info.get("total_amount", 0))
                if order_info.get("total_amount")
                and str(order_info["total_amount"]).strip()
                else 0.0
            ),
            "Requires Prescription": order_info.get("requires_prescription", "No"),
        }
        await consumer_orders_collection.insert_one(consumer_order_data)
        logger.info(f"📊 Order saved to consumer_orders (Dashboard): {order_id}")

    # Also sync with unified_orders collection
    if unified_orders_collection is not None:
        user_profile = await get_user_profile(phone)
        patient_name = (
            user_profile.get("name", "Unknown Patient")
            if user_profile
            else "Unknown Patient"
        )
        age = user_profile.get("age", 0) if user_profile else 0
        gender = user_profile.get("gender", "Unknown") if user_profile else "Unknown"

        addr_dict = order_info.get("delivery_address", {})
        addr_str = f"{addr_dict.get('address_line1', '')} {addr_dict.get('address_line2', '')} {addr_dict.get('city', '')} {addr_dict.get('state', '')} {addr_dict.get('pincode', '')}".strip()

        unified_doc = {
            "order_id": order_id,
            "patient_name": patient_name,
            "patient_id": phone,
            "age": age,
            "gender": gender,
            "contact_number": phone,
            "address": addr_str,
            "medicine_name": order_info.get("medicine_name"),
            "product_id": order_info.get("product_id", ""),
            "quantity": (
                float(order_info.get("quantity", 1))
                if order_info.get("quantity") and str(order_info["quantity"]).strip()
                else 1.0
            ),
            "unit_price": (
                float(order_info.get("price", 0))
                if order_info.get("price") and str(order_info["price"]).strip()
                else 0.0
            ),  # Real price!
            "total_amount": (
                float(order_info.get("total_amount", 0))
                if order_info.get("total_amount")
                and str(order_info["total_amount"]).strip()
                else 0.0
            ),
            "order_channel": "WhatsApp",
            "order_status": "Pending",
            "order_date": order_ts,
            "requires_prescription": order_info.get("requires_prescription", "No"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await unified_orders_collection.insert_one(unified_doc)
        logger.info(f"🔄 Order saved to unified_orders: {order_id}")

    # Invalidate dashboard cache so UI picks up new WhatsApp orders immediately.
    DASHBOARD_CACHE.clear()

    return order_id


async def get_recent_orders(phone: str) -> List[Dict]:
    """Get recent orders for user"""
    if orders_collection is None:
        return []
    cursor = orders_collection.find({"user_id": phone}).sort("created_at", -1).limit(3)
    orders = await cursor.to_list(length=3)
    return [
        {
            "order_id": o.get("order_id"),
            "medicine_name": o.get("medicine_name"),
            "quantity": o.get("quantity"),
            "total_amount": o.get("total_amount"),
            "status": o.get("order_status"),
            "date": (
                o.get("created_at").isoformat() if o.get("created_at") else "Unknown"
            ),
        }
        for o in orders
    ]


async def update_order_with_address(
    order_id: str, address_id: str, address_details: Dict
):
    """Update order with address information"""
    if orders_collection is None:
        return

    await orders_collection.update_one(
        {"order_id": order_id},
        {
            "$set": {
                "address_id": address_id,
                "delivery_address": address_details,
                "order_status": "address_confirmed",
                "updated_at": datetime.utcnow(),
            }
        },
    )

    # Update in pharmacy_management.consumer_orders if present
    if consumer_orders_collection is not None:
        addr_str = f"{address_details.get('address_line1', '')} {address_details.get('address_line2', '')} {address_details.get('city', '')} {address_details.get('state', '')} {address_details.get('pincode', '')}".strip()
        await consumer_orders_collection.update_one(
            {"Order ID": order_id},
            {"$set": {"Address": addr_str, "Order Status": "Address Confirmed"}},
        )

    # Update in pharmacy_management.unified_orders if present
    if unified_orders_collection is not None:
        addr_str = f"{address_details.get('address_line1', '')} {address_details.get('address_line2', '')} {address_details.get('city', '')} {address_details.get('state', '')} {address_details.get('pincode', '')}".strip()
        await unified_orders_collection.update_one(
            {"order_id": order_id},
            {
                "$set": {
                    "address": addr_str,
                    "order_status": "Address Confirmed",
                    "updated_at": datetime.utcnow(),
                }
            },
        )


# =============================
# WHATSAPP API HELPERS
# =============================
def send_whatsapp_text(to: str, text: str):
    if not WHATSAPP_TOKEN:
        return
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {"messaging_product": "whatsapp", "to": to, "text": {"body": text}}
    try:
        requests.post(WA_URL, headers=headers, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Send Text Error: {e}")


def send_whatsapp_buttons(to: str, text: str, buttons: List[Dict[str, str]]):
    """
    buttons format: [{"id": "btn_1", "title": "Button 1"}]
    """
    if not WHATSAPP_TOKEN:
        return
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    # Construct button objects
    btn_objects = []
    for btn in buttons:
        # Title limit is 20 chars for buttons
        safe_title = btn["title"][:20]
        btn_objects.append(
            {"type": "reply", "reply": {"id": btn["id"], "title": safe_title}}
        )

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": text},
            "action": {"buttons": btn_objects},
        },
    }
    try:
        requests.post(WA_URL, headers=headers, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Send Buttons Error: {e}")


def send_whatsapp_list(to: str, text: str, button_text: str, sections: List[Dict]):
    if not WHATSAPP_TOKEN:
        return
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": text},
            "action": {"button": button_text, "sections": sections},
        },
    }
    try:
        requests.post(WA_URL, headers=headers, json=payload, timeout=10)
    except Exception as e:
        logger.error(f"Send List Error: {e}")


def _get_whatsapp_auth_headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}


def _download_whatsapp_media(media_id: str) -> Optional[bytes]:
    """
    Download incoming WhatsApp media bytes using Graph API:
    1) Resolve media URL from media ID
    2) Download binary with auth header
    """
    if not WHATSAPP_TOKEN or not media_id:
        return None

    try:
        meta_url = f"https://graph.facebook.com/v19.0/{media_id}"
        meta_resp = requests.get(
            meta_url, headers=_get_whatsapp_auth_headers(), timeout=15
        )
        if meta_resp.status_code != 200:
            logger.error(
                f"Failed to resolve media URL ({meta_resp.status_code}): {meta_resp.text}"
            )
            return None

        media_url = meta_resp.json().get("url")
        if not media_url:
            logger.error("Media URL missing in Graph API response")
            return None

        media_resp = requests.get(
            media_url, headers=_get_whatsapp_auth_headers(), timeout=20
        )
        if media_resp.status_code != 200:
            logger.error(
                f"Failed to download media ({media_resp.status_code}): {media_resp.text}"
            )
            return None

        return media_resp.content
    except Exception as exc:
        logger.error(f"Media download error: {exc}")
        return None


def _extract_text_with_ocr_space(image_bytes: bytes, filename: str = "image.jpg") -> str:
    """Run OCR.Space on image bytes and return extracted text (empty string on failure)."""
    if not OCR_SPACE_API_KEY:
        logger.error("OCR_SPACE_API_KEY is missing in environment.")
        return ""

    try:
        ocr_url = "https://api.ocr.space/parse/image"
        files = {"file": (filename, image_bytes)}
        data = {
            "apikey": OCR_SPACE_API_KEY,
            "language": "eng",
            "isOverlayRequired": False,
        }
        resp = requests.post(ocr_url, files=files, data=data, timeout=30)
        if resp.status_code != 200:
            logger.error(f"OCR API failed ({resp.status_code}): {resp.text}")
            return ""

        result = resp.json()
        if result.get("IsErroredOnProcessing"):
            logger.error(f"OCR processing error: {result.get('ErrorMessage')}")
            return ""

        parsed = result.get("ParsedResults") or []
        if not parsed:
            return ""
        return (parsed[0].get("ParsedText") or "").strip()
    except Exception as exc:
        logger.error(f"OCR extraction error: {exc}")
        return ""


# =============================
# AI PROCESSING
# =============================
def process_ai_interaction(
    user_text: str,
    user_profile: Optional[Dict],
    recent_orders: List[Dict],
    user_addresses: List[Dict],
    conversation_state: Dict,
) -> Dict:
    if not groq_client:
        return {"intent": "ERROR", "reply_text": "AI not configured."}

    # Context Building
    profile_str = "Unknown (First time user)"
    if user_profile:
        profile_str = json.dumps(
            {
                "name": user_profile.get("name"),
                "age": user_profile.get("age"),
                "gender": user_profile.get("gender"),
                "language": user_profile.get("language"),
            },
            indent=2,
        )

    addresses_str = "No saved addresses"
    if user_addresses:
        addresses_str = json.dumps(
            [
                {
                    "type": a.get("address_type"),
                    "line1": a.get("address_line1"),
                    "city": a.get("city"),
                    "pincode": a.get("pincode"),
                    "is_default": a.get("is_default"),
                }
                for a in user_addresses
            ],
            indent=2,
        )

    msg_context = f"""
    CURRENT USER DATA: {profile_str}
    RECENT ORDERS: {json.dumps(recent_orders)}
    SAVED ADDRESSES: {addresses_str}
    CONVERSATION STATE: {conversation_state.get('state', 'GENERAL')}
    TEMP DATA: {json.dumps(conversation_state.get('temp_data', {}))}
    USER MESSAGE: "{user_text}"
    """

    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": msg_context},
            ],
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return json.loads(completion.choices[0].message.content.strip())
    except Exception as e:
        logger.error(f"AI Error: {e}")
        return {
            "intent": "ERROR",
            "reply_text": "Sorry, I'm having trouble thinking right now.",
        }


# =============================
# ADDRESS COLLECTION HELPER
# =============================
async def handle_address_collection(
    user_number: str, user_text: str, conversation_state: Dict
):
    """Handle step-by-step address collection"""
    temp_data = conversation_state.get("temp_data", {})
    current_step = temp_data.get("step", "line1")

    if "address_info" not in temp_data:
        temp_data["address_info"] = {}

    # Process based on current step
    if current_step == "select":
        if user_text == "➕ Add New Address":
            temp_data["step"] = "line1"
            await update_conversation_state(
                user_number, ConversationState.ORDERING_ADDRESS, temp_data
            )
            send_whatsapp_text(
                user_number,
                "Please enter your *Address Line 1* (Street, building, etc.):",
            )
            return
        elif user_text.startswith("addr_"):
            # User selected saved address
            try:
                addr_index = int(user_text.split("_")[1])
                addresses = temp_data.get("addresses", [])
                if addr_index < len(addresses):
                    selected_addr = addresses[addr_index]
                    temp_data["address_info"] = selected_addr
                    temp_data["step"] = "confirm"
                    await update_conversation_state(
                        user_number,
                        ConversationState.ORDERING_ADDRESS_CONFIRM,
                        temp_data,
                    )

                    # Show address and ask for confirmation
                    address_str = format_address_string(selected_addr)
                    buttons = [
                        {"id": "confirm_addr", "title": "Confirm Address"},
                        {"id": "new_addr", "title": "Use Different"},
                    ]
                    send_whatsapp_buttons(
                        user_number,
                        f"Deliver to this address?\n\n{address_str}",
                        buttons,
                    )
                    return
            except (ValueError, IndexError):
                pass

    # Handle confirmation from address selection
    if current_step == "confirm":
        if user_text == "Confirm Address":
            # Complete order with selected address
            await complete_order_with_address(user_number, temp_data)
        elif user_text == "Use Different":
            # Show addresses again
            await show_address_selection(user_number, temp_data)
        return

    # Regular address collection steps
    steps_order = ["line1", "line2", "city", "state", "pincode", "landmark"]
    step_index = steps_order.index(current_step) if current_step in steps_order else 0

    # Save current input
    reply = ""

    if current_step == "line1":
        temp_data["address_info"]["address_line1"] = user_text
        temp_data["step"] = "line2"
        reply = "Enter *Address Line 2* (Area/locality) or type 'Skip':"

    elif current_step == "line2":
        if user_text.lower() != "skip":
            temp_data["address_info"]["address_line2"] = user_text
        temp_data["step"] = "city"
        reply = "Enter your *City*:"

    elif current_step == "city":
        temp_data["address_info"]["city"] = user_text
        temp_data["step"] = "state"
        reply = "Enter your *State*:"

    elif current_step == "state":
        temp_data["address_info"]["state"] = user_text
        temp_data["step"] = "pincode"
        reply = "Enter your *Pincode* (6 digits):"

    elif current_step == "pincode":
        if re.match(r"^\d{6}$", user_text):
            temp_data["address_info"]["pincode"] = user_text
            temp_data["step"] = "landmark"
            reply = "Enter a *Landmark* for easy delivery or type 'Skip':"
        else:
            reply = "❌ Invalid pincode. Please enter 6 digits:"
            send_whatsapp_text(user_number, reply)
            await update_conversation_state(
                user_number, ConversationState.ORDERING_ADDRESS, temp_data
            )
            return

    elif current_step == "landmark":
        if user_text.lower() != "skip":
            temp_data["address_info"]["landmark"] = user_text

        # Ask if they want to save address
        address_str = format_address_string(temp_data["address_info"])
        buttons = [
            {"id": "save_yes", "title": "Yes, Save"},
            {"id": "save_no", "title": "No, Use Once"},
        ]
        await update_conversation_state(
            user_number, ConversationState.ORDERING_ADDRESS_CONFIRM, temp_data
        )
        send_whatsapp_buttons(
            user_number,
            f"Confirm your address:\n\n{address_str}\n\nSave this address for future?",
            buttons,
        )
        return

    # Update state and send next question
    await update_conversation_state(
        user_number, ConversationState.ORDERING_ADDRESS, temp_data
    )
    send_whatsapp_text(user_number, reply)


async def show_address_selection(user_number: str, temp_data: Dict):
    """Show saved addresses for selection"""
    addresses = temp_data.get("addresses", [])
    if addresses:
        buttons = []
        for i, addr in enumerate(addresses[:3]):  # Max 3 buttons
            addr_summary = (
                f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:15]}..."
            )
            buttons.append({"id": f"addr_{i}", "title": addr_summary})
        buttons.append({"id": "addr_new", "title": "➕ Add New Address"})

        temp_data["step"] = "select"
        await update_conversation_state(
            user_number, ConversationState.ORDERING_ADDRESS, temp_data
        )
        send_whatsapp_buttons(user_number, "Select delivery address:", buttons)
    else:
        # No addresses, start fresh collection
        temp_data["step"] = "line1"
        await update_conversation_state(
            user_number, ConversationState.ORDERING_ADDRESS, temp_data
        )
        send_whatsapp_text(
            user_number, "Please enter your *Address Line 1* (Street, building, etc.):"
        )


async def complete_order_with_address(user_number: str, temp_data: Dict):
    """Complete the order with the collected address using REAL agent data"""

    # Get orchestrator output with real prices
    orchestrator_output = temp_data.get("orchestrator_output", {})
    inventory_results = orchestrator_output.get("inventory_results", [{}])
    in_stock = inventory_results[0].get("in_stock", []) if inventory_results else []

    # Handle save address choice
    save_address = temp_data.get("save_address", False)
    address_id = None
    if save_address:
        address_id = await save_user_address(user_number, temp_data["address_info"])

    order_ids = []

    if in_stock:
        # Use REAL data from Agent 4 inventory results
        for item in in_stock:
            order_data = {
                "medicine_name": item.get("name"),
                "product_id": item.get("product_id", ""),
                "quantity": item.get("qty", 1),
                "price": item.get("price", 0),  # REAL price from database!
                "total_amount": item.get("total_price", 0),
                "delivery_address": temp_data.get("address_info", {}),
                "address_id": address_id,
                "order_status": "confirmed",
                "requires_prescription": item.get("requires_prescription", "No"),
                "available_stock": item.get("available_stock", 0),
            }
            order_id = await create_order(user_number, order_data)
            order_ids.append(order_id)
            logger.info(
                f"✅ Order created: {order_id} - {item['name']} x{item['qty']} @ ₹{item['price']}"
            )
    else:
        # Fallback to temp_data (old method) if no agent results
        logger.warning("⚠️ No agent results found, using temp_data fallback")
        medicine_name = temp_data.get("medicine_name", "Medicine")
        quantity = temp_data.get("quantity", 1)
        price = temp_data.get("price", 0)
        # Ensure quantity and price are integers and not empty strings
        try:
            quantity_val = int(quantity) if quantity and str(quantity).strip() else 1
        except (ValueError, TypeError):
            quantity_val = 1

        try:
            price_val = int(price) if price and str(price).strip() else 0
        except (ValueError, TypeError):
            price_val = 0

        order_data = {
            "medicine_name": medicine_name,
            "quantity": quantity_val,
            "price": price_val,
            "total_amount": quantity_val * price_val,
            "delivery_address": temp_data.get("address_info", {}),
            "address_id": address_id,
            "order_status": "confirmed",
        }
        order_id = await create_order(user_number, order_data)
        order_ids.append(order_id)

    return order_ids[0] if order_ids else None


def format_address_string(address: Dict) -> str:
    """Format address dictionary into readable string"""
    parts = []
    if address.get("address_line1"):
        parts.append(address["address_line1"])
    if address.get("address_line2"):
        parts.append(address["address_line2"])
    if address.get("landmark"):
        parts.append(f"Near {address['landmark']}")
    if address.get("city"):
        parts.append(address["city"])
    if address.get("state"):
        parts.append(address["state"])
    if address.get("pincode"):
        parts.append(f"PIN: {address['pincode']}")

    return "\n".join(parts)


async def handle_onboarding(user_number: str, user_text: str, user_profile: Optional[Dict]):
    """Handle step-by-step user onboarding (Name, Language, Gender, Age)"""
    
    # Step 1: Ask for Name
    if not user_profile or not user_profile.get("name"):
        if user_text.lower() in ["hi", "hello", "hey", "start"]:
            send_whatsapp_text(
                user_number,
                "👋 Welcome to SanjeevaniRxAI! I'm your pharmacy assistant.\n\nLet's set up your profile.\n\n*What is your Name?*"
            )
        else:
            # User provided name
            await update_user_profile(user_number, {"name": user_text})
            send_whatsapp_text(
                user_number,
                f"Nice to meet you, {user_text}!\n\n*Which language do you prefer?*\n(Reply: English or Hindi)"
            )
        return
    
    # Step 2: Ask for Language
    if not user_profile.get("language"):
        language = "English" if "eng" in user_text.lower() else "Hindi" if "hin" in user_text.lower() else user_text
        await update_user_profile(user_number, {"language": language})
        send_whatsapp_text(
            user_number,
            f"Got it!\n\n*What is your Gender?*\n(Reply: Male or Female)"
        )
        return
    
    # Step 3: Ask for Gender
    if not user_profile.get("gender"):
        gender = "Male" if "male" in user_text.lower() or "m" == user_text.lower() else "Female" if "female" in user_text.lower() or "f" == user_text.lower() else user_text
        await update_user_profile(user_number, {"gender": gender})
        send_whatsapp_text(
            user_number,
            "Almost done!\n\n*Please type your Age* (e.g., 25):"
        )
        return
    
    # Step 4: Ask for Age
    if not user_profile.get("age"):
        try:
            age = int(user_text)
            await update_user_profile(user_number, {"age": age})
            
            # Profile complete!
            user_profile = await get_user_profile(user_number)
            name = user_profile.get("name", "there")
            
            send_whatsapp_text(
                user_number,
                f"✅ *Profile Complete!*\n\nWelcome {name}! 🎉\n\n*Which medicine do you want to order today?*\n(Type the medicine name)"
            )
        except ValueError:
            send_whatsapp_text(
                user_number,
                "Please enter a valid age (numbers only):"
            )
        return


# =============================
# WEBHOOKS
# =============================
@router.get("/webhook")
async def verify_webhook(request: Request):
    p = dict(request.query_params)
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return int(p.get("hub.challenge", 0))
    return "Verification failed"


@router.post("/webhook")
async def handle_message(request: Request):
    try:
        data = await request.json()
    except:
        return {"status": "no_json"}

    # Basic Validation
    try:
        entry = data["entry"][0]
        changes = entry["changes"][0]
        value = changes["value"]
        messages = value.get("messages", [])
    except (KeyError, IndexError):
        return {"status": "ignored"}

    if not messages:
        return {"status": "no_message"}

    msg = messages[0]
    user_number = msg.get("from")

    # Extract Text from various message types
    user_text = ""
    interactive_data = None
    is_image = False
    media_id = None

    if msg.get("type") == "text":
        user_text = msg["text"]["body"]
    elif msg.get("type") == "image":
        user_text = "IMAGE_UPLOADED"
        is_image = True
        media_id = (msg.get("image") or {}).get("id")
        logger.info(f"📸 Image received from {user_number}")
    elif msg.get("type") == "interactive":
        interactive = msg["interactive"]
        if interactive["type"] == "button_reply":
            user_text = interactive["button_reply"]["title"]
            interactive_data = {
                "type": "button",
                "id": interactive["button_reply"]["id"],
            }
        elif interactive["type"] == "list_reply":
            user_text = interactive["list_reply"]["title"]
            interactive_data = {"type": "list", "id": interactive["list_reply"]["id"]}

    if not user_text:
        return {"status": "unknown_message_type"}

    logger.info(f"📩 Message from {user_number}: {user_text}")

    # Get User Context
    user_profile = await get_user_profile(user_number)
    recent_orders = await get_recent_orders(user_number)
    conversation_state = await get_conversation_state(user_number)
    user_addresses = await get_user_addresses(user_number)

    # STEP 1: Check if user profile is complete (Name, Language, Gender, Age)
    if not user_profile or not all([
        user_profile.get("name"),
        user_profile.get("language"),
        user_profile.get("gender"),
        user_profile.get("age")
    ]):
        # User needs onboarding - collect profile step by step
        await handle_onboarding(user_number, user_text, user_profile)
        return {"status": "onboarding"}

    # Handle Prescription Upload
    if is_image:
        temp_data = conversation_state.get("temp_data", {})
        if not temp_data.get("awaiting_prescription", False):
            send_whatsapp_text(
                user_number,
                "Image received. Please share medicine details first. I will ask for prescription only when required.",
            )
            return {"status": "image_ignored_not_required"}

        if not media_id:
            send_whatsapp_text(
                user_number,
                "I could not read the image metadata. Please send the prescription image again.",
            )
            return {"status": "image_missing_media_id"}

        image_bytes = _download_whatsapp_media(media_id)
        if not image_bytes:
            send_whatsapp_text(
                user_number,
                "I could not download the image. Please resend a clear prescription photo.",
            )
            return {"status": "image_download_failed"}

        extracted_text = _extract_text_with_ocr_space(
            image_bytes, filename=f"prescription_{user_number}.jpg"
        )
        if not extracted_text:
            send_whatsapp_text(
                user_number,
                "I could not extract text from this image. Please send a clearer prescription image.",
            )
            return {"status": "ocr_failed"}

        temp_data["prescription_uploaded"] = True
        temp_data["awaiting_prescription"] = False
        temp_data["prescription_ocr_text"] = extracted_text[:4000]
        await update_conversation_state(user_number, conversation_state["state"], temp_data)

        preview = extracted_text[:250].replace("\n", " ").strip()
        send_whatsapp_text(
            user_number,
            f"✅ Prescription received and text extracted.\n\nOCR Preview: {preview}\n\nNow please confirm your medicine request again.",
        )
        return {"status": "prescription_received", "ocr_preview": preview}

    # Handle Address Selection Flow (State-based)
    if conversation_state["state"] == ConversationState.ORDERING_ADDRESS:
        await handle_address_collection(user_number, user_text, conversation_state)
        return {"status": "address_processing"}

    elif conversation_state["state"] == ConversationState.ORDERING_ADDRESS_CONFIRM:
        temp_data = conversation_state.get("temp_data", {})

        if interactive_data and interactive_data["id"] == "save_yes":
            temp_data["save_address"] = True
            await complete_order_with_address(user_number, temp_data)
        elif interactive_data and interactive_data["id"] == "save_no":
            temp_data["save_address"] = False
            await complete_order_with_address(user_number, temp_data)
        elif user_text.lower() in ["yes", "confirm", "save"]:
            temp_data["save_address"] = True
            await complete_order_with_address(user_number, temp_data)
        else:
            # Start address collection again
            await show_address_selection(user_number, temp_data)

        return {"status": "order_processed"}
    
    # Handle Order Confirmation (when user clicks "Confirm Order" button)
    elif conversation_state["state"] == ConversationState.ORDER_CONFIRMED:
        if interactive_data and interactive_data["id"] == "confirm_yes":
            # User confirmed order, now collect address
            temp_data = conversation_state.get("temp_data", {})
            addresses = await get_user_addresses(user_number)
            
            if addresses:
                await show_address_selection(user_number, temp_data)
            else:
                # No saved addresses, start collection
                temp_data["step"] = "line1"
                await update_conversation_state(
                    user_number, ConversationState.ORDERING_ADDRESS, temp_data
                )
                send_whatsapp_text(
                    user_number,
                    "Please enter your *Address Line 1* (Street, building, etc.):"
                )
            return {"status": "address_request"}
        elif interactive_data and interactive_data["id"] == "confirm_no":
            # User cancelled order
            await update_conversation_state(user_number, ConversationState.GENERAL, {})
            send_whatsapp_text(user_number, "Order cancelled. How else can I help you?")
            return {"status": "order_cancelled"}

    # Process AI Intent for regular flow (RESTORED FIX)
    ai_response = process_ai_interaction(
        user_text, user_profile, recent_orders, user_addresses, conversation_state
    )
    intent = ai_response.get("intent", "GENERAL")
    reply_text = ai_response.get("reply_text", "I'm having trouble processing that.")

    if intent == "SELECT_ADDRESS":
        # Show saved addresses
        addresses = await get_user_addresses(user_number)
        temp_data = conversation_state.get("temp_data", {})
        temp_data["addresses"] = addresses

        if addresses:
            await show_address_selection(user_number, temp_data)
        else:
            # No saved addresses, start collection
            temp_data["step"] = "line1"
            await update_conversation_state(
                user_number, ConversationState.ORDERING_ADDRESS, temp_data
            )
            send_whatsapp_text(
                user_number,
                "Please enter your *Address Line 1* (Street, building, etc.):",
            )

    elif intent in [
        "ASK_ADDRESS_LINE1",
        "ASK_ADDRESS_LINE2",
        "ASK_CITY",
        "ASK_STATE",
        "ASK_PINCODE",
        "ASK_LANDMARK",
    ]:
        # Update conversation state for address collection
        step_map = {
            "ASK_ADDRESS_LINE1": "line1",
            "ASK_ADDRESS_LINE2": "line2",
            "ASK_CITY": "city",
            "ASK_STATE": "state",
            "ASK_PINCODE": "pincode",
            "ASK_LANDMARK": "landmark",
        }

        current_step = step_map.get(intent, "line1")
        temp_data = conversation_state.get("temp_data", {})

        if "address_info" not in temp_data:
            temp_data["address_info"] = {}

        temp_data["step"] = current_step
        await update_conversation_state(
            user_number, ConversationState.ORDERING_ADDRESS, temp_data
        )
        send_whatsapp_text(user_number, reply_text)

    elif intent == "SAVE_ADDRESS":
        buttons = [
            {"id": "save_yes", "title": "Yes, Save"},
            {"id": "save_no", "title": "No, Use Once"},
        ]
        send_whatsapp_buttons(user_number, reply_text, buttons)

    elif intent == "ORDER_PLACED":
        # Clear conversation state
        await update_conversation_state(user_number, ConversationState.GENERAL, {})
        send_whatsapp_text(user_number, reply_text)

    elif intent == "TRACK_ORDER":
        if recent_orders:
            track_text = "*📦 Your Recent Orders:*\n\n"
            for order in recent_orders:
                track_text += f"• *{order['medicine_name']}* - Qty: {order['quantity']}\n  Status: {order['status']}\n  Order: #{order['order_id']}\n\n"
            send_whatsapp_text(user_number, track_text)
        else:
            send_whatsapp_text(user_number, "You haven't placed any orders yet.")

    elif intent == "GENERAL" or intent == "ORDER_MEDICINE":
        # Use the Langchain Orchestrator for medicine orders
        try:
            from langchain_core.messages import HumanMessage

            initial_state = AgentState(
                messages=[HumanMessage(content=user_text)],
                extracted_meds=[],
                safety_validated=True,
                prescription_required=False,
                prescription_uploaded=conversation_state.get("temp_data", {}).get(
                    "prescription_uploaded", False
                ),
                validation_reasons=[],
                inventory_checked=True,
                inventory_results=[],
                fulfillment_status=None,
                final_response=None,
                channel="WhatsApp",
                channel_metadata={
                    "user_id": user_number,
                    "interactive_data": interactive_data,
                    "prescription_ocr_text": conversation_state.get("temp_data", {}).get(
                        "prescription_ocr_text", ""
                    ),
                },
                steps=[],
                current_agent=None,
            )

            orchestrator_output = await orchestrator.ainvoke(initial_state)
            
            # Check if prescription is required
            if orchestrator_output.get("prescription_required") and not orchestrator_output.get("safety_validated"):
                extracted_meds = orchestrator_output.get("extracted_meds", [])
                med_names = ", ".join([m.get("name", "") for m in extracted_meds if m.get("name")])

                temp_data = conversation_state.get("temp_data", {})
                temp_data["awaiting_prescription"] = True
                temp_data["pending_user_message"] = user_text
                temp_data["pending_extracted_meds"] = extracted_meds
                await update_conversation_state(
                    user_number, conversation_state["state"], temp_data
                )

                reply_text = (
                    f"⚠️ *PRESCRIPTION REQUIRED* ⚠️\n\n"
                    f"*{med_names}* requires a valid doctor's prescription.\n\n"
                    f"Please visit our store with your prescription or send us a photo of it."
                )
                send_whatsapp_text(user_number, reply_text)
                return {"status": "prescription_required"}
            
            # Check inventory
            if not orchestrator_output.get("inventory_checked"):
                inventory_results = orchestrator_output.get("inventory_results", [{}])
                out_of_stock = inventory_results[0].get("out_of_stock", []) if inventory_results else []
                
                if out_of_stock:
                    med_list = "\n• ".join([
                        f"{m['name']} (Requested: {m['wanted']}, Available: {m['available']})"
                        for m in out_of_stock
                    ])
                    reply_text = (
                        f"😔 *Out of Stock*\n\n"
                        f"Sorry, these items are unavailable:\n\n• {med_list}\n\n"
                        f"Would you like to be notified when back in stock?"
                    )
                    send_whatsapp_text(user_number, reply_text)
                    return {"status": "out_of_stock"}
            
            # If order is successful, store details and ask for confirmation
            if orchestrator_output.get("inventory_checked") and orchestrator_output.get("safety_validated"):
                inventory_results = orchestrator_output.get("inventory_results", [])
                extracted_meds = orchestrator_output.get("extracted_meds", [])
                
                if inventory_results and extracted_meds:
                    # Store order details with orchestrator output
                    temp_data = conversation_state.get("temp_data", {})
                    temp_data["orchestrator_output"] = {
                        "inventory_results": inventory_results,
                        "extracted_meds": extracted_meds,
                        "safety_validated": orchestrator_output.get("safety_validated"),
                        "fulfillment_status": orchestrator_output.get("fulfillment_status"),
                    }
                    
                    # Get first item details
                    in_stock = inventory_results[0].get("in_stock", []) if inventory_results else []
                    if in_stock:
                        first_item = in_stock[0]
                        temp_data["medicine_name"] = first_item.get("name", "Medicine")
                        temp_data["quantity"] = first_item.get("qty", 1)
                        temp_data["price"] = first_item.get("price", 0)
                        temp_data["product_id"] = first_item.get("product_id", "")
                    
                    await update_conversation_state(
                        user_number, ConversationState.ORDER_CONFIRMED, temp_data
                    )
                    
                    # Send confirmation with button
                    final_response = orchestrator_output.get("final_response", "Order ready for confirmation")
                    buttons = [
                        {"id": "confirm_yes", "title": "✅ Confirm Order"},
                        {"id": "confirm_no", "title": "❌ Cancel"},
                    ]
                    send_whatsapp_buttons(user_number, final_response, buttons)
                    return {"status": "order_confirmation"}
            
            # Send general response
            final_response = orchestrator_output.get("final_response", reply_text)
            send_whatsapp_text(user_number, final_response)
            
        except Exception as e:
            logger.error(f"Orchestrator Error: {e}")
            send_whatsapp_text(user_number, reply_text)

    else:
        # For standard intents (ASK_NAME, etc.)
        send_whatsapp_text(user_number, reply_text)

    # Update user profile if needed
    if "user_info" in ai_response:
        await update_user_profile(user_number, ai_response["user_info"])

    return {"status": "processed"}


@router.post(
    "/whatsapp/test-chat",
    summary="Hot-test the WhatsApp AI logic without real WhatsApp",
)
async def test_whatsapp_chat(
    request: Request,
):
    """
    Simulate a message and see exactly how the AI agents would respond.
    Returns the AI's intent, the drafted reply, and the orchestrator's state.
    """
    # Accept both JSON and form payloads, and surface malformed JSON clearly.
    user_text = ""
    user_number = "919876543210"
    user_name = "Test User"

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8") if raw_body else "{}")
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Invalid JSON payload. Escape backslashes as \\\\.",
                    "example": {
                        "user_text": "Need medicine for cough and cold",
                        "user_number": "919876543210",
                        "user_name": "Test User",
                    },
                    "json_error": str(exc),
                },
            )

        user_text = str(payload.get("user_text", "")).strip()
        user_number = str(payload.get("user_number", user_number)).strip() or user_number
        user_name = str(payload.get("user_name", user_name)).strip() or user_name
    else:
        try:
            form = await request.form()
            user_text = str(form.get("user_text", "")).strip()
            user_number = str(form.get("user_number", user_number)).strip() or user_number
            user_name = str(form.get("user_name", user_name)).strip() or user_name
        except Exception:
            pass

    if not user_text:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "user_text is required.",
                "expected_json": {
                    "user_text": "Need paracetamol 650",
                    "user_number": "919876543210",
                    "user_name": "Test User",
                },
            },
        )

    # 1. Get Context
    user_profile = await get_user_profile(user_number)
    recent_orders = await get_recent_orders(user_number)
    conversation_state = await get_conversation_state(user_number)
    user_addresses = await get_user_addresses(user_number)

    # 2. Process via AI Classifier
    ai_response = process_ai_interaction(
        user_text, user_profile, recent_orders, user_addresses, conversation_state
    )
    intent = ai_response.get("intent", "GENERAL")
    reply_text = ai_response.get("reply_text", "")

    # 3. If intent is GENERAL or ORDER_MEDICINE, run the full Orchestrator
    orchestrator_output = None
    if intent in ["GENERAL", "ORDER_MEDICINE"]:
        from langchain_core.messages import HumanMessage
        from app.agents.state import AgentState
        from app.agents.agent_6_orchestrator import orchestrator

        initial_state = AgentState(
            messages=[HumanMessage(content=user_text)],
            extracted_meds=[],
            safety_validated=True,
            prescription_required=False,
            prescription_uploaded=conversation_state.get("temp_data", {}).get(
                "prescription_uploaded", False
            ),
            validation_reasons=[],
            inventory_checked=True,
            inventory_results=[],
            fulfillment_status=None,
            final_response=None,
            channel="WhatsApp",
            steps=[],
            current_agent=None,
            channel_metadata={
                "user_id": user_number,
                "patient_name": user_name,
            },
        )
        orchestrator_output = await orchestrator.ainvoke(initial_state)
        # Use orchestrator response if available
        if orchestrator_output.get("final_response"):
            reply_text = orchestrator_output["final_response"]

    return {
        "status": "ok",
        "simulated_for": user_number,
        "input_text": user_text,
        "intent_detected": intent,
        "ai_classifier_reply": ai_response.get("reply_text"),
        "final_reply": reply_text,
        "orchestrator_state": (
            {
                "safety_validated": (
                    orchestrator_output.get("safety_validated")
                    if orchestrator_output
                    else None
                ),
                "inventory_checked": (
                    orchestrator_output.get("inventory_checked")
                    if orchestrator_output
                    else None
                ),
                "fulfillment_status": (
                    orchestrator_output.get("fulfillment_status")
                    if orchestrator_output
                    else None
                ),
            }
            if orchestrator_output
            else None
        ),
    }


@router.get("/whatsapp-status")
def root():
    return {
        "status": "Pharmastic Bot (WhatsApp Native) Running with Address Management"
    }


# =============================
# NEW API ENDPOINTS
# =============================


class ChannelEnum(str, Enum):
    MANUAL = "Manual"
    WHATSAPP = "WhatsApp"
    TELEGRAM = "Telegram"


class OrderRequest(BaseModel):
    patient_id: str
    patient_name: str
    age: int
    gender: str
    contact_number: str
    address: str
    medicine_name: str
    quantity: float
    unit_price: float
    channel: ChannelEnum


@router.post("/api/v1/place-order")
async def api_place_order(order: OrderRequest):
    """
    API endpoint for placing an order from various channels (Manual, WhatsApp, Telegram, etc.)
    """
    if unified_orders_collection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")

    # Generate a unique Order ID
    order_id = f"ORD{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{order.patient_id[-4:]}"

    # Prepare order document
    order_doc = {
        "order_id": order_id,
        "patient_name": order.patient_name,
        "patient_id": order.patient_id,
        "age": order.age,
        "gender": order.gender,
        "contact_number": order.contact_number,
        "address": order.address,
        "medicine_name": order.medicine_name,
        "quantity": float(order.quantity),
        "unit_price": float(order.unit_price),
        "total_amount": float(order.quantity * order.unit_price),
        "order_channel": order.channel.value,
        "order_status": "Pending",
        "order_date": datetime.utcnow().strftime("%Y-%m-%d"),
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    try:
        # Save to the new unified_orders collection
        await unified_orders_collection.insert_one(order_doc)

        # Also sync with the legacy consumer_orders_collection if needed
        if consumer_orders_collection is not None:
            legacy_doc = {
                "Patient Name": order.patient_name,
                "Patient ID": order.patient_id,
                "Age": order.age,
                "Gender": order.gender,
                "Contact Number": order.contact_number,
                "Address": order.address,
                "Order ID": order_id,
                "Order Date": order_doc["order_date"],
                "Order Channel": order.channel.value,
                "Order Status": "Pending",
                "Medicine Name": order.medicine_name,
                "Quantity Ordered": float(order.quantity),
                "Unit Price": float(order.unit_price),
                "Total Amount": float(order.quantity * order.unit_price),
            }
            await consumer_orders_collection.insert_one(legacy_doc)

        logger.info(
            f"✅ Order {order_id} placed successfully via {order.channel.value}"
        )
        return {
            "status": "success",
            "message": "Order placed successfully",
            "order_id": order_id,
        }
    except Exception as e:
        logger.error(f"❌ Error placing order: {e}")
        raise HTTPException(status_code=500, detail=str(e))


from twilio.twiml.messaging_response import MessagingResponse


@router.post("/twilio/webhook")
async def twilio_webhook(
    Body: str = Form(""),
    From: str = Form(""),
    To: str = Form(""),
    ProfileName: str = Form(""),
    NumMedia: int = Form(0),
    MediaUrl0: str = Form(""),
    MediaContentType0: str = Form(""),
):
    """
    Twilio Sandbox Webhook for WhatsApp
    Processes incoming messages from Twilio, runs them through the AI Orchestrator,
    and returns TwiML response.
    
    Now supports prescription image uploads via Twilio media handling.
    """
    user_number = From.replace("whatsapp:", "") if "whatsapp:" in From else From
    user_text = Body.strip()

    logger.info(f"📩 Twilio Message from {user_number} ({ProfileName}): {user_text}")
    conversation_state = await get_conversation_state(user_number)
    temp_data = conversation_state.get("temp_data", {})

    # Handle Image/Media Upload (Prescription)
    if NumMedia > 0 and MediaUrl0:
        logger.info(f"📸 Image received from {user_number}: {MediaUrl0}")

        if not temp_data.get("awaiting_prescription", False):
            resp = MessagingResponse()
            resp.message(
                "Image received. Please share medicine details first. I will ask for prescription only when required."
            )
            return Response(content=str(resp), media_type="application/xml")

        if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
            logger.error("Twilio media auth credentials missing in environment.")
            resp = MessagingResponse()
            resp.message(
                "Prescription image received, but media access is not configured on server."
            )
            return Response(content=str(resp), media_type="application/xml")

        try:
            media_resp = requests.get(
                MediaUrl0,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
                timeout=20,
            )
            if media_resp.status_code != 200:
                logger.error(
                    f"Twilio media download failed ({media_resp.status_code}): {media_resp.text}"
                )
                resp = MessagingResponse()
                resp.message(
                    "I could not download your prescription image. Please resend a clearer photo."
                )
                return Response(content=str(resp), media_type="application/xml")

            extracted_text = _extract_text_with_ocr_space(
                media_resp.content, filename=f"twilio_prescription_{user_number}.jpg"
            )
            if not extracted_text:
                resp = MessagingResponse()
                resp.message(
                    "I could not read text from this image. Please send a clearer prescription photo."
                )
                return Response(content=str(resp), media_type="application/xml")

            temp_data["prescription_uploaded"] = True
            temp_data["awaiting_prescription"] = False
            temp_data["prescription_ocr_text"] = extracted_text[:4000]
            await update_conversation_state(
                user_number, conversation_state["state"], temp_data
            )

            preview = extracted_text[:250].replace("\n", " ").strip()
            resp = MessagingResponse()
            resp.message(
                f"✅ Prescription received and text extracted.\nOCR Preview: {preview}\nPlease confirm your medicine request again."
            )
            return Response(content=str(resp), media_type="application/xml")
        except Exception as exc:
            logger.error(f"Twilio image processing error: {exc}")
            resp = MessagingResponse()
            resp.message(
                "I could not process your prescription image right now. Please try again."
            )
            return Response(content=str(resp), media_type="application/xml")

    from langchain_core.messages import HumanMessage
    from app.agents.state import AgentState
    from app.agents.agent_6_orchestrator import orchestrator

    initial_state = AgentState(
        messages=[HumanMessage(content=user_text)],
        extracted_meds=[],
        safety_validated=True,
        validation_reasons=[],
        inventory_checked=True,
        inventory_results=[],
        fulfillment_status=None,
        final_response=None,
        channel="WhatsApp",
        channel_metadata={
            "user_id": user_number,
            "patient_name": ProfileName,
            "platform": "Twilio WhatsApp Sandbox",
            "prescription_ocr_text": temp_data.get("prescription_ocr_text", ""),
        },
    )

    try:
        # Run orchestrator
        orchestrator_output = await orchestrator.ainvoke(initial_state)

        if orchestrator_output.get("prescription_required") and not orchestrator_output.get(
            "safety_validated"
        ):
            extracted_meds = orchestrator_output.get("extracted_meds", [])
            med_names = ", ".join(
                [m.get("name", "") for m in extracted_meds if m.get("name")]
            )
            temp_data["awaiting_prescription"] = True
            temp_data["pending_user_message"] = user_text
            temp_data["pending_extracted_meds"] = extracted_meds
            await update_conversation_state(
                user_number, conversation_state["state"], temp_data
            )

            reply_text = (
                f"⚠️ Prescription required for {med_names or 'this medicine'}.\n"
                "Please send a clear photo of your prescription."
            )
        else:
            reply_text = orchestrator_output.get(
                "final_response", "I'm having trouble processing your request."
            )

    except Exception as e:
        logger.error(f"❌ Twilio Orchestrator Error: {e}")
        reply_text = (
            "Sorry, our automated system encountered an error fulfilling your request."
        )

    # Return Twilio Response format
    resp = MessagingResponse()
    resp.message(reply_text)
    return Response(content=str(resp), media_type="application/xml")
