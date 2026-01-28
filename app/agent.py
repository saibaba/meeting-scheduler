from __future__ import annotations
import os
from typing import Dict, Any, List, Optional
import datetime as dt
import pytz

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END

from .schemas import AgentState, MeetingDraft, SlotSuggestion
from .prompts import EXTRACTION_SYSTEM, DIALOG_SYSTEM
from .utils import parse_natural_datetime, now_in_tz, to_iso, ensure_tz, parse_user_date
from .calendar_mock import MockCalendar
from langchain_core.output_parsers import JsonOutputParser

from .llm import invoke_llm

json_parser = JsonOutputParser(pydantic_object=MeetingDraft)


def build_agent(calendar: MockCalendar):
    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        temperature=0.2,
    )
    default_tz = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")


    async def extract_node(state: AgentState) -> AgentState:
        user_text = state.last_user_message
    
        msg = [
            SystemMessage(content=EXTRACTION_SYSTEM),
            HumanMessage(content=user_text),
        ]
        res = await invoke_llm(llm, msg)
        # Expect JSON
        try:
            data = json_parser.parse(res.content) #json.loads(res.content)
            
        except Exception:
            data = {}
    
        draft = state.draft.model_copy(deep=True)
    
        # Merge extracted fields if present
        attendee = data["attendee_full_name"]
        subject = data["subject"]
        start_time_text = data["start_time_text"]
        duration = data["duration_minutes"]
        tz = data.get("timezone") or draft.timezone or default_tz
    
        if attendee and not draft.attendee_full_name:
            draft.attendee_full_name = attendee
        if subject and not draft.subject:
            draft.subject = subject
        if isinstance(duration, int) and duration > 0:
            draft.duration_minutes = duration
        draft.timezone = tz
    
        # Parse start time if provided
        if start_time_text:
            parsed = parse_user_date(start_time_text, None) # parse_natural_datetime(start_time_text, tz)
            if parsed:
                draft.start_time_iso = parsed.isoformat()
    
        state.draft = draft
        return state
    
    def missing_fields(draft: MeetingDraft) -> List[str]:
        missing = []
        if not draft.attendee_full_name:
            missing.append("attendee_full_name")
        if not draft.subject:
            missing.append("subject")
        if not draft.start_time_iso:
            missing.append("start_time")
        return missing
    
    async def ask_missing_node(state: AgentState) -> AgentState:
        draft = state.draft
        miss = missing_fields(draft)
    
        # Craft a very targeted question without extra fluff
        questions = []
        if "attendee_full_name" in miss:
            questions.append("Who is the meeting with? (full name)")
        if "subject" in miss:
            questions.append("What’s the meeting about? (subject)")
        if "start_time" in miss:
            questions.append("When should it happen? (date + time)")
    
        reply = " ".join(questions)
        state.last_agent_message = reply
        return state
    
    
    async def check_availability_node(state: AgentState) -> AgentState:
        draft = state.draft
        tz = ensure_tz(draft.timezone or default_tz)
    
        start = dt.datetime.fromisoformat(draft.start_time_iso)
        if start.tzinfo is None:
            start = tz.localize(start)
    
        dur = draft.duration_minutes or 30
    
        if state.override or calendar.is_available(draft.attendee_full_name):
            state.status = "confirming"
            state.suggestions = []
            # Confirm booking directly (you can require explicit "yes" if you want)
            event = calendar.book(draft.attendee_full_name, draft.subject, start, dur)
            state.booked_event = event
            state.status = "booked"
            state.last_agent_message = (
                f"Booked: {event['subject']} with {event['attendee_full_name']} "
                f"at {event['start_time_iso']} for {event['duration_minutes']} minutes."
            )
            return state
    
        # Busy → propose alternatives
        suggestions = calendar.suggest_alternatives(draft.attendee_full_name, start, dur, count=3)
        state.suggestions = [
            SlotSuggestion(start_time_iso=s[0].isoformat(), duration_minutes=s[1]) for s in suggestions
        ]
        state.status = "proposing_alternatives"
        state.override = True
    
        # Build human-friendly suggestions
        nice = []
        for s in suggestions:
            dt_obj = s[0].astimezone(tz)
            nice.append(dt_obj.strftime("%a, %b %d at %-I:%M %p"))
        if nice:
            state.last_agent_message = (
                f"{draft.attendee_full_name} is busy then. How about: "
                + "; ".join(nice)
                + " ?"
            )
        else:
            state.last_agent_message = (
                f"{draft.attendee_full_name} is busy then, and I couldn't find an open slot soon. "
                "What other times should I try?"
            )
        return state
    
    
    async def decide_next_node(state: AgentState) -> str:
        miss = missing_fields(state.draft)
        if miss:
            state.status = "collecting_info"
            return "ask_missing"
        state.status = "checking_availability"
        return "check_availability"
    
    

    # Graph wiring
    g = StateGraph(AgentState)
    g.add_node("extract", extract_node)
    g.add_node("ask_missing", ask_missing_node)
    g.add_node("check_availability", check_availability_node)

    g.set_entry_point("extract")
    g.add_conditional_edges("extract", decide_next_node, {
        "ask_missing": "ask_missing",
        "check_availability": "check_availability",
    })
    g.add_edge("ask_missing", END)
    g.add_edge("check_availability", END)

    return g.compile()

