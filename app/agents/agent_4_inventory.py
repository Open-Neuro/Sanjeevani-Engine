from app.agents.state import AgentState
from app.database.mongo_client import get_db
import re
from app.config import settings
from langfuse.callback import CallbackHandler

# Langfuse tracking
langfuse_handler = CallbackHandler(
    secret_key=settings.LANGFUSE_SECRET_KEY,
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    host=settings.LANGFUSE_HOST
)


def inventory_librarian_node(state: AgentState) -> AgentState:
    """
    Agent 4: Database & Inventory Librarian.
    Checks stock levels from the Master Data and returns REAL prices from database.
    Uses intelligent fuzzy matching to find medicines even with partial names.
    """
    print("📦 Agent 4 (Inventory Librarian) checking stock levels...")

    # Track with Langfuse
    from langfuse import Langfuse
    langfuse = Langfuse(
        secret_key=settings.LANGFUSE_SECRET_KEY,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        host=settings.LANGFUSE_HOST
    )
    
    trace = langfuse.trace(
        name="agent-4-inventory-check",
        metadata={"agent": "Agent 4: Inventory Librarian"}
    )

    extracted_meds = state.get("extracted_meds", [])

    in_stock_meds = []
    out_of_stock_meds = []

    db = get_db()

    # Check each medicine against real database with multiple search strategies
    for med in extracted_meds:
        med_name = med.get("name", "").strip()
        qty = med.get("quantity", 1)

        print(f"🔍 Searching for: '{med_name}'")
        
        span = trace.span(
            name=f"search-medicine-{med_name}",
            input={"medicine": med_name, "quantity": qty}
        )

        inventory_item = None

        # Strategy 1: Exact match (case-insensitive)
        regex_pattern = re.compile(f"^{re.escape(med_name)}$", re.IGNORECASE)
        inventory_item = db["inventory"].find_one(
            {
                "$or": [
                    {"product_id": {"$regex": regex_pattern}},
                    {"medicine_name": {"$regex": regex_pattern}},
                ]
            }
        )

        # Strategy 2: Starts with (e.g., "Omeprazole" matches "Omeprazole 20mg")
        if not inventory_item:
            regex_pattern = re.compile(f"^{re.escape(med_name)}", re.IGNORECASE)
            inventory_item = db["inventory"].find_one(
                {
                    "$or": [
                        {"product_id": {"$regex": regex_pattern}},
                        {"medicine_name": {"$regex": regex_pattern}},
                    ]
                }
            )
            if inventory_item:
                print(
                    f"  ✓ Found with 'starts with' match: {inventory_item.get('medicine_name')}"
                )

        # Strategy 3: Contains (partial match anywhere)
        if not inventory_item:
            regex_pattern = re.compile(f".*{re.escape(med_name)}.*", re.IGNORECASE)
            inventory_item = db["inventory"].find_one(
                {
                    "$or": [
                        {"product_id": {"$regex": regex_pattern}},
                        {"medicine_name": {"$regex": regex_pattern}},
                    ]
                }
            )
            if inventory_item:
                print(
                    f"  ✓ Found with 'contains' match: {inventory_item.get('medicine_name')}"
                )

        # Strategy 4: Word-based fuzzy match (split and match first word)
        if not inventory_item:
            # Get first word of medicine name
            first_word = med_name.split()[0] if med_name else ""
            if first_word and len(first_word) > 3:  # Only if word is meaningful
                regex_pattern = re.compile(f"^{re.escape(first_word)}", re.IGNORECASE)
                inventory_item = db["inventory"].find_one(
                    {
                        "$or": [
                            {"product_id": {"$regex": regex_pattern}},
                            {"medicine_name": {"$regex": regex_pattern}},
                        ]
                    }
                )
                if inventory_item:
                    print(
                        f"  ✓ Found with first word match: {inventory_item.get('medicine_name')}"
                    )

        if inventory_item:
            # Get REAL data from database
            stock = inventory_item.get("current_stock", 0)
            price = inventory_item.get("price", 0)
            medicine_name = inventory_item.get("medicine_name", med_name)
            product_id = inventory_item.get("product_id", "")
            requires_rx = inventory_item.get("requires_prescription", "No")

            try:
                stock = float(stock)
                price = float(price)
                qty = float(qty)
            except (ValueError, TypeError):
                stock = 0
                price = 0
                qty = 1

            if stock >= qty:
                result = {
                    "name": medicine_name,
                    "product_id": product_id,
                    "qty": qty,
                    "price": price,
                    "total_price": price * qty,
                    "available_stock": stock,
                    "requires_prescription": requires_rx,
                    "status": "AVAILABLE",
                }
                in_stock_meds.append(result)
                span.end(output={"status": "available", "result": result})
                print(
                    f"✅ {medicine_name}: Available (Stock: {stock}, Price: ₹{price})"
                )
            else:
                result = {
                    "name": medicine_name,
                    "product_id": product_id,
                    "wanted": qty,
                    "available": stock,
                    "price": price,
                    "requires_prescription": requires_rx,
                    "status": "OUT_OF_STOCK",
                }
                out_of_stock_meds.append(result)
                span.end(output={"status": "out_of_stock", "result": result})
                print(
                    f"❌ {medicine_name}: Out of stock (Wanted: {qty}, Available: {stock})"
                )
        else:
            # Medicine not found in database at all
            result = {
                "name": med_name,
                "wanted": qty,
                "available": 0,
                "price": 0,
                "status": "NOT_FOUND_IN_DATABASE",
            }
            out_of_stock_meds.append(result)
            span.end(output={"status": "not_found", "result": result})
            print(f"❌ {med_name}: Not found in database")

    inventory_checked = len(out_of_stock_meds) == 0

    results = {"in_stock": in_stock_meds, "out_of_stock": out_of_stock_meds}

    print(
        f"📊 Inventory Check: {len(in_stock_meds)} available, {len(out_of_stock_meds)} unavailable"
    )
    
    trace.update(output=results)

    # Update extracted_meds with the REAL names from the database
    # This ensures the rest of the agents (Safety, Output) use the correct names.
    updated_extracted_meds = in_stock_meds + out_of_stock_meds

    return {
        "inventory_checked": inventory_checked,
        "inventory_results": [results],
        "extracted_meds": updated_extracted_meds,
        "steps": ["Agent 4 (Inventory) checked real stock levels"],
        "current_agent": "Agent 4: Inventory",
    }
