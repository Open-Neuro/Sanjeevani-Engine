from typing import TypedDict, Annotated, List, Dict, Any, Optional
import operator
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """
    The state dictionary that is passed between Agents in the LangGraph workflow.
    This maintains the 'Chain of Thought' for observability.
    """

    messages: Annotated[List[BaseMessage], operator.add]
    extracted_meds: List[Dict[str, Any]]
    safety_validated: bool
    prescription_required: bool
    prescription_uploaded: bool
    validation_reasons: List[str]
    inventory_checked: bool
    inventory_results: List[Dict[str, Any]]
    fulfillment_status: Optional[str]
    final_response: Optional[str]
    channel: Optional[str]
    channel_metadata: Optional[Dict[str, Any]]
    steps: Annotated[List[str], operator.add]
    current_agent: Optional[str]
