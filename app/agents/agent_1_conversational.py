import json
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from app.config import settings
from app.agents.state import AgentState
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


def format_system_prompt() -> str:
    return """
    You are the Agent 1: Conversational Intake Pharmacist.
    Your job is to read the raw user input and extract the medicines they want to order.
    
    You MUST output valid JSON ONLY, exactly matching this schema:
    {
        "medicines": [
            {
                "name": "Medicine Name",
                "quantity": 1
            }
        ]
    }
    If the user doesn't specify a quantity, default to 1.
    If no medicines are found, return empty array.
    DO NOT output any conversational text, just the raw JSON.
    """


def conversational_intake_node(state: AgentState) -> AgentState:
    """
    Agent 1: Extracts structured medicine data from raw user messages.
    """
    print("🤖 Agent 1 (Conversational Intake) thinking...")

    # We get the latest user message
    last_message = state["messages"][-1].content

    messages = [
        {"role": "system", "content": format_system_prompt()},
        {"role": "user", "content": last_message},
    ]

    # Track with Langfuse
    response = llm.invoke(
        messages,
        config={"callbacks": [langfuse_handler], "tags": ["agent-1", "conversational-intake"]}
    )

    try:
        # Try to parse the LLM output as JSON
        output_str = response.content.strip()
        # sometimes LLMs wrap JSON in markdown tags
        if output_str.startswith("```json"):
            output_str = output_str[7:-3]
        elif output_str.startswith("```"):
            output_str = output_str[3:-3]

        data = json.loads(output_str)
        extracted = data.get("medicines", [])
        return {
            "extracted_meds": extracted,
            "steps": ["Agent 1 (Conversational Intake) extracted medicine request"],
            "current_agent": "Agent 1: Intake",
        }
    except Exception as e:
        print(f"Agent 1 Extraction Failed: {e}")
        return {"extracted_meds": []}
