import os
import json
import logging
import re
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
import requests
from dotenv import load_dotenv
from groq import Groq
from motor.motor_asyncio import AsyncIOMotorClient
import httpx
from telegram import Update, Bot
from telegram.ext import Dispatcher, MessageHandler, Filters, CallbackContext

from app.database.models import ConversationState, OrderChannel, OrderRequest
from app.agents.state import AgentState
from app.agents.agent_6_orchestrator import orchestrator

# Load environment variables
load_dotenv()

# =============================
# CONFIGURATION
# =============================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")

# Logging Configuration
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

router = APIRouter(tags=["Telegram Bot"])

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

# Telegram Bot Setup
if TELEGRAM_BOT_TOKEN:
    bot = Bot(TELEGRAM_BOT_TOKEN)
    dispatcher = Dispatcher(bot, None, workers=0)
    logger.info("✅ Telegram Bot Configured")
else:
    logger.error("⚠️ TELEGRAM_BOT_TOKEN is missing!")
    bot = None
    dispatcher = None


# =============================
# UPDATED SYSTEM INSTRUCTIONS
# =============================
SYSTEM_INSTRUCTION = """
You are a Pharmacy Assistant AI operating on Telegram.
Your goal is to onboard new users step-by-step and then help them order medicines with address delivery.

You operate as part of a backend system using:
- FastAPI
- Telegram Bot API
- MongoDB (user, order & address storage)

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


async def get_user_profile(chat_id: str) -> Optional[Dict]:
    if users_collection is None:
        return None
    return await users_collection.find_one({"user_id": chat_id})


async def update_user_profile(chat_id: str, user_data: Dict):
    if users_collection is None:
        return
    existing = await users_collection.find_one({"user_id": chat_id})

    # Clean None values
    update_data = {k: v for k, v in user_data.items() if v is not None}
    update_data["user_id"] = chat_id

    if not existing:
        update_data["created_at"] = datetime.utcnow()
        await users_collection.insert_one(update_data)
    else:
        await users_collection.update_one({"user_id": chat_id}, {"$set": update_data})


async def get_conversation_state(chat_id: str) -> Dict:
    """Get or create conversation state for user"""
    if conversations_collection is None:
        return {"state": ConversationState.GENERAL, "temp_data": {}}

    state = await conversations_collection.find_one({"user_id": chat_id})
    if not state:
        state = {
            "user_id": chat_id,
            "state": ConversationState.GENERAL,
            "temp_data": {},
            "updated_at": datetime.utcnow(),
        }
        await conversations_collection.insert_one(state)
    return state


async def update_conversation_state(
    chat_id: str, new_state: str, temp_data: Dict = None
):
    """Update conversation state for user"""
    if conversations_collection is None:
        return

    update = {"state": new_state, "updated_at": datetime.utcnow()}
    if temp_data is not None:
        update["temp_data"] = temp_data

    await conversations_collection.update_one(
        {"user_id": chat_id}, {"$set": update}, upsert=True
    )


async def save_user_address(chat_id: str, address_data: Dict) -> str:
    """Save user address and return address ID"""
    if addresses_collection is None:
        return None

    address = {
        "user_id": chat_id,
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
            {"user_id": chat_id, "is_default": True}, {"$set": {"is_default": False}}
        )

    result = await addresses_collection.insert_one(address)
    return str(result.inserted_id)


async def get_user_addresses(chat_id: str) -> List[Dict]:
    """Get all addresses for user"""
    if addresses_collection is None:
        return []

    cursor = addresses_collection.find({"user_id": chat_id}).sort("is_default", -1)
    addresses = await cursor.to_list(length=10)
    return addresses


async def get_default_address(chat_id: str) -> Optional[Dict]:
    """Get user's default address"""
    if addresses_collection is None:
        return None

    return await addresses_collection.find_one({"user_id": chat_id, "is_default": True})


