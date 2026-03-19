import requests
from app.agents.state import AgentState
import uuid
from datetime import datetime
from app.database.mongo_client import get_db
from app.config import settings
from langfuse.callback import CallbackHandler

# Langfuse tracking
langfuse_handler = CallbackHandler(
    secret_key=settings.LANGFUSE_SECRET_KEY,
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    host=settings.LANGFUSE_HOST
)


def fulfillment_dispatcher_node(state: AgentState) -> AgentState:
    """
    Agent 5: Fulfillment & Notification Dispatcher.
    Saves order records to multiple collections for dashboard and tracking.
    """
    print("🚀 Agent 5 (Fulfillment Dispatcher) activating...")

    # Track with Langfuse
    from langfuse import Langfuse
    langfuse = Langfuse(
        secret_key=settings.LANGFUSE_SECRET_KEY,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        host=settings.LANGFUSE_HOST
    )
    
    trace = langfuse.trace(
        name="agent-5-fulfillment",
        metadata={"agent": "Agent 5: Fulfillment Dispatcher"}
    )

    db = get_db()
    extracted = state.get("extracted_meds", [])

    # Check if we should fulfill or reject
    is_safe = state.get("safety_validated", False)
    is_available = state.get("inventory_checked", False)

    # For the dashboard, we use these statuses
    status = "Confirmed" if (is_safe and is_available) else "Rejected"

    channel = state.get("channel", "Web")
    user_id = "guest_user"
    patient_name = "AI Customer"

    if state.get("channel_metadata") and isinstance(
        state.get("channel_metadata"), dict
    ):
        user_id = state["channel_metadata"].get("user_id", "guest_user")
        patient_name = (
            state["channel_metadata"].get("patient_name")
            or state["channel_metadata"].get("ProfileName")
            or "AI Customer"
        )

    orders_created = []

    for med in extracted:
        med_name = med.get("name")
        qty = med.get("quantity", med.get("qty", 1))  # Handle both variants
        price = med.get("price", 250) or 250
        product_id = med.get("product_id", "")

        # Ensure qty and price are numbers
        try:
            qty = float(qty)
            price = float(price)
        except:
            qty = 1.0
            price = 250.0

        span = trace.span(
            name=f"create-order-{med_name}",
            input={"medicine": med_name, "quantity": qty, "price": price, "status": status}
        )

        if status == "Confirmed":
            # Deduct from inventory (actual 'products' collection used by dashboard)
            db["products"].update_one(
                {"Medicine Name": {"$regex": f"^{med_name}$", "$options": "i"}},
                {"$inc": {"Current Stock": -qty}},
            )
            # Also update 'inventory' collection used by agents
            db["inventory"].update_one(
                {"$or": [{"product_id": med_name}, {"medicine_name": med_name}]},
                {"$inc": {"current_stock": -qty}},
            )

        # Create order record for 'orders' collection (backend tracking)
        order_id = f"{channel[:3].upper()}{datetime.utcnow().strftime('%Y%m%d%H%M%S')}{str(uuid.uuid4())[:4]}"

        order_doc = {
            "order_id": order_id,
            "user_id": user_id,
            "medicine_name": med_name,
            "product_id": product_id,
            "quantity": qty,
            "price": price,
            "total_amount": qty * price,
            "order_status": status.lower(),
            "channel": channel,
            "requires_prescription": med.get("requires_prescription", "No"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        db["orders"].insert_one(order_doc)

        # Create record for 'consumer_orders' collection (Dashboard display)
        dashboard_order = {
            "Patient Name": patient_name,
            "Patient ID": user_id,
            "Contact Number": user_id,
            "Order ID": order_id,
            "Order Date": datetime.utcnow(),
            "Order Channel": channel,
            "Order Status": status,
            "Medicine Name": med_name,
            "Quantity Ordered": qty,
            "Unit Price": price,
            "Total Amount": qty * price,
            "requires_prescription": med.get("requires_prescription", "No"),
            "source": f"AI {channel} Bot",
        }
        db["consumer_orders"].insert_one(dashboard_order)

        # Add to unified_orders (New Requirement for multi-channel consolidation)
        unified_doc = {
            "order_id": order_id,
            "patient_name": patient_name,
            "patient_id": user_id,
            "contact_number": user_id,
            "medicine_name": med_name,
            "quantity": qty,
            "unit_price": price,
            "total_amount": qty * price,
            "order_channel": channel,
            "order_status": status,
            "order_date": datetime.utcnow().strftime("%Y-%m-%d"),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        db["unified_orders"].insert_one(unified_doc)

        orders_created.append(order_id)
        span.end(output={"order_id": order_id, "status": status})
        print(f"📦 Order {order_id} stored in database with status: {status}")

    trace.update(output={"orders_created": orders_created, "status": status})

    if status == "Confirmed":
        print(f"📧 AI Auto-Confirmed Order successfully.")
        return {
            "fulfillment_status": "SUCCESS",
            "steps": [
                "Agent 5 (Fulfillment) AUTO-CONFIRMED order and updated dashboard"
            ],
            "current_agent": "Agent 5: Fulfillment",
        }
    else:
        print(f"❌ Order rejected due to safety or inventory issues.")
        return {
            "fulfillment_status": "REJECTED",
            "steps": [
                "Agent 5 (Fulfillment) logged rejected order for dashboard audit"
            ],
            "current_agent": "Agent 5: Fulfillment",
        }
