from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Optional, List, Literal, Dict, Any


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Client-provided stable session id")
    message: str = Field(..., description="User message")


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    state: Dict[str, Any]


class MeetingDraft(BaseModel):
    attendee_full_name: Optional[str] = None
    subject: Optional[str] = None
    start_time_iso: Optional[str] = None  # ISO string in local tz or with offset
    duration_minutes: Optional[int] = 30
    timezone: Optional[str] = None


class SlotSuggestion(BaseModel):
    start_time_iso: str
    duration_minutes: int


class AgentState(BaseModel):
    # Conversation
    last_user_message: str = ""
    last_agent_message: str = ""

    # Draft meeting details
    draft: MeetingDraft = Field(default_factory=MeetingDraft)

    # Decisioning
    status: Literal[
        "collecting_info",
        "checking_availability",
        "proposing_alternatives",
        "confirming",
        "booked",
    ] = "collecting_info"

    # Suggestions / results
    suggestions: List[SlotSuggestion] = Field(default_factory=list)
    booked_event: Optional[dict] = None
    override : bool = False;

