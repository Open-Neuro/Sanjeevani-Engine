import os
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Request, HTTPException, Response
from pydantic import BaseModel, Field
import requests
from dotenv import load_dotenv
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
import httpx
from twilio.twiml.messaging_response import MessagingResponse

from app.database.models import ConversationState, OrderChannel, OrderRequest
from app.agents.state import AgentState
from app.agents.agent_6_orchestrator import orchestrator

# Load environment variables
load_dotenv()

# =============================
# CONFIGURATION
# =============================
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_SMS_NUMBER = os.getenv(
    "TWILIO_SMS_NUMBER"
)  # Use a specific env var for SMS number

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")

# Logging Configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

router = APIRouter(tags=["SMS Bot"])

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
You are a Pharmacy Assistant AI operating on SMS.
Your goal is to onboard new users step-by-step and then help them order medicines with address delivery.

You operate as part of a backend system using:
- FastAPI
- Twilio SMS API
- MongoDB (user, order & address storage)

════════════════════════════════════
STRICT ONBOARDING FLOW
════════════════════════════════════

For a NEW USER (User Profile is "Unknown"):
You must collect **Name**, **Language**, **Gender**, and **Age** in this EXACT order. Do not ask for everything at once.

**Step 1: Ask Name**
If you have NO info:
Reply Text: "👋 Welcome to Pharmastic! I see you are a new customer. Let's set up your profile.\n\nWhat is your Name?"
Intent: "ASK_NAME"

**Step 2: Ask Language**
If you have Name but NO Language:
Reply Text: "Nice to meet you, {name}!\n\nWhich language do you prefer? Reply with 'English' or 'Hindi'."
Intent: "ASK_LANGUAGE"

**Step 3: Ask Gender**
If you have Name and Language but NO Gender:
Reply Text: "Got it.\n\nWhat is your Gender? Reply with 'Male' or 'Female'."
Intent: "ASK_GENDER"

**Step 4: Ask Age**
If you have Name, Language, and Gender but NO Age:
Reply Text: "Almost done.\n\nPlease reply with your Age (e.g., 25):"
Intent: "ASK_AGE"

**Step 5: Profile Complete**
If you just received the Age (and now have all 4 fields):
Reply Text: "✅ Profile Complete!\n\nWelcome {name}.\n\nWhich medicine do you want to order today?\n(Reply with the medicine name)"
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
   - Reply Text: "Please select delivery address: Reply with '1' for Home, '2' for Office, or 'New' to add a new address."
   - Intent: "SELECT_ADDRESS"
   (System will show buttons with saved addresses)

   If NO saved addresses OR user selects "Add New":
   - Step 1: Ask for Address Line 1
     Reply Text: "Please reply with your Address Line 1 (Street, building, etc.):"
     Intent: "ASK_ADDRESS_LINE1"
   
   - Step 2: Ask for Address Line 2 (optional)
     Reply Text: "Reply with Address Line 2 (Area/locality) or type 'Skip':"
     Intent: "ASK_ADDRESS_LINE2"
   
   - Step 3: Ask for City
     Reply Text: "Reply with your City:"
     Intent: "ASK_CITY"
   
   - Step 4: Ask for State
     Reply Text: "Reply with your State:"
     Intent: "ASK_STATE"
   
   - Step 5: Ask for Pincode
     Reply Text: "Reply with your Pincode (6 digits):"
     Intent: "ASK_PINCODE"
   
   - Step 6: Ask for Landmark (optional)
     Reply Text: "Reply with a Landmark for easy delivery or type 'Skip':"
     Intent: "ASK_LANDMARK"
   
   - Step 7: Ask to Save Address
     Reply Text: "Would you like to save this address for future orders? Reply 'Yes' to save, or 'No' to use once."
     Intent: "SAVE_ADDRESS"

4. **Address Confirmation**:
   After address collection:
   - Show full address summary
   - Intent: "ADDRESS_CONFIRMED"

5. **Final Order**:
   Reply Text: "✅ Order Placed Successfully!\n\nYour order will be delivered to:\n{address}\n\nWe will notify you when it ships. Order ID: #{order_id}"
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


async def get_user_profile(phone_number: str) -> Optional[Dict]:
    if users_collection is None:
        return None
    return await users_collection.find_one({"user_id": phone_number})


