import logging
import json
from langgraph.graph import StateGraph, END
from langchain_groq import ChatGroq
from langchain_anthropic import ChatAnthropic
from app.config import settings
from app.agents.state import AgentState
from app.agents.agent_1_conversational import conversational_intake_node
from app.agents.agent_2_safety import safety_verifier_node
from app.agents.agent_4_inventory import inventory_librarian_node
from app.agents.agent_5_fulfillment import fulfillment_dispatcher_node
from langfuse.callback import CallbackHandler

logger = logging.getLogger(__name__)

# Langfuse tracking
langfuse_handler = CallbackHandler(
    secret_key=settings.LANGFUSE_SECRET_KEY,
    public_key=settings.LANGFUSE_PUBLIC_KEY,
    host=settings.LANGFUSE_HOST
)

# Model initialization for final response synthesis (Groq for speed as requested)
llm = ChatGroq(
    api_key=settings.GROQ_API_KEY, model_name=settings.GROQ_MODEL, temperature=0.7
)


# The final node structures an AI response using Claude to be conversational and professional
def output_synthesizer_node(state: AgentState) -> AgentState:
    print("🧠 Agent 6 (Supervisor) synthesizing final response via Groq...")

    channel = state.get("channel", "WhatsApp")
    channel_metadata = state.get("channel_metadata") or {}
    preferred_language = channel_metadata.get("preferred_language", "en")
    preferred_script = channel_metadata.get("preferred_script", "latin")
    language_instruction = (
        "Respond in natural Hindi (Devanagari script)."
        if preferred_language == "hi" and preferred_script == "devanagari"
        else "Respond in natural Hinglish (Hindi in Latin script)."
        if preferred_language == "hi"
        else "Respond in English."
    )

    # Prepare context for synthesis
    context = {
        "safety_validated": state.get("safety_validated"),
        "prescription_required": state.get("prescription_required"),
        "validation_reasons": state.get("validation_reasons"),
        "inventory_checked": state.get("inventory_checked"),
        "inventory_results": state.get("inventory_results"),
        "fulfillment_status": state.get("fulfillment_status"),
        "extracted_meds": state.get("extracted_meds"),
    }

    if channel == "Voice Call":
        system_prompt = f"""
        You are SANJEEVANI, the Voice AI Pharmacist. Your response MUST be extremely brief and spoken (no markdown, no bold).
        LANGUAGE: {language_instruction}
        
        GOAL: Confirm and close.
        1. IF SUCCESS: "Order confirmed! [Medicine Name] will be ready soon. Thank you for calling SanjeevaniRxAI. Goodbye!"
        2. IF OUT OF STOCK: "I'm sorry, [Medicine Name] is out of stock. Please try again later. Goodbye!"
        3. IF PRESCRIPTION NEEDED: "[Medicine Name] requires a prescription. Please visit us at the store with your doctor's note. Goodbye!"
        4. IF ERRORS: "I'm sorry, I couldn't find that item. Please try again. Goodbye!"
        
        NEVER ask "Is that okay?" or "How many?". Assume the default quantity of 1 if not specified.
        """
    else:
        system_prompt = f"""
        You are Sanjeevani, the Executive Supervisor of SanjeevaniRxAI Intelligence. 
        Your goal is to provide a helpful, professional, and friendly response to the pharmacy customer.
        LANGUAGE: {language_instruction}
        
        CRITICAL OPERATIONAL RULES:
        1. FOR SUCCESSFUL ORDERS: List actual medicine names, prices (₹), and the total clearly. Confirm the order is placed.
        2. FOR MISSING DATA: If an item is not in our database, apologize and say it's unavailable.
        3. ⚠️ PRESCRIPTION REQUIRED ⚠️: If 'prescription_required' is true and 'safety_validated' is false:
           - Start with: "**⚠️ PRESCRIPTION REQUIRED ⚠️**"
           - State which medicine needs prescription (e.g., "Metformin requires a valid doctor's prescription")
           - Then say EXACTLY: "📸 **Please upload your prescription photo here** and I'll verify it for you!"
           - Keep it SHORT and CLEAR - no long explanations
           - Stop other order processing for those items.
        4. OUT OF STOCK: Notify if unavailable and suggest waiting or alternatives.
        5. TONE: Warm, expert, and reassuring. Use relevant emojis. Keep responses concise.
        """

    user_message = f"Internal State: {json.dumps(context)}\nLatest user message: {state['messages'][-1].content}"

    try:
        response = llm.invoke(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            config={"callbacks": [langfuse_handler], "tags": ["agent-6", "orchestrator", "output-synthesizer"]}
        )
        return {
            "final_response": response.content,
            "channel": channel,
            "channel_metadata": channel_metadata,
            "steps": ["Agent 6 (Supervisor) synthesized final customer response"],
            "current_agent": "Agent 6: Supervisor",
        }
    except Exception as e:
        logger.error(f"Agent 6 synthesis failed: {e}")
        # Fallback to manual formatting if LLM fails
        return {
            "final_response": "I'm sorry, I'm having trouble finalizing your request. Please try again or contact support.",
            "channel": channel,
            "channel_metadata": channel_metadata,
            "steps": ["Agent 6 (Supervisor) encountered error but provided fallback"],
            "current_agent": "Agent 6: Supervisor",
        }


# State Routing functions
def route_after_intake(state: AgentState) -> str:
    if len(state.get("extracted_meds", [])) == 0:
        # User didn't ask for medicines, end the process early and reply conversationally.
        return "output"
    return "inventory"


def route_after_inventory(state: AgentState) -> str:
    # Always check safety after inventory (even if items are out of stock, we want to know if they need an Rx)
    return "safety"


def route_after_safety(state: AgentState) -> str:
    # Only proceed to fulfillment if everything is valid and available
    is_safe = state.get("safety_validated", False)
    is_available = state.get("inventory_checked", False)

    if is_safe and is_available:
        return "fulfillment"
    return "output"


def build_orchestrator_graph():
    """
    Agent 6: The Orchestrator setup.
    Compiles the Chain of Thought LangGraph using our specialized expert agents.
    Provides complete state step-by-step observability to Langfuse.
    """

    builder = StateGraph(AgentState)

    # Add all Agent Nodes
    builder.add_node("intake", conversational_intake_node)
    builder.add_node("safety", safety_verifier_node)
    builder.add_node("inventory", inventory_librarian_node)
    builder.add_node("fulfillment", fulfillment_dispatcher_node)
    builder.add_node("output", output_synthesizer_node)

    # Define Entry
    builder.set_entry_point("intake")

    # Define edges and conditional routing
    builder.add_conditional_edges("intake", route_after_intake)
    builder.add_conditional_edges("inventory", route_after_inventory)
    builder.add_conditional_edges("safety", route_after_safety)

    # Fulfillment always leads to formatting the output success message
    builder.add_edge("fulfillment", "output")

    # End
    builder.add_edge("output", END)

    graph = builder.compile()
    return graph


# Expose compiled instance
orchestrator = build_orchestrator_graph()
