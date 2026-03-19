from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
import json
import logging
import uuid
import os
import re
from groq import Groq
from app.database.mongo_client import get_db
from app.config import settings
from app.utils.ocr_service import (
    extract_text_from_image,
    extract_medicines_from_text,
    verify_prescription_with_llm,
)
from app.database.mongo_client import get_db
from app.api.whatsapp import (
    get_user_profile,
    get_recent_orders,
    get_conversation_state,
    get_user_addresses,
    process_ai_interaction,
    update_user_profile,
    update_conversation_state,
    handle_address_collection,
    show_address_selection,
    complete_order_with_address,
    ConversationState,
    orchestrator,
    AgentState,
    create_order,
    save_user_address,
)
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Web Chatbot"])

# --- Models ---


class WebChatRequest(BaseModel):
    message: str
    phone: str = "+1234567890"
    session_id: Optional[str] = None


class WebChatResponse(BaseModel):
    text: str
    buttons: Optional[List[Dict[str, str]]] = None
    intent: Optional[str] = None
    session_id: str


class ChatHistoryItem(BaseModel):
    session_id: str
    title: str
    last_message: str
    timestamp: datetime


# --- Database Helpers ---


def detect_user_language(user_text: str) -> Dict[str, str]:
    """Detect whether user prefers English or Hindi/Hinglish."""
    text = (user_text or "").strip()
    lowered = text.lower()

    has_devanagari = any("\u0900" <= ch <= "\u097F" for ch in text)
    if has_devanagari:
        return {"language": "hi", "script": "devanagari"}

    hindi_markers = {
        "mera", "meri", "mere", "mujhe", "kya", "kyu", "kyun", "kaise", "haan",
        "nahi", "nhi", "bhai", "dawai", "davai", "chahiye", "chahiyee", "krdo",
        "kar do", "karna", "batao", "bolo", "kitna", "kitni", "order", "jaldi",
    }
    marker_hits = sum(
        1
        for marker in hindi_markers
        if re.search(rf"\b{re.escape(marker)}\b", lowered)
    )
    if marker_hits >= 2:
        return {"language": "hi", "script": "latin"}

    return {"language": "en", "script": "latin"}


def translate_reply_if_needed(user_text: str, reply_text: str) -> str:
    """Translate assistant reply to user language when Hindi/Hinglish is detected."""
    if not reply_text:
        return reply_text

    lang_pref = detect_user_language(user_text)
    if lang_pref["language"] != "hi":
        return reply_text

    target_style = (
        "natural Hindi in Devanagari script"
        if lang_pref["script"] == "devanagari"
        else "natural Hinglish in Latin script"
    )

    try:
        client = Groq(api_key=settings.GROQ_API_KEY)
        translated = client.chat.completions.create(
            model=settings.GROQ_MODEL,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a pharmacy chatbot translator. "
                        "Translate the assistant response into the requested language style. "
                        "Keep medicine names, numbers, order IDs, prices, markdown, bullets, and emojis intact. "
                        "Return only translated text."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Target style: {target_style}\n"
                        f"User message: {user_text}\n"
                        f"Assistant response: {reply_text}"
                    ),
                },
            ],
        )
        localized = (translated.choices[0].message.content or "").strip()
        return localized if localized else reply_text
    except Exception as e:
        logger.warning(f"Translation fallback to original text: {e}")
        return reply_text


async def save_and_build_response(
    *,
    user_number: str,
    user_text: str,
    session_id: str,
    text: str,
    intent: str,
    buttons: Optional[List[Dict[str, str]]] = None,
) -> WebChatResponse:
    localized_text = translate_reply_if_needed(user_text, text)
    await save_chat_message(user_number, session_id, "bot", localized_text)
    return WebChatResponse(
        text=localized_text,
        buttons=buttons if buttons else None,
        intent=intent,
        session_id=session_id,
    )