async def update_user_profile(phone_number: str, user_data: Dict):
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": phone_number})

    # Clean None values
    update_data = {k: v for k, v in user_data.items() if v is not None}
    update_data["user_id"] = phone_number

    if not existing:
        update_data["created_at"] = datetime.utcnow()
        await users_collection.insert_one(update_data)
    else:
        await users_collection.update_one(
            {"user_id": phone_number}, {"$set": update_data}
        )


async def get_conversation_state(phone_number: str) -> Dict:
    """Get or create conversation state for user"""
    if conversations_collection is None:
        return {"state": ConversationState.GENERAL, "temp_data": {}}

    state = await conversations_collection.find_one({"user_id": phone_number})
    if not state:
        state = {
            "user_id": phone_number,
            "state": ConversationState.GENERAL,
            "temp_data": {},
            "updated_at": datetime.utcnow(),
        }
        await conversations_collection.insert_one(state)
    return state


async def update_conversation_state(
    phone_number: str, new_state: str, temp_data: Dict = None
):
    """Update conversation state for user"""
    if conversations_collection is None:
        return

    update = {"state": new_state, "updated_at": datetime.utcnow()}
    if temp_data is not None:
        update["temp_data"] = temp_data

    await conversations_collection.update_one(
        {"user_id": phone_number}, {"$set": update}, upsert=True
    )


async def save_user_address(phone_number: str, address_data: Dict) -> str:
    """Save user address and return address ID"""
    if addresses_collection is None:
        return None

    address = {
        "user_id": phone_number,
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
            {"user_id": phone_number, "is_default": True},
            {"$set": {"is_default": False}},
        )

    result = await addresses_collection.insert_one(address)
    return str(result.inserted_id)


async def get_user_addresses(phone_number: str) -> List[Dict]:
    """Get all addresses for user"""
    if addresses_collection is None:
        return []

    cursor = addresses_collection.find({"user_id": phone_number}).sort("is_default", -1)
    addresses = await cursor.to_list(length=10)
    return addresses


async def get_default_address(phone_number: str) -> Optional[Dict]:
    """Get user's default address"""
    if addresses_collection is None:
        return None

    return await addresses_collection.find_one(
        {"user_id": phone_number, "is_default": True}
    )


