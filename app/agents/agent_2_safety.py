import json
from langchain_groq import ChatGroq
from app.config import settings
from app.agents.state import AgentState
from app.database.mongo_client import get_db
import re
from langfuse.callback import CallbackHandler

# Langfuse tracking
langfuse_handler = CallbackHandler(
    secret_key=settings.LANGFUSE_SECRET_KEY,
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    host=settings.LANGFUSE_HOST
)

# Model initialization
llm = ChatGroq(
    api_key=settings.GROQ_API_KEY, model_name=settings.GROQ_MODEL, temperature=0.1
)


def check_prescription_in_database(medicine_name: str) -> tuple[bool, str]:
    """
    Check if medicine requires prescription by looking in database first.
    Returns: (requires_prescription, actual_medicine_name)
    """
    db = get_db()

    # Try multiple search strategies (same as Agent 4)
    inventory_item = None

    # Strategy 1: Exact match
    regex_pattern = re.compile(f"^{re.escape(medicine_name)}$", re.IGNORECASE)
    inventory_item = db["inventory"].find_one(
        {
            "$or": [
                {"product_id": {"$regex": regex_pattern}},
                {"medicine_name": {"$regex": regex_pattern}},
            ]
        }
    )

    # Strategy 2: Starts with
    if not inventory_item:
        regex_pattern = re.compile(f"^{re.escape(medicine_name)}", re.IGNORECASE)
        inventory_item = db["inventory"].find_one(
            {
                "$or": [
                    {"product_id": {"$regex": regex_pattern}},
                    {"medicine_name": {"$regex": regex_pattern}},
                ]
            }
        )

    # Strategy 3: Contains
    if not inventory_item:
        regex_pattern = re.compile(f".*{re.escape(medicine_name)}.*", re.IGNORECASE)
        inventory_item = db["inventory"].find_one(
            {
                "$or": [
                    {"product_id": {"$regex": regex_pattern}},
                    {"medicine_name": {"$regex": regex_pattern}},
                ]
            }
        )

    if inventory_item:
        actual_name = inventory_item.get("medicine_name", medicine_name)
        requires_rx = inventory_item.get("requires_prescription", "No")

        # Check if requires prescription (Yes/Y/True/1)
        needs_rx = str(requires_rx).strip().lower() in ("yes", "y", "true", "1")

        return needs_rx, actual_name

    # Not found in database - use LLM as fallback
    return None, medicine_name


def format_safety_system_prompt() -> str:
    return """
    You are Agent 2: Safety & Compliance Verifier Pharmacist.
    Your job is to determine if medicines require a prescription based on general medical knowledge.
    
    CRITICAL RULES:
    1. Common OTC medicines DO NOT need prescription:
       - Paracetamol, Acetaminophen, Tylenol
       - Ibuprofen, Aspirin
       - Cetirizine, Loratadine (antihistamines)
       - Omeprazole, Pantoprazole (antacids)
       - Vitamin supplements
       - Cough syrups (non-codeine)
    
    2. These REQUIRE prescription:
       - Antibiotics (Amoxicillin, Azithromycin, Ciprofloxacin)
       - Diabetes medicines (Metformin, Insulin)
       - Blood pressure medicines (Amlodipine, Atenolol)
       - Cholesterol medicines (Atorvastatin, Simvastatin)
       - Hormones and steroids
       - Controlled substances
    
    You MUST output valid JSON ONLY:
    {
        "safety_validated": boolean,
        "validation_reasons": ["List of medicines that need prescription"]
    }
    
    Set "safety_validated" to false if ANY medicine requires prescription.
    DO NOT output any conversational text, just raw JSON.
    """


def safety_verifier_node(state: AgentState) -> AgentState:
    """
    Agent 2: Safety & Policy Enforcement.
    First checks database for prescription requirements, then uses LLM as fallback.
    """
    print("🛡️ Agent 2 (Safety & Compliance Verifier) checking policies...")

    extracted_meds = state.get("extracted_meds", [])
    if not extracted_meds:
        return {
            "safety_validated": True,
            "validation_reasons": [],
        }

    new_extracted_meds = []
    prescription_required_any = False
    medicines_needing_rx = []

    # Check each medicine in database first
    for med in extracted_meds:
        med_copy = med.copy()
        med_name = med.get("name", "Unknown")

        print(f"  🔍 Checking: {med_name}")

        # Check database first
        needs_rx_db, actual_name = check_prescription_in_database(med_name)

        if needs_rx_db is not None:
            # Found in database
            if needs_rx_db:
                medicines_needing_rx.append(
                    f"{actual_name} requires a doctor's prescription"
                )
                med_copy["requires_prescription"] = "Yes"
                prescription_required_any = True
                print(f"    ⚠️ Prescription required (from database)")
            else:
                med_copy["requires_prescription"] = "No"
                print(f"    ✅ No prescription needed (from database)")
        else:
            # Not in database - use LLM as fallback
            print(f"    🤖 Not in database, checking with LLM...")

            messages = [
                {"role": "system", "content": format_safety_system_prompt()},
                {
                    "role": "user",
                    "content": f"Check if this medicine needs prescription: {med_name}. Answer ONLY with the JSON format requested.",
                },
            ]

            try:
                response = llm.invoke(
                    messages,
                    config={"callbacks": [langfuse_handler], "tags": ["agent-2", "safety-verifier"]}
                )
                output_str = response.content.strip()

                if output_str.startswith("```json"):
                    output_str = output_str[7:-3]
                elif output_str.startswith("```"):
                    output_str = output_str[3:-3]

                data = json.loads(output_str)

                if not data.get("safety_validated", True):
                    reasons = data.get("validation_reasons", [])
                    medicines_needing_rx.extend(reasons)
                    med_copy["requires_prescription"] = "Yes"
                    prescription_required_any = True
                    print(f"    ⚠️ Prescription required (from LLM)")
                else:
                    med_copy["requires_prescription"] = "No"
                    print(f"    ✅ No prescription needed (from LLM)")

            except Exception as e:
                print(f"    ❌ LLM check failed: {e}")
                # On error, be conservative and require prescription
                medicines_needing_rx.append(f"{med_name} - Unable to verify safety")
                med_copy["requires_prescription"] = "Yes"
                prescription_required_any = True

        new_extracted_meds.append(med_copy)

    # Final decision
    is_already_uploaded = state.get("prescription_uploaded", False)

    if medicines_needing_rx:
        print(f"  ⚠️ Prescription required for: {len(medicines_needing_rx)} medicine(s)")

        # If already uploaded, we mark as validated but keep the reasons for record
        safety_passed = is_already_uploaded

        return {
            "extracted_meds": new_extracted_meds,
            "safety_validated": safety_passed,
            "prescription_required": True,
            "validation_reasons": medicines_needing_rx,
            "steps": [
                (
                    "Agent 2 (Safety Verifier) flagged prescription requirement, and validated based on upload status"
                    if is_already_uploaded
                    else "Agent 2 (Safety Verifier) flagged prescription requirement"
                )
            ],
            "current_agent": "Agent 2: Safety",
        }
    else:
        print(f"  ✅ All medicines are safe (no prescription needed)")
        return {
            "extracted_meds": new_extracted_meds,
            "safety_validated": True,
            "prescription_required": False,
            "validation_reasons": [],
            "steps": ["Agent 2 (Safety Verifier) validated all medicines"],
            "current_agent": "Agent 2: Safety",
        }