async def create_order(chat_id: str, order_info: Dict):
    """Create a new order"""
    if orders_collection is None:
        return

    # Generate order ID
    order_id = f"ORD{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{chat_id[-4:]}"

    order_data = {
        "order_id": order_id,
        "user_id": chat_id,
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
        user_profile = await get_user_profile(chat_id)
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
            "Patient ID": chat_id,
            "Age": age,
            "Gender": gender,
            "Contact Number": chat_id,
            "Address": addr_str,
            "Order ID": order_id,
            "Order Date": datetime.utcnow().strftime("%Y-%m-%d"),
            "Order Channel": "Telegram",
            "Order Status": "Confirmed",
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
        user_profile = await get_user_profile(chat_id)
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
            "patient_id": chat_id,
            "age": age,
            "gender": gender,
            "contact_number": chat_id,
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
            "order_channel": "Telegram",
            "order_status": "Confirmed",
            "order_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        await unified_orders_collection.insert_one(unified_doc)

    return order_id


async def get_recent_orders(chat_id: str) -> List[Dict]:
    """Get recent orders for user"""
    if orders_collection is None:
        return []
    cursor = (
        orders_collection.find({"user_id": chat_id}).sort("created_at", -1).limit(3)
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
        addr_str = f"{address_details.get('address_line1', '')} {address_details.get('address_line2', '')} {address_details.get('city', '')} {address_details.get('state', '')} {address_details.get('pincode', '')}".strip()
        await consumer_orders_collection.update_one(
            {"Order ID": order_id},
            {"$set": {"Address": addr_str, "Order Status": "Address Confirmed"}},
        )

    # Update in pharmacy_management.unified_orders if present
    if unified_orders_collection is not None:
        addr_str = f"{address_details.get('address_line1', '')} {address_details.get('address_line2', '')} {address_details.get('city', '')} {address_details.get('state', '')} {addr_dict.get('pincode', '')}".strip()
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
# TELEGRAM API HELPERS
# =============================
async def send_telegram_message(
    chat_id: int, text: str, reply_markup: Optional[Dict] = None
):
    if not bot:
        logger.error("Telegram Bot not configured.")
        return
    try:
        await bot.send_message(
            chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Send Telegram Message Error: {e}")


def process_ai_interaction(
    user_text: str,
    user_profile: Optional[Dict],
    recent_orders: List[Dict],
    user_addresses: List[Dict],
    conversation_state: Dict,
) -> Dict:
    """
    Processes the user message using Groq LLM and returns a structured JSON
    response based on the Pharmacy Assistant system instructions.
    """
    if not groq_client:
        logger.error("❌ Groq AI client is not configured.")
        return {
            "intent": "ERROR",
            "reply_text": "I'm sorry, my AI module is currently offline. Please try again later.",
        }

    # 1. Format User Profile for the AI
    # If profile is empty, we tell the AI it's a "New User" to trigger onboarding
    if not user_profile:
        profile_str = "Unknown (First time user - Start Onboarding)"
    else:
        profile_str = json.dumps(
            {
                "name": user_profile.get("name"),
                "age": user_profile.get("age"),
                "gender": user_profile.get("gender"),
                "language": user_profile.get("language"),
            },
            indent=2,
        )

    # 2. Format Saved Addresses for selection logic
    addresses_str = "No saved addresses"
    if user_addresses:
        addresses_str = json.dumps(
            [
                {
                    "type": a.get("address_type", "Home"),
                    "line1": a.get("address_line1"),
                    "city": a.get("city"),
                    "pincode": a.get("pincode"),
                }
                for a in user_addresses
            ],
            indent=2,
        )

    # 3. Create the Context Block for the LLM
    # We include everything the AI needs to make a decision
    msg_context = f"""
    --- CURRENT SYSTEM CONTEXT ---
    USER PROFILE: {profile_str}
    RECENT ORDERS: {json.dumps(recent_orders)}
    SAVED ADDRESSES: {addresses_str}
    CURRENT CONVERSATION STATE: {conversation_state.get('state', 'GENERAL')}
    PENDING DATA: {json.dumps(conversation_state.get('temp_data', {}))}
    
    USER MESSAGE: "{user_text}"
    ------------------------------
    """

    try:
        # 4. Call Groq API with forced JSON mode
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": msg_context},
            ],
            temperature=0.1,  # Low temperature for strict adherence to flow
            response_format={"type": "json_object"},
        )

        # 5. Parse and validate the response
        raw_content = completion.choices[0].message.content.strip()
        ai_json = json.loads(raw_content)

        logger.info(
            f"🤖 AI Intent: {ai_json.get('intent')} | Response: {ai_json.get('reply_text')[:50]}..."
        )
        return ai_json

    except json.JSONDecodeError:
        logger.error("❌ AI returned invalid JSON format.")
        return {
            "intent": "GENERAL",
            "reply_text": "I encountered a formatting error. Could you please repeat that?",
        }
    except Exception as e:
        logger.error(f"❌ Groq API Error: {str(e)}")
        return {
            "intent": "ERROR",
            "reply_text": "I'm having a bit of trouble thinking right now. Let's try again in a moment.",
        }


# =============================
# ADDRESS COLLECTION HELPER
# =============================
async def handle_address_collection(
    chat_id: str, user_text: str, conversation_state: Dict
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
                chat_id, ConversationState.ORDERING_ADDRESS, temp_data
            )
            await send_telegram_message(
                chat_id,
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
                        chat_id,
                        ConversationState.ORDERING_ADDRESS_CONFIRM,
                        temp_data,
                    )

                    # Show address and ask for confirmation
                    address_str = format_address_string(selected_addr)
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {
                                    "text": "Confirm Address",
                                    "callback_data": "confirm_addr",
                                },
                                {"text": "Use Different", "callback_data": "new_addr"},
                            ]
                        ]
                    }
                    await send_telegram_message(
                        chat_id,
                        f"Deliver to this address?\n\n{address_str}",
                        reply_markup=reply_markup,
                    )
                    return
            except (ValueError, IndexError):
                pass

    # Handle confirmation from address selection
    if current_step == "confirm":
        if user_text == "Confirm Address":
            # Complete order with selected address
            await complete_order_with_address(chat_id, temp_data)
        elif user_text == "Use Different":
            # Show addresses again
            await show_address_selection(chat_id, temp_data)
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
            await send_telegram_message(chat_id, reply)
            await update_conversation_state(
                chat_id, ConversationState.ORDERING_ADDRESS, temp_data
            )
            return

    elif current_step == "landmark":
        if user_text.lower() != "skip":
            temp_data["address_info"]["landmark"] = user_text

        # Ask if they want to save address
        address_str = format_address_string(temp_data["address_info"])
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "Yes, Save", "callback_data": "save_yes"},
                    {"text": "No, Use Once", "callback_data": "save_no"},
                ]
            ]
        }
        await update_conversation_state(
            chat_id, ConversationState.ORDERING_ADDRESS_CONFIRM, temp_data
        )
        await send_telegram_message(
            chat_id,
            f"Confirm your address:\n\n{address_str}\n\nSave this address for future?",
            reply_markup=reply_markup,
        )
        return

    # Update state and send next question
    await update_conversation_state(
        chat_id, ConversationState.ORDERING_ADDRESS, temp_data
    )
    await send_telegram_message(chat_id, reply)