async def create_order(phone_number: str, order_info: Dict):
    """Create a new order"""
    if orders_collection is None:
        return

    # Generate order ID
    order_id = f"ORD{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{phone_number[-4:]}"

    order_data = {
        "order_id": order_id,
        "user_id": phone_number,
        "medicine_name": order_info.get("medicine_name"),
        "quantity": order_info.get("quantity"),
        "price": order_info.get("price", 0),
        "total_amount": int(
            float(order_info.get("quantity", 0)) if order_info.get("quantity") else 0
        )
        * int(float(order_info.get("price", 0)) if order_info.get("price") else 0),
        "delivery_address": order_info.get("delivery_address"),
        "address_id": order_info.get("address_id"),
        "order_status": "confirmed",
        "payment_status": "pending",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    result = await orders_collection.insert_one(order_data)

    # Also insert into SanjeevaniRxAI's pharmacy_management.consumer_orders
    if consumer_orders_collection is not None:
        user_profile = await get_user_profile(phone_number)
        patient_name = (
            user_profile.get("name", "Unknown Patient")
            if user_profile
            else "Unknown Patient"
        )
        age = user_profile.get("age", 0) if user_profile else 0
        gender = user_profile.get("gender", "Unknown") if user_profile else "Unknown"

        # Build address string directly from dictionary
        addr_dict = order_info.get("delivery_address", {})
        addr_str = f"{addr_dict.get('address_line1', '')} {addr_dict.get('address_line2', '')} {addr_dict.get('city', '')} {addr_dict.get('state', '')} {addr_dict.get('pincode', '')}".strip()

        consumer_order_data = {
            "Patient Name": patient_name,
            "Patient ID": phone_number,
            "Age": age,
            "Gender": gender,
            "Contact Number": phone_number,
            "Address": addr_str,
            "Order ID": order_id,
            "Order Date": datetime.utcnow().strftime("%Y-%m-%d"),
            "Order Channel": "SMS",
            "Order Status": "Pending",
            "Medicine Name": order_info.get("medicine_name"),
            "Quantity Ordered": float(order_info.get("quantity", 1)),
            "Unit Price": float(order_info.get("price", 0)),
            "Total Amount": float(
                int(
                    float(order_info.get("quantity", 0))
                    if order_info.get("quantity")
                    else 0
                )
                * int(
                    float(order_info.get("price", 0)) if order_info.get("price") else 0
                )
            ),
        }
        await consumer_orders_collection.insert_one(consumer_order_data)

    # Also sync with the new unified_orders collection
    if unified_orders_collection is not None:
        user_profile = await get_user_profile(phone_number)
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
            "patient_id": phone_number,
            "age": age,
            "gender": gender,
            "contact_number": phone_number,
            "address": addr_str,
            "medicine_name": order_info.get("medicine_name"),
            "quantity": float(order_info.get("quantity", 1)),
            "unit_price": float(order_info.get("price", 0)),
            "total_amount": float(
                int(
                    float(order_info.get("quantity", 0))
                    if order_info.get("quantity")
                    else 0
                )
                * int(
                    float(order_info.get("price", 0)) if order_info.get("price") else 0
                )
            ),
            "order_channel": "SMS",
            "order_status": "Pending",
            "order_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await unified_orders_collection.insert_one(unified_doc)

    return order_id


async def get_recent_orders(phone_number: str) -> List[Dict]:
    """Get recent orders for user"""
    if orders_collection is None:
        return []
    cursor = (
        orders_collection.find({"user_id": phone_number})
        .sort("created_at", -1)
        .limit(3)
    )
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
        addr_str = f"{address_details.get('address_line1', '')} {address_details.get('address_line2', '')} {addr_dict.get('city', '')} {addr_dict.get('state', '')} {addr_dict.get('pincode', '')}".strip()
        await consumer_orders_collection.update_one(
            {"Order ID": order_id},
            {"$set": {"Address": addr_str, "Order Status": "Address Confirmed"}},
        )

    # Update in pharmacy_management.unified_orders if present
    if unified_orders_collection is not None:
        addr_str = f"{address_details.get('address_line1', '')} {address_details.get('address_line2', '')} {addr_dict.get('city', '')} {addr_dict.get('state', '')} {addr_dict.get('pincode', '')}".strip()
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
# TWILIO SMS HELPERS
# =============================
def send_sms_message(to: str, message: str):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_SMS_NUMBER:
        logger.error("Twilio SMS credentials not configured.")
        return

    from twilio.rest import Client

    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

    try:
        client.messages.create(to=to, from_=TWILIO_SMS_NUMBER, body=message)
        logger.info(f"SMS sent to {to}: {message}")
    except Exception as e:
        logger.error(f"Error sending SMS to {to}: {e}")


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
    phone_number: str, user_text: str, conversation_state: Dict
):
    """Handle step-by-step address collection for SMS"""
    temp_data = conversation_state.get("temp_data", {})
    current_step = temp_data.get("step", "line1")

    if "address_info" not in temp_data:
        temp_data["address_info"] = {}

    # Process based on current step
    if current_step == "select":
        if user_text.lower() == "new":
            temp_data["step"] = "line1"
            await update_conversation_state(
                phone_number, ConversationState.ORDERING_ADDRESS, temp_data
            )
            send_sms_message(
                phone_number,
                "Please reply with your Address Line 1 (Street, building, etc.):",
            )
            return
        elif user_text == "1":  # Assuming '1' for Home
            addresses = temp_data.get("addresses", [])
            if addresses:
                selected_addr = addresses[0]
                temp_data["address_info"] = selected_addr
                temp_data["step"] = "confirm"
                await update_conversation_state(
                    phone_number,
                    ConversationState.ORDERING_ADDRESS_CONFIRM,
                    temp_data,
                )
                address_str = format_address_string(selected_addr)
                send_sms_message(
                    phone_number,
                    f"Deliver to this address? {address_str}. Reply 'Yes' to confirm, or 'No' to use a different address.",
                )
                return
            else:
                send_sms_message(
                    phone_number,
                    "No saved addresses found. Please reply with your Address Line 1.",
                )
                return
        elif user_text == "2":  # Assuming '2' for Office
            addresses = temp_data.get("addresses", [])
            if len(addresses) > 1:
                selected_addr = addresses[1]
                temp_data["address_info"] = selected_addr
                temp_data["step"] = "confirm"
                await update_conversation_state(
                    phone_number,
                    ConversationState.ORDERING_ADDRESS_CONFIRM,
                    temp_data,
                )
                address_str = format_address_string(selected_addr)
                send_sms_message(
                    phone_number,
                    f"Deliver to this address? {address_str}. Reply 'Yes' to confirm, or 'No' to use a different address.",
                )
                return
            else:
                send_sms_message(
                    phone_number,
                    "No office address found. Please reply with your Address Line 1.",
                )
                return
        else:
            send_sms_message(
                phone_number,
                "I didn't understand your selection. Please reply with '1' for Home, '2' for Office, or 'New' to add a new address.",
            )
            return

    # Handle confirmation from address selection
    if current_step == "confirm":
        if user_text.lower() == "yes":
            # Complete order with selected address
            await complete_order_with_address(phone_number, temp_data)
        elif user_text.lower() == "no":
            # Show addresses again
            await show_address_selection(phone_number, temp_data)
        else:
            send_sms_message(
                phone_number,
                "I didn't understand. Reply 'Yes' to confirm, or 'No' to use a different address.",
            )
        return

    # Regular address collection steps
    reply = ""

    if current_step == "line1":
        temp_data["address_info"]["address_line1"] = user_text
        temp_data["step"] = "line2"
        reply = "Reply with Address Line 2 (Area/locality) or type 'Skip':"

    elif current_step == "line2":
        if user_text.lower() != "skip":
            temp_data["address_info"]["address_line2"] = user_text
        temp_data["step"] = "city"
        reply = "Reply with your City:"

    elif current_step == "city":
        temp_data["address_info"]["city"] = user_text
        temp_data["step"] = "state"
        reply = "Reply with your State:"

    elif current_step == "state":
        temp_data["address_info"]["state"] = user_text
        temp_data["step"] = "pincode"
        reply = "Reply with your Pincode (6 digits):"

    elif current_step == "pincode":
        if re.match(r"^\d{6}$", user_text):
            temp_data["address_info"]["pincode"] = user_text
            temp_data["step"] = "landmark"
            reply = "Reply with a Landmark for easy delivery or type 'Skip':"
        else:
            reply = "❌ Invalid pincode. Please reply with 6 digits:"
            send_sms_message(phone_number, reply)
            await update_conversation_state(
                phone_number, ConversationState.ORDERING_ADDRESS, temp_data
            )
            return

    elif current_step == "landmark":
        if user_text.lower() != "skip":
            temp_data["address_info"]["landmark"] = user_text

        # Ask if they want to save address
        address_str = format_address_string(temp_data["address_info"])
        await update_conversation_state(
            phone_number, ConversationState.ORDERING_ADDRESS_CONFIRM, temp_data
        )
        send_sms_message(
            phone_number,
            f"Confirm your address:\n\n{address_str}\n\nWould you like to save this address for future? Reply 'Yes' to save, or 'No' to use once.",
        )
        return

    # Update state and send next question
    await update_conversation_state(
        phone_number, ConversationState.ORDERING_ADDRESS, temp_data
    )
    send_sms_message(phone_number, reply)