async def save_chat_message(phone: str, session_id: str, role: str, text: str):
    db = get_db()
    message = {
        "user_id": phone,
        "session_id": session_id,
        "role": role,
        "text": text,
        "timestamp": datetime.utcnow(),
    }
    db.chat_messages.insert_one(message)

    # Update session summary
    db.chat_sessions.update_one(
        {"session_id": session_id},
        {
            "$set": {
                "user_id": phone,
                "last_message": text,
                "updated_at": datetime.utcnow(),
            },
            "$setOnInsert": {
                "title": text[:30] + ("..." if len(text) > 30 else ""),
                "created_at": datetime.utcnow(),
            },
        },
        upsert=True,
    )


@router.get("/chat/sessions", response_model=List[ChatHistoryItem])
async def get_chat_sessions(phone: str = "+1234567890"):
    db = get_db()
    sessions = (
        db.chat_sessions.find({"user_id": phone}).sort("updated_at", -1).limit(20)
    )
    return [
        ChatHistoryItem(
            session_id=s["session_id"],
            title=s["title"],
            last_message=s.get("last_message", ""),
            timestamp=s.get("updated_at", s.get("created_at", datetime.utcnow())),
        )
        for s in sessions
    ]


@router.get("/chat/history/{session_id}")
async def get_session_history(session_id: str):
    db = get_db()
    messages = db.chat_messages.find({"session_id": session_id}).sort("timestamp", 1)
    return [
        {"role": m["role"], "text": m["text"], "timestamp": m["timestamp"]}
        for m in messages
    ]


@router.delete("/chat/sessions/{session_id}")
async def delete_session(session_id: str):
    db = get_db()
    # Delete messages associated with this session
    db.chat_messages.delete_many({"session_id": session_id})
    # Delete the session summary
    result = db.chat_sessions.delete_one({"session_id": session_id})

    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "success", "message": f"Session {session_id} deleted"}


@router.delete("/chat/sessions")
async def delete_all_sessions(phone: str = "+1234567890"):
    db = get_db()
    # Delete all messages for this phone number
    db.chat_messages.delete_many({"user_id": phone})
    # Delete all session summaries for this phone number
    db.chat_sessions.delete_many({"user_id": phone})

    return {"status": "success", "message": f"All chat history for {phone} deleted"}