async def show_address_selection(chat_id: str, temp_data: Dict):
    """Show saved addresses for selection"""
    addresses = temp_data.get("addresses", [])
    if addresses:
        buttons = []
        for i, addr in enumerate(addresses[:3]):  # Max 3 buttons
            addr_summary = (
                f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:15]}..."
            )
            buttons.append({"text": addr_summary, "callback_data": f"addr_{i}"})
        buttons.append({"text": "➕ Add New Address", "callback_data": "addr_new"})

        reply_markup = {"inline_keyboard": [buttons]}

        temp_data["step"] = "select"
        await update_conversation_state(
            chat_id, ConversationState.ORDERING_ADDRESS, temp_data
        )
        await send_telegram_message(
            chat_id, "Select delivery address:", reply_markup=reply_markup
        )
    else:
        # No addresses, start fresh collection
        temp_data["step"] = "line1"
        await update_conversation_state(
            chat_id, ConversationState.ORDERING_ADDRESS, temp_data
        )
        await send_telegram_message(
            chat_id, "Please enter your *Address Line 1* (Street, building, etc.):"
        )


async def complete_order_with_address(chat_id: str, temp_data: Dict):
    """Complete the order with the collected address"""
    # Handle save address choice
    save_address = temp_data.get("save_address", False)
    if save_address:
        address_id = await save_user_address(chat_id, temp_data["address_info"])
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

    order_id = await create_order(chat_id, order_data)

    # Send confirmation
    address_str = format_address_string(temp_data["address_info"])
    reply = f"✅ *Order Placed Successfully!*\n\n*Order ID:* #{order_id}\n\n*Medicine:* {medicine_name}\n*Quantity:* {quantity_val}\n*Total:* ₹{quantity_val * price_val}\n\n*Delivering to:*\n{address_str}\n\nWe will notify you when it ships. 📦"
    await send_telegram_message(chat_id, reply)

    # Reset conversation state
    await update_conversation_state(chat_id, ConversationState.GENERAL, {})


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