async def show_address_selection(phone_number: str, temp_data: Dict):
    """Show saved addresses for selection for SMS"""
    addresses = await get_user_addresses(phone_number)
    temp_data["addresses"] = addresses  # Store addresses in temp_data for later use

    if addresses:
        message = "Please select delivery address. "
        for i, addr in enumerate(addresses[:3]):  # Max 3 options for simplicity
            addr_summary = (
                f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:20]}..."
            )
            message += f"Reply '{i+1}' for {addr_summary}. "
        message += "Or reply 'New' to add a new address."

        temp_data["step"] = "select"
        await update_conversation_state(
            phone_number, ConversationState.ORDERING_ADDRESS, temp_data
        )
        send_sms_message(phone_number, message)
    else:
        # No addresses, start fresh collection
        temp_data["step"] = "line1"
        await update_conversation_state(
            phone_number, ConversationState.ORDERING_ADDRESS, temp_data
        )
        send_sms_message(
            phone_number,
            "No saved addresses found. Please reply with your Address Line 1 (Street, building, etc.):",
        )


async def complete_order_with_address(phone_number: str, temp_data: Dict):
    """Complete the order with the collected address for SMS"""
    # Handle save address choice
    save_address = temp_data.get("save_address", False)
    if save_address:
        address_id = await save_user_address(phone_number, temp_data["address_info"])
    else:
        address_id = None

    # Create order
    medicine_name = temp_data.get("medicine_name", "Medicine")
    quantity = temp_data.get("quantity", 1)
    price = temp_data.get("price", 250)

    # Ensure quantity and price are integers and not empty strings
    try:
        quantity_val = int(float(quantity)) if quantity and str(quantity).strip() else 1
    except (ValueError, TypeError):
        quantity_val = 1

    try:
        price_val = int(float(price)) if price and str(price).strip() else 250
    except (ValueError, TypeError):
        price_val = 250

    order_data = {
        "medicine_name": medicine_name,
        "quantity": quantity_val,
        "price": price_val,
        "total_amount": quantity_val * price_val,
        "delivery_address": temp_data["address_info"],
        "address_id": address_id,
        "order_status": "confirmed",
    }

    order_id = await create_order(phone_number, order_data)

    # Send confirmation
    address_str = format_address_string(temp_data["address_info"])
    reply = f"✅ Order Placed Successfully!\n\nOrder ID: #{order_id}\n\nMedicine: {medicine_name}\nQuantity: {quantity_val}\nTotal: ₹{quantity_val * price_val}\n\nDelivering to:\n{address_str}\n\nWe will notify you when it ships. 📦"
    send_sms_message(phone_number, reply)

    # Reset conversation state
    await update_conversation_state(phone_number, ConversationState.GENERAL, {})


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

    return ", ".join(parts)


