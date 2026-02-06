from __future__ import annotations
import os
from typing import Dict, Any, List, Optional
import datetime as dt
import pytz

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END

from .schemas import AgentState, MeetingDraft, SlotSuggestion
from .prompts import EXTRACTION_SYSTEM, DIALOG_SYSTEM, ASK_MISSING_SYSTEM, ASK_SUGGESTIONS_SYSTEM, SUMMARIZE_SYSTEM
from .utils import parse_natural_datetime, now_in_tz, to_iso, ensure_tz, parse_user_date
from .calendar_mock import MockCalendar
from langchain_core.output_parsers import JsonOutputParser
from langgraph.checkpoint.memory import MemorySaver

from .llm import invoke_llm

def missing_fields(draft: MeetingDraft) -> List[str]:
    missing = []
    if not draft.host_full_name:
        missing.append("host_full_name")
    if not draft.attendee_full_name:
        missing.append("attendee_full_name")
    if not draft.subject:
        missing.append("subject")
    if not draft.start_time_iso:
        missing.append("start_time")
    return missing

async def extract_node(state: AgentState, runtime: Runtime[RuntimeContext]) -> dict:

    """
    Take the latest state, invoke LLM to extract necessary fields from the history of messages in the state
    """

    draft_m = HumanMessage(content=f"draft: {state.draft.model_dump_json()}")
    m = HumanMessage(content=state.messages[-1])
    msgs = [SystemMessage(content=EXTRACTION_SYSTEM)] + [draft_m, m]
    res = await invoke_llm(runtime.context.llm, msgs)

    # Expect JSON
    try:
        data = runtime.context.json_parser.parse(res.content)
        
    except Exception:
        data = {}

    draft = state.draft.model_copy(deep=True)

    # Merge extracted fields if present
    host = data["host_full_name"]
    attendee = data["attendee_full_name"]
    subject = data["subject"]
    start_time_text = data["start_time_text"]
    duration = data["duration_minutes"]
    tz = data.get("timezone") or draft.timezone or runtime.context.default_tz

    if host and not draft.host_full_name:
        draft.host_full_name = host
    if attendee and not draft.attendee_full_name:
        draft.attendee_full_name = attendee
    if subject and not draft.subject:
        draft.subject = subject
    if isinstance(duration, int) and duration > 0:
        draft.duration_minutes = duration
    draft.timezone = tz

    # Parse start time if provided
    if start_time_text:
        parsed = parse_user_date(start_time_text, None)
        if parsed:
            draft.start_time_iso = parsed.isoformat()

    return {"draft": draft }

async def ask_missing_node(state: AgentState, runtime: Runtime[RuntimeContext]) -> dict:

    """
    Use LLM to format human a understandable question to get missing fields.
    """

    m = state.draft.model_dump_json()
    msgs = [SystemMessage(content=ASK_MISSING_SYSTEM)] + [ HumanMessage(content=f"draft: {m}")]
    
    res = await invoke_llm(runtime.context.llm, msgs)
    return {"messages": [res.content], "status": "ask_human" }


async def check_availability_node(state: AgentState, runtime: Runtime[RuntimeContext]) -> dict:
    draft = state.draft
    tz = ensure_tz(draft.timezone or runtime.context.default_tz)

    start = dt.datetime.fromisoformat(draft.start_time_iso)
    if start.tzinfo is None:
        start = tz.localize(start)

    dur = draft.duration_minutes or 30

    if state.override or runtime.context.calendar.is_available(draft.attendee_full_name):
        # Confirm booking directly (you can require explicit "yes" if you want)
        event = runtime.context.calendar.book(draft.host_full_name, draft.attendee_full_name, draft.subject, start, dur)
        last_agent_message =  f"Booked: {event['subject']} with {event['attendee_full_name']} " + f"at {event['start_time_iso']} for {event['duration_minutes']} minutes by host {event['host_full_name']}."
    
        return {"status": "booked", "booked_event": event, "messages": [last_agent_message] }

    # Busy → propose alternatives
    suggestions = runtime.context.calendar.suggest_alternatives(draft.attendee_full_name, start, dur, count=3)

    
    # Build human-friendly suggestions
    nice = []
    last_agent_message = None
    
    for s in suggestions:
        dt_obj = s[0].astimezone(tz)
        nice.append(dt_obj.strftime("%a, %b %d at %-I:%M %p"))
    if nice:
        last_agent_message = (
            f"The attendee {draft.attendee_full_name} is busy then. Ask {draft.host_full_name}' to choose from these alternative time slots: "
            + "; ".join(nice)
            + " ?"
        )
    else:
        last_agent_message = (
            f"The attendee {draft.attendee_full_name} is busy then, and I couldn't find an open slot soon. "
            "What other times should I try?"
        )

    d =  {"override" : True, 
          "suggestions": [SlotSuggestion(start_time_iso=s[0].isoformat(), duration_minutes=s[1]) for s in suggestions] }
    
    if last_agent_message is not None:
        d["messages"] = [last_agent_message]

    return d


