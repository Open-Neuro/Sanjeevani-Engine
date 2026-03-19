import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from groq import Groq

# Import langchain_core safely
try:
    from langchain_core.messages import HumanMessage
except ImportError:
    print("⚠️ langchain_core not found. AI chat endpoint will be limited.")
    HumanMessage = None  # type: ignore

# Import Langfuse callback handler safely
try:
    from langfuse.callback import CallbackHandler as LangfuseCallbackHandler
except (ImportError, ModuleNotFoundError):
    print("⚠️ Langfuse callback handler not found. Tracing will be disabled.")
    LangfuseCallbackHandler = None

from app.agents.agent_6_orchestrator import orchestrator
from app.agents.agent_3_refill import ProactiveRefillAgent

router = APIRouter(prefix="/agents", tags=["Agentic AI Framework"])


class ChatRequest(BaseModel):
    message: str
    user_id: str = "guest_user"


@router.post("/groq-chat", summary="Groq-powered Chatbot")
async def groq_chat(req: ChatRequest):
    try:
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": req.message,
                }
            ],
            model="llama3-8b-8192",  # You can choose a different Groq model here
        )
        return {
            "status": "success",
            "reply": chat_completion.choices[0].message.content,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat", summary="Conversational AI Doctor/Pharmacist")
async def chat_with_agent(req: ChatRequest):

    if HumanMessage is None:
        raise HTTPException(
            status_code=503,
            detail="AI chat is unavailable: langchain is not installed. Run: pip install langchain langchain-core",
        )

    callbacks = []
    if (
        LangfuseCallbackHandler
        and os.getenv("LANGFUSE_SECRET_KEY")
        and os.getenv("LANGFUSE_PUBLIC_KEY")
    ):
        langfuse_handler = LangfuseCallbackHandler(
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
            session_id=req.user_id,
        )
        callbacks.append(langfuse_handler)
        print("🔗 Tracing Agent interaction to Langfuse")

    # This must be a LangChain message (NOT langfuse handler)
    messages = [HumanMessage(content=req.message)]

    initial_state = {
        "messages": messages,
        "extracted_meds": [],
        "safety_validated": False,
        "validation_reasons": [],
        "inventory_checked": False,
        "inventory_results": [],
        "fulfillment_status": None,
        "final_response": None,
        "channel_metadata": {"user_id": req.user_id},
        "steps": [],
        "current_agent": None,
    }

    try:
        result = orchestrator.invoke(initial_state, config={"callbacks": callbacks})

        response_text = (
            result.get("final_response") or "Sorry, I couldn't understand that request."
        )

        return {
            "status": "success",
            "reply": response_text,
            "chain_of_thought": {
                "extracted": result.get("extracted_meds"),
                "safety_passed": result.get("safety_validated"),
                "safety_reasons": result.get("validation_reasons"),
                "inventory_passed": result.get("inventory_checked"),
            },
            "agent_steps": result.get("steps", []),
        }
    except Exception as e:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger-proactive-refills", summary="Trigger Agent 3 Refill Scan")
async def trigger_refills():
    agent = ProactiveRefillAgent()
    alerts = agent.scan_patients_for_refills()

    return {
        "status": "success",
        "alerts_generated": len(alerts),
        "notifications": alerts,
    }