@router.post("/chat/upload-prescription")
async def upload_prescription(
    file: UploadFile = File(...),
    phone: str = Form("+1234567890"),
    session_id: Optional[str] = Form(None),
):
    """Handle prescription image upload with OCR and LLM verification"""
    try:
        # Create uploads directory if it doesn't exist
        upload_dir = "uploads/prescriptions"
        os.makedirs(upload_dir, exist_ok=True)

        # Save file with unique name
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = (
            f"{phone}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}{file_extension}"
        )
        file_path = os.path.join(upload_dir, unique_filename)

        # Save the file
        with open(file_path, "wb") as buffer:
            content = await file.read()
            buffer.write(content)

        logger.info(f"📸 Prescription saved: {file_path}")

        # Step 1: Extract text using OCR
        logger.info("🔍 Starting OCR extraction...")
        ocr_text = extract_text_from_image(file_path)

        if not ocr_text:
            return {
                "status": "error",
                "message": "❌ Failed to extract text from image. Please ensure the image is clear and try again.",
                "file_path": file_path,
            }

        logger.info(f"✅ OCR extracted {len(ocr_text)} characters")

        # Step 2: Extract medicine names from OCR text
        extracted_medicines = extract_medicines_from_text(ocr_text)
        logger.info(f"💊 Found {len(extracted_medicines)} potential medicines")

        # Step 3: Verify with LLM
        logger.info("🤖 Verifying prescription with LLM...")
        groq_client = Groq(api_key=settings.GROQ_API_KEY)
        verification_result = await verify_prescription_with_llm(
            ocr_text, extracted_medicines, groq_client
        )

        # Step 4: Match medicines with database inventory
        db = get_db()
        verified_medicines = []

        for med_info in verification_result.get("medicines", []):
            med_name = med_info.get("name", "")

            # Search in inventory
            import re

            regex_pattern = re.compile(f".*{med_name}.*", re.IGNORECASE)

            inventory_item = db["inventory"].find_one(
                {
                    "$or": [
                        {"product_id": {"$regex": regex_pattern}},
                        {"medicine_name": {"$regex": regex_pattern}},
                    ]
                }
            )

            if inventory_item:
                verified_medicines.append(
                    {
                        "name": inventory_item.get("medicine_name", med_name),
                        "product_id": inventory_item.get("product_id", ""),
                        "dosage": med_info.get("dosage", ""),
                        "frequency": med_info.get("frequency", ""),
                        "price": float(inventory_item.get("price", 0)),
                        "stock": float(inventory_item.get("current_stock", 0)),
                        "requires_prescription": inventory_item.get(
                            "requires_prescription", "No"
                        ),
                        "in_database": True,
                    }
                )
            else:
                # Medicine not found in database
                verified_medicines.append(
                    {
                        "name": med_name,
                        "dosage": med_info.get("dosage", ""),
                        "frequency": med_info.get("frequency", ""),
                        "in_database": False,
                        "warning": "Not found in our inventory",
                    }
                )

        logger.info(f"✅ Verified {len(verified_medicines)} medicines against database")

        # Update conversation state
        conversation_state = await get_conversation_state(phone)
        temp_data = conversation_state.get("temp_data", {})
        temp_data["prescription_uploaded"] = True
        temp_data["prescription_file"] = file_path
        temp_data["ocr_text"] = ocr_text
        temp_data["verified_medicines"] = verified_medicines
        temp_data["verification_result"] = verification_result

        await update_conversation_state(
            phone, conversation_state.get("state", ConversationState.GENERAL), temp_data
        )

        # Step 5: Run Orchestrator for the final "Agent-led" response
        # This makes sure Safety, Inventory, and Supervisor agents all participate
        initial_state = AgentState(
            messages=[
                HumanMessage(
                    content=f"I'm uploading a prescription. Extract and process these: {ocr_text}"
                )
            ],
            extracted_meds=[],
            safety_validated=True,  # Validating because it's uploaded
            prescription_required=True,
            prescription_uploaded=True,
            validation_reasons=[],
            inventory_checked=True,
            inventory_results=[],
            fulfillment_status=None,
            final_response=None,
            channel="Web",
            channel_metadata={"user_id": phone, "is_prescription": True},
        )

        orchestrator_output = await orchestrator.ainvoke(initial_state)
        agent_response = (
            orchestrator_output.get("final_response")
            or "Prescription received and processed."
        )

        is_valid = verification_result.get("is_valid_prescription", False)
        confidence = verification_result.get("confidence", 0)

        return {
            "status": "success" if is_valid else "warning",
            "message": agent_response,
            "file_path": file_path,
            "ocr_text": ocr_text[:500],
            "medicines": verified_medicines,
            "verification": verification_result,
            "confidence": confidence,
        }

    except Exception as e:
        logger.error(f"Prescription upload error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to process prescription: {str(e)}"
        )


# --- Main Chat Endpoint ---