async def human_node(state: AgentState) -> dict:
    new_messages = []
    new_messages.append(state.messages[-1])

    return {"messages": new_messages}


async def ask_alternative_node(state: AgentState, runtime: Runtime[RuntimeContext]) -> dict:

    """
    Use LLM to format human to get alternative time slits
    """

    m = state.draft.model_dump_json()
    s = state.messages[-1]
    msgs = [SystemMessage(content=ASK_SUGGESTIONS_SYSTEM)] + [HumanMessage(content=f"draft: {m}")] + [s]
    
    res = await invoke_llm(runtime.context.llm, msgs)
    return {"messages": [res.content], "status": "ask_human" }


async def extract_decide_next_node(state: AgentState) -> str:
    miss = missing_fields(state.draft)
    if miss:
        return "ask_missing"
    return "check_availability"

async def availability_decide_next_node(state: AgentState) -> str:
    if state.status == "booked":
        return "summarize"
    else:
        return "ask_alternative"


async def summarize_node(state: AgentState, runtime: Runtime[RuntimeContext]) -> dict:

    """
    Use LLM to format human to get alternative time slits
    """

    m = [state.messages[-1]]
    msgs = [SystemMessage(content=SUMMARIZE_SYSTEM)] + [ HumanMessage(content=f"draft: {m}")]
    res = await invoke_llm(runtime.context.llm, msgs)
    return {"messages": [res.content]}


def create_revivable_graph():

    g = StateGraph(AgentState)
    g.add_node("extract", extract_node)
    g.add_node("ask_missing", ask_missing_node)
    g.add_node("check_availability", check_availability_node)
    g.add_node("ask_alternative", ask_alternative_node)

    g.add_node("summarize", summarize_node)

    g.set_entry_point("extract")


    g.add_conditional_edges("extract", extract_decide_next_node, {
        "ask_missing": "ask_missing",
        "check_availability": "check_availability",
    })

    g.add_edge("ask_missing", END)

    g.add_conditional_edges("check_availability", availability_decide_next_node, {
        "summarize": "summarize",
        "ask_alternative" : "ask_alternative"});

    g.add_edge("ask_alternative", END)

    g.add_edge("summarize", END)

    workflow = g.compile()

    return workflow

def create_human_in_loop_graph():

    g = StateGraph(AgentState)
    g.add_node("extract", extract_node)
    g.add_node("ask_missing", ask_missing_node)
    g.add_node("check_availability", check_availability_node)
    g.add_node("ask_alternative", ask_alternative_node)
    g.add_node("human", human_node)
    g.add_node("summarize", summarize_node)
  
    g.set_entry_point("extract")

    g.add_conditional_edges("extract", extract_decide_next_node, {
        "ask_missing": "ask_missing",
        "check_availability": "check_availability",
    })

    g.add_edge("ask_missing", "human")
    g.add_edge("human", "extract")
 
    g.add_conditional_edges("check_availability", availability_decide_next_node, {
        "summarize": "summarize",
        "ask_alternative" : "ask_alternative"});

    g.add_edge("ask_alternative", "human")

    g.add_edge("summarize", END)

    workflow = g.compile(checkpointer=MemorySaver(), interrupt_before=["human"])

    return workflow