# =============================
# TELEGRAM WEBHOOKS
# =============================
async def handle_telegram_message(update: Update, context: CallbackContext):
    if not update.message or not update.message.text:
        return

    chat_id = str(update.message.chat_id)
    user_text = update.message.text

    logger.info(f"📩 Message from {chat_id}: {user_text}")

    # 1. Get User Context from DB
    user_profile = await get_user_profile(chat_id)
    recent_orders = await get_recent_orders(chat_id)
    conversation_state = await get_conversation_state(chat_id)
    user_addresses = await get_user_addresses(chat_id)

    # 2. Check if we are in the middle of a specific Address State
    if conversation_state.get("state") == ConversationState.ORDERING_ADDRESS:
        await handle_address_collection(chat_id, user_text, conversation_state)
        return

    # 3. Process with Groq LLM
    # This calls the function you already have that uses groq_client.chat.completions
    ai_response = process_ai_interaction(
        user_text, user_profile, recent_orders, user_addresses, conversation_state
    )

    # 4. Handle the AI's Intent & Update DB
    intent = ai_response.get("intent")
    reply_text = ai_response.get("reply_text", "I'm sorry, I didn't understand that.")

    # Update User Profile if AI extracted new info (Name, Age, etc.)
    if ai_response.get("user_info"):
        await update_user_profile(chat_id, ai_response["user_info"])

    # If the AI says we are now ordering, update the conversation state
    if intent == "ORDER_MEDICINE":
        temp_data = {
            "medicine_name": ai_response.get("medicine_name"),
            "quantity": ai_response.get("quantity"),
            "price": ai_response.get("price"),
        }
        await update_conversation_state(chat_id, "ORDERING_MEDICINE", temp_data)

    # If the AI triggers address collection
    if intent == "SELECT_ADDRESS":
        temp_data = conversation_state.get("temp_data", {})
        temp_data["addresses"] = user_addresses
        await show_address_selection(chat_id, temp_data)
        return

    # 5. Send the final text back to Telegram
    await send_telegram_message(chat_id, reply_text)


async def handle_telegram_callback_query(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()  # Acknowledge the callback query

    chat_id = str(query.message.chat_id)
    user_text = query.data  # The callback_data from the inline keyboard

    logger.info(f"📩 Callback Query from {chat_id}: {user_text}")

    # Get User Context
    user_profile = await get_user_profile(chat_id)
    recent_orders = await get_recent_orders(chat_id)
    conversation_state = await get_conversation_state(chat_id)
    user_addresses = await get_user_addresses(chat_id)

    # Handle Address Selection Flow (State-based)
    if conversation_state["state"] == ConversationState.ORDERING_ADDRESS:
        await handle_address_collection(chat_id, user_text, conversation_state)
        return

    elif conversation_state["state"] == ConversationState.ORDERING_ADDRESS_CONFIRM:
        temp_data = conversation_state.get("temp_data", {})

        if user_text == "save_yes":
            temp_data["save_address"] = True
            await complete_order_with_address(chat_id, temp_data)
        elif user_text == "save_no":
            temp_data["save_address"] = False
            await complete_order_with_address(chat_id, temp_data)
        elif user_text == "confirm_addr":
            # User confirmed selected address
            await complete_order_with_address(chat_id, temp_data)
        elif user_text == "new_addr":
            # User wants to use a different address
            await show_address_selection(chat_id, temp_data)
        return

    # Regular AI Processing for callback data via Orchestrator
    initial_state = AgentState(
        messages=[],  # Orchestrator expects messages to be added internally
        extracted_meds=[],
        safety_validated=True,  # Assume true for now, agents will validate
        validation_reasons=[],
        inventory_checked=True,  # Assume true for now, agents will validate
        inventory_results=[],
        fulfillment_status=None,
        final_response=None,
        channel="Telegram",
        channel_metadata={"chat_id": chat_id, "callback_data": user_text},
    )

    # The orchestrator expects the user message to be part of the messages list
    # We'll add it as a HumanMessage for the orchestrator to process
    from langchain_core.messages import HumanMessage

    initial_state["messages"].append(HumanMessage(content=user_text))

    orchestrator_output = await orchestrator.ainvoke(initial_state)
    final_response = orchestrator_output.get(
        "final_response", "Sorry, I'm having trouble processing your request right now."
    )

    await send_telegram_message(chat_id, final_response)


@router.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    if not dispatcher:
        raise HTTPException(status_code=500, detail="Telegram Bot not configured.")

    update = Update.de_json(await request.json(), bot)
    dispatcher.process_update(update)
    return {"status": "ok"}


# Add handlers to the dispatcher
if dispatcher:
    dispatcher.add_handler(
        MessageHandler(Filters.text & ~Filters.command, handle_telegram_message)
    )
    dispatcher.add_handler(
        MessageHandler(Filters.command, handle_telegram_message)
    )  # Handle commands like /start
    from telegram.ext import CallbackQueryHandler

    dispatcher.add_handler(CallbackQueryHandler(handle_telegram_callback_query))
