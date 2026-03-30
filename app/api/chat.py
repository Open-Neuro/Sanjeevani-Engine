import os
import time
import uuid
import json
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File, Form
from pydantic import BaseModel
from groq import Groq
import groq

from app.database.mongo_client import get_db
from app.config import settings
from app.utils.logger import get_logger

router = APIRouter(prefix="/chat", tags=["Chatbot"])
logger = get_logger(__name__)

# Initialize Groq client
if settings.GROQ_API_KEY:
    groq_client = Groq(api_key=settings.GROQ_API_KEY)
else:
    groq_client = None

class ChatRequest(BaseModel):
    message: str
    phone: Optional[str] = None
    session_id: Optional[str] = None
    merchant_id: Optional[str] = "samaypowade9@gmail.com"

class ChatResponse(BaseModel):
    text: str
    session_id: str
    buttons: Optional[list] = []

def generate_session_id():
    return str(uuid.uuid4())

@router.get("/sessions")
def get_sessions(phone: str = "", merchant_id: str = "samaypowade9@gmail.com"):
    db = get_db()
    query = {"merchant_id": merchant_id}
    if phone:
        query["phone"] = phone
    
    sessions_cursor = db["chat_sessions"].find(query).sort("updated_at", -1)
    sessions = []
    for s in sessions_cursor:
        sessions.append({
            "session_id": s["session_id"],
            "title": s.get("title", "Chat Request"),
            "updated_at": s.get("updated_at")
        })
    return sessions

@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, merchant_id: str = "samaypowade9@gmail.com"):
    db = get_db()
    session = db["chat_sessions"].find_one({"session_id": session_id, "merchant_id": merchant_id})
    if session:
        db["chat_sessions"].delete_one({"session_id": session_id})
        db["chat_history"].delete_many({"session_id": session_id})
    return {"status": "ok"}

@router.get("/history/{session_id}")
def get_history(session_id: str, merchant_id: str = "samaypowade9@gmail.com"):
    db = get_db()
    history = list(db["chat_history"].find({"session_id": session_id, "merchant_id": merchant_id}).sort("timestamp", 1))
    for h in history:
        h.pop("_id", None)
    return history