# =============================
# TWILIO WEBHOOKS
# =============================
@router.post("/sms-webhook")
async def handle_sms_webhook(request: Request):
    try:
        form_data = await request.form()
        sender_number = form_data.get("From")
        user_text = form_data.get("Body")

        if not sender_number:
            logger.error("No sender number found in Twilio SMS webhook.")
            return Response(
                content=str(MessagingResponse().message("An error occurred.")),
                media_type="application/xml",
            )

        logger.info(f"💬 SMS from {sender_number}: {user_text}")

        # Get User Context
        user_profile = await get_user_profile(sender_number)
        recent_orders = await get_recent_orders(sender_number)
        conversation_state = await get_conversation_state(sender_number)
        user_addresses = await get_user_addresses(sender_number)

        # Handle Address Selection Flow (State-based)
        if conversation_state["state"] == ConversationState.ORDERING_ADDRESS:
            await handle_address_collection(
                sender_number, user_text, conversation_state
            )
            return Response(
                content=str(MessagingResponse()), media_type="application/xml"
            )

        elif conversation_state["state"] == ConversationState.ORDERING_ADDRESS_CONFIRM:
            temp_data = conversation_state.get("temp_data", {})

            if user_text.lower() == "yes":
                temp_data["save_address"] = True
                await complete_order_with_address(sender_number, temp_data)
            elif user_text.lower() == "no":
                temp_data["save_address"] = False
                await complete_order_with_address(sender_number, temp_data)
            else:
                send_sms_message(
                    sender_number,
                    "I didn't understand. Reply 'Yes' to confirm, or 'No' to use once.",
                )
            return Response(
                content=str(MessagingResponse()), media_type="application/xml"
            )

        # Regular AI Processing via Orchestrator
        initial_state = AgentState(
            messages=[],  # Orchestrator expects messages to be added internally
            extracted_meds=[],
            safety_validated=True,  # Assume true for now, agents will validate
            validation_reasons=[],
            inventory_checked=True,  # Assume true for now, agents will validate
            inventory_results=[],
            fulfillment_status=None,
            final_response=None,
            channel="SMS",
            channel_metadata={"sender_number": sender_number},
        )

        # The orchestrator expects the user message to be part of the messages list
        # We'll add it as a HumanMessage for the orchestrator to process
        from langchain_core.messages import HumanMessage

        initial_state["messages"].append(HumanMessage(content=user_text))

        orchestrator_output = await orchestrator.ainvoke(initial_state)
        final_response = orchestrator_output.get(
            "final_response",
            "Sorry, I'm having trouble processing your request right now.",
        )

        send_sms_message(sender_number, final_response)

    except Exception as e:
        logger.error(f"Error in SMS webhook: {e}", exc_info=True)
        send_sms_message(
            sender_number,
            "I'm sorry, an unexpected error occurred. Please try again later.",
        )

    return Response(content=str(MessagingResponse()), media_type="application/xml")