@router.post("/chat", response_model=WebChatResponse)
async def web_chat(req: WebChatRequest):
    user_number = req.phone
    user_text = req.message
    language_pref = detect_user_language(user_text)
    session_id = req.session_id or str(uuid.uuid4())

    logger.info(f"📩 Web Chat from {user_number} (Session: {session_id}): {user_text}")

    # Save User Message
    await save_chat_message(user_number, session_id, "user", user_text)

    # Get User Context
    user_profile = await get_user_profile(user_number)
    recent_orders = await get_recent_orders(user_number)
    conversation_state = await get_conversation_state(user_number)
    user_addresses = await get_user_addresses(user_number)

    # 1. Simplified Address Collection (Single Step)
    if conversation_state["state"] == ConversationState.ORDERING_ADDRESS:
        temp_data = conversation_state.get("temp_data", {})
        temp_data["address_info"] = {
            "address_line1": user_text,
            "address_line2": "",
            "city": "",
            "state": "",
            "pincode": "",
            "landmark": "",
            "address_type": "Home",
        }
        temp_data["save_address"] = True
        await save_user_address(user_number, temp_data["address_info"])
        order_id = await complete_order_with_address(user_number, temp_data)
        await update_conversation_state(user_number, ConversationState.GENERAL, {})

        reply_text = f"✅ *Order Placed Successfully!*\n\nYour order for *{temp_data.get('medicine_name', 'Medicine')}* has been placed. \n\n*Delivery Address:* {user_text}\n*Order ID:* #{order_id}\n\nIt will appear on your dashboard shortly!"
        return await save_and_build_response(
            user_number=user_number,
            user_text=user_text,
            session_id=session_id,
            text=reply_text,
            intent="ORDER_PLACED",
        )

    # Process AI Intent
    ai_response = process_ai_interaction(
        user_text, user_profile, recent_orders, user_addresses, conversation_state
    )
    intent = ai_response.get("intent", "GENERAL")
    reply_text = ai_response.get("reply_text", "I'm having trouble processing that.")
    buttons = []

    # 0. Handle Welcome / New Session
    if (
        user_text.lower() in ["hi", "hello", "hey", "start"]
        and conversation_state.get("state") == ConversationState.GENERAL
    ):
        welcome_msg = "👋 Welcome back to SanjeevaniRxAI! I'm your smart pharmacy assistant.\n\nHow can I help you today? You can ask me to order medicines, check stock, or track your recent orders."
        buttons = [
            {"id": "browse_meds", "title": "Browse Medicines 💊"},
            {"id": "track_order", "title": "Track Order 📦"},
        ]
        return await save_and_build_response(
            user_number=user_number,
            user_text=user_text,
            session_id=session_id,
            text=welcome_msg,
            buttons=buttons,
            intent="WELCOME",
        )

    # --- Handling Specific Intents ---
    is_affirming = user_text.lower() in [
        "yes",
        "y",
        "confirm",
        "ok",
        "sure",
        "correct",
        "place order",
        "confirm order",
        "confirm order ✅",
    ]

    # 2. Handle Prescription Upload Simulation
    if "Uploaded prescription" in user_text:
        temp_data = conversation_state.get("temp_data", {})
        temp_data["prescription_uploaded"] = True
        await update_conversation_state(
            user_number, conversation_state["state"], temp_data
        )
        reply = "✅ Prescription received and verified! I've added it to your order. Should we proceed with the delivery address?"
        return await save_and_build_response(
            user_number=user_number,
            user_text=user_text,
            session_id=session_id,
            text=reply,
            intent="GENERAL",
        )

    # 3. Handle Order Confirmation and Placement
    if intent == "ORDER_PLACED" or (
        is_affirming
        and conversation_state.get("state") == ConversationState.ORDER_CONFIRMED
    ):
        temp_data = conversation_state.get("temp_data", {})
        temp_data["channel"] = "Web"

        if temp_data.get("address_info"):
            order_id = await complete_order_with_address(user_number, temp_data)
            await update_conversation_state(user_number, ConversationState.GENERAL, {})

            med_name = temp_data.get("medicine_name", "Medicine")
            qty = temp_data.get("quantity", 1)
            raw_price = temp_data.get("price", 250)

            # Safe conversion
            try:
                qty_val = int(float(qty)) if qty and str(qty).strip() else 1
            except (ValueError, TypeError):
                qty_val = 1

            try:
                price_val = (
                    int(float(raw_price))
                    if raw_price and str(raw_price).strip()
                    else 250
                )
            except (ValueError, TypeError):
                price_val = 250

            total = qty_val * price_val

            success_msg = (
                f"✅ **Order Confirmed & Placed!**\n\n"
                f"**Order ID:** #{order_id}\n"
                f"**Medicine:** {med_name}\n"
                f"**Quantity:** {qty_val}\n"
                f"**Total Amount:** ₹{total}\n"
                f"**Status:** Fulfilled (Web)\n\n"
                f"Your order has been saved and updated on the dashboard. We'll notify you once it's out for delivery! 🚀"
            )
            return await save_and_build_response(
                user_number=user_number,
                user_text=user_text,
                session_id=session_id,
                text=success_msg,
                intent="ORDER_PLACED",
            )
        else:
            addresses = await get_user_addresses(user_number)
            if addresses:
                for i, addr in enumerate(addresses[:3]):
                    addr_summary = f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:15]}..."
                    buttons.append({"id": f"addr_{i}", "title": addr_summary})
                buttons.append({"id": "addr_new", "title": "➕ Add New Address"})
                reply_text = "Order confirmed! Please select your delivery address:"
                await update_conversation_state(
                    user_number, ConversationState.ORDERING_ADDRESS, temp_data
                )
            else:
                reply_text = "Order confirmed! Please enter your *Full Delivery Address* in a single message:"
                await update_conversation_state(
                    user_number, ConversationState.ORDERING_ADDRESS, temp_data
                )

            return await save_and_build_response(
                user_number=user_number,
                user_text=user_text,
                session_id=session_id,
                text=reply_text,
                buttons=buttons if buttons else None,
                intent="SELECT_ADDRESS",
            )

    # 4. Handle Address Selection from AI
    if intent == "SELECT_ADDRESS" or intent in [
        "ASK_ADDRESS_LINE1",
        "ASK_ADDRESS_LINE2",
        "ASK_CITY",
        "ASK_STATE",
        "ASK_PINCODE",
        "ASK_LANDMARK",
    ]:
        addresses = await get_user_addresses(user_number)
        temp_data = conversation_state.get("temp_data", {})
        if addresses:
            for i, addr in enumerate(addresses[:3]):
                addr_summary = f"{addr.get('address_type', 'Home')}: {addr['address_line1'][:15]}..."
                buttons.append({"id": f"addr_{i}", "title": addr_summary})
            buttons.append({"id": "addr_new", "title": "➕ Add New Address"})
            reply_text = "Select delivery address:"
        else:
            reply_text = "Please enter your *Full Delivery Address* (including City, State, and Pincode) in a single message:"

        await update_conversation_state(
            user_number, ConversationState.ORDERING_ADDRESS, temp_data
        )
        return await save_and_build_response(
            user_number=user_number,
            user_text=user_text,
            session_id=session_id,
            text=reply_text,
            buttons=buttons if buttons else None,
            intent="SELECT_ADDRESS",
        )

    # 5. Handle Medicine Ordering with Agent Checks (Safety, Inventory)
    elif intent == "ORDER_MEDICINE" or intent == "GENERAL":
        if user_text.lower() in ["hi", "hello", "hey", "start", "ok", "yes", "no"]:
            pass
        else:
            try:
                initial_state = AgentState(
                    messages=[HumanMessage(content=user_text)],
                    extracted_meds=[],
                    safety_validated=True,
                    validation_reasons=[],
                    inventory_checked=True,
                    inventory_results=[],
                    fulfillment_status=None,
                    final_response=None,
                    channel="Web",
                    channel_metadata={
                        "user_id": user_number,
                        "preferred_language": language_pref["language"],
                        "preferred_script": language_pref["script"],
                    },
                )
                orchestrator_output = await orchestrator.ainvoke(initial_state)

                # Store orchestrator output in temp_data for later use
                temp_data = conversation_state.get("temp_data", {})
                temp_data["orchestrator_output"] = {
                    "inventory_results": orchestrator_output.get("inventory_results"),
                    "extracted_meds": orchestrator_output.get("extracted_meds"),
                    "safety_validated": orchestrator_output.get("safety_validated"),
                    "fulfillment_status": orchestrator_output.get("fulfillment_status"),
                }

                if orchestrator_output.get("safety_validated") is False:
                    temp_data = conversation_state.get("temp_data", {})
                    if not temp_data.get("prescription_uploaded"):
                        # Prescription required - ask user to upload
                        extracted_meds = orchestrator_output.get("extracted_meds", [])
                        med_names = ", ".join([m.get("name", "") for m in extracted_meds if m.get("name")])
                        
                        # Simple, clear message
                        reply_text = (
                            f"⚠️ **PRESCRIPTION REQUIRED** ⚠️\n\n"
                            f"**{med_names}** requires a valid doctor's prescription.\n\n"
                            f"📸 **Please upload your prescription photo here** and I'll verify it for you!"
                        )

                        buttons = [
                            {"id": "upload_rx", "title": "📷 Upload Prescription"}
                        ]

                        return await save_and_build_response(
                            user_number=user_number,
                            user_text=user_text,
                            session_id=session_id,
                            text=reply_text,
                            buttons=buttons,
                            intent="PRESCRIPTION_REQUIRED",
                        )
                    else:
                        # Prescription already uploaded, proceed
                        pass

                if orchestrator_output.get("inventory_checked") is False:
                    # Out of stock
                    inventory_results = orchestrator_output.get(
                        "inventory_results", [{}]
                    )
                    out_of_stock = (
                        inventory_results[0].get("out_of_stock", [])
                        if inventory_results
                        else []
                    )

                    if out_of_stock:
                        med_list = "\n• ".join(
                            [
                                f"{m['name']} (Requested: {m['wanted']}, Available: {m['available']})"
                                for m in out_of_stock
                            ]
                        )
                        reply_text = (
                            f"😔 **Out of Stock**\n\n"
                            f"Sorry, the following items are currently unavailable:\n\n"
                            f"• {med_list}\n\n"
                            f"Would you like to:\n"
                            f"• Order available items only\n"
                            f"• Get notified when back in stock\n"
                            f"• Browse alternatives"
                        )

                        buttons = [
                            {"id": "notify_stock", "title": "🔔 Notify Me"},
                            {"id": "browse_alt", "title": "🔍 Browse Alternatives"},
                        ]
                    else:
                        reply_text = orchestrator_output.get(
                            "final_response", "Some items are out of stock."
                        )
                        buttons = []

                    return await save_and_build_response(
                        user_number=user_number,
                        user_text=user_text,
                        session_id=session_id,
                        text=reply_text,
                        buttons=buttons if buttons else None,
                        intent="OUT_OF_STOCK",
                    )

                temp_data = conversation_state.get("temp_data", {})
                new_med = ai_response.get("medicine_name")
                if new_med:
                    temp_data["medicine_name"] = new_med
                new_qty = ai_response.get("quantity")
                if new_qty is not None:
                    temp_data["quantity"] = new_qty
                elif "quantity" not in temp_data:
                    temp_data["quantity"] = 1
                new_price = ai_response.get("price")
                if new_price is not None:
                    temp_data["price"] = new_price
                elif "price" not in temp_data:
                    temp_data["price"] = 250
                await update_conversation_state(
                    user_number, ConversationState.ORDER_CONFIRMED, temp_data
                )
                if intent == "CONFIRM_ORDER":
                    buttons.append({"id": "confirm_order", "title": "Confirm Order ✅"})
            except Exception as e:
                logger.error(f"Orchestrator Error: {e}")

    # Update user profile if needed
    if "user_info" in ai_response:
        await update_user_profile(user_number, ai_response["user_info"])

    return await save_and_build_response(
        user_number=user_number,
        user_text=user_text,
        session_id=session_id,
        text=reply_text,
        buttons=buttons if buttons else None,
        intent=intent,
    )