@router.post("", response_model=ChatResponse)
def process_chat(request: ChatRequest):
    db = get_db()
    session_id = request.session_id
    if not session_id:
        session_id = generate_session_id()
        # Initialize session
        db["chat_sessions"].insert_one({
            "session_id": session_id,
            "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
            "phone": request.phone,
            "title": f"Chat {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        })
    
    # Save user message
    db["chat_history"].insert_one({
        "session_id": session_id,
        "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
        "role": "user",
        "text": request.message,
        "timestamp": datetime.utcnow()
    })
    
    # Update session
    db["chat_sessions"].update_one(
        {"session_id": session_id},
        {"$set": {"updated_at": datetime.utcnow()}}
    )

    if not groq_client:
        bot_response = "I am currently running in offline mode because the AI key is not configured. I can still help you browse manually!"
        db["chat_history"].insert_one({
            "session_id": session_id,
            "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
            "role": "bot",
            "text": bot_response,
            "timestamp": datetime.utcnow()
        })
        return {"text": bot_response, "session_id": session_id}

    # Fetch history for context
    history_cursor = db["chat_history"].find({"session_id": session_id}).sort("timestamp", 1).limit(10)
    messages = [
        {"role": "system", "content": """You are SanjeevaniRxAI, a helpful pharmacy assistant.
You can help users find medicines, answer medical queries securely, and place orders.

If a user explicitly asks to order a medicine, you MUST include a special JSON block at the very end of your response to trigger the ordering system.
Format:
```json
{
  "PLACE_ORDER": true,
  "medicine_name": "Name of medicine",
  "quantity": 1
}
```
Otherwise, just respond normally and be very concise and helpful."""}
    ]
    
    for h in history_cursor:
        role = "assistant" if h["role"] == "bot" else "user"
        messages.append({"role": role, "content": h["text"]})
        
    try:
        completion = groq_client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=messages,
            temperature=0.3,
            max_tokens=500
        )
        bot_response = completion.choices[0].message.content
        
    except Exception as e:
        logger.error(f"Groq API error: {e}")
        bot_response = "I'm having trouble connecting to my AI brain right now. Please try again."

    # Process potential orders
    placed_order_id = None
    if "```json" in bot_response and "PLACE_ORDER" in bot_response:
        try:
            # Extract JSON block
            json_str = bot_response.split("```json")[1].split("```")[0].strip()
            order_data = json.loads(json_str)
            if order_data.get("PLACE_ORDER"):
                med_name = order_data.get("medicine_name", "Unknown Medicine")
                qty = order_data.get("quantity", 1)
                
                # Fetch product to get price
                product = db["products"].find_one({
                    "Medicine Name": {"$regex": f"^{med_name}$", "$options": "i"},
                    "merchant_id": request.merchant_id or "samaypowade9@gmail.com"
                })
                price = product.get("MRP", 100) if product else 100
                
                placed_order_id = f"CHAT-{int(time.time())}"
                new_order = {
                    "Order ID": placed_order_id,
                    "Patient Name": request.phone or "Guest User",
                    "Medicine Name": med_name,
                    "Quantity": qty,
                    "Total Amount": price * qty,
                    "Order Status": "Pending",
                    "Order Channel": "Chatbot",
                    "Order Date": datetime.utcnow(),
                    "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
                    "Payment Method": "Cash on Delivery",
                    "Contact Number": request.phone or ""
                }
                db["consumer_orders"].insert_one(new_order)
                logger.info(f"Chatbot placed order: {placed_order_id}")
                
            # Clean up response for user
            bot_response = bot_response.split("```json")[0].strip()
            
        except Exception as e:
            logger.error(f"Failed to parse order from chat: {e}")
    
    if placed_order_id:
        bot_response += f"\n\n✅ Your order #{placed_order_id} has been placed successfully and will appear on our dashboard for processing!"
        buttons = [{"id": "track", "title": "Track Order"}]
    else:
        buttons = []

    # Save bot response
    db["chat_history"].insert_one({
        "session_id": session_id,
        "merchant_id": request.merchant_id or "samaypowade9@gmail.com",
        "role": "bot",
        "text": bot_response,
        "timestamp": datetime.utcnow()
    })
    
    return {"text": bot_response, "session_id": session_id, "buttons": buttons}


@router.post("/upload-prescription")
async def upload_prescription(
    file: UploadFile = File(...),
    phone: str = Form(None),
    session_id: str = Form(None),
    merchant_id: str = Form("samaypowade9@gmail.com")
):
    db = get_db()
    if not session_id:
        session_id = generate_session_id()
        db["chat_sessions"].insert_one({
            "session_id": session_id,
            "merchant_id": merchant_id or "samaypowade9@gmail.com",
            "phone": phone,
            "title": f"Prescription Upload {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        })
        
    placed_order_id = f"RX-{int(time.time())}"
    new_order = {
        "Order ID": placed_order_id,
        "Patient Name": phone or "Guest User",
        "Medicine Name": "Prescription Order",
        "Quantity": 1,
        "Total Amount": 0,
        "Order Status": "Pending",
        "Order Channel": "Chatbot",
        "Notes": "Uploaded Prescription Image",
        "Order Date": datetime.utcnow(),
        "merchant_id": merchant_id or "samaypowade9@gmail.com",
        "Payment Method": "Pending Verification",
        "Contact Number": phone or ""
    }
    db["consumer_orders"].insert_one(new_order)
    
    message = (f"✅ **Prescription Uploaded Successfully!**\n\n"
               f"Order {placed_order_id} has been generated and sent to our pharmacists for review. "
               f"You can check the dashboard for live status.")
               
    # Save bot response
    db["chat_history"].insert_one({
        "session_id": session_id,
        "merchant_id": merchant_id or "samaypowade9@gmail.com",
        "role": "bot",
        "text": message,
        "timestamp": datetime.utcnow()
    })
               
    return {"message": message, "session_id": session_id}
