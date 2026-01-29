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

from .llm import invoke_llm

json_parser = JsonOutputParser(pydantic_object=MeetingDraft)


def build_agent(calendar: MockCalendar):
    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        temperature=0.2,
    )
    default_tz = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")

 
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
    
    async def extract_node(state: AgentState) -> dict:
    
        """
        Take the latest state, invoke LLM to extract necessary fields from the history of messages in the state
        """
    
        #msgs = [SystemMessage(content=EXTRACTION_SYSTEM)] + format_messages(state, [])
    
        draft_m = HumanMessage(content=f"draft: {state.draft.model_dump_json()}")
    
        m = HumanMessage(content=state.messages[-1])
        
        msgs = [SystemMessage(content=EXTRACTION_SYSTEM)] + [draft_m, m]
    
        res = await invoke_llm(llm, msgs)
    
        # Expect JSON
        try:
            data = json_parser.parse(res.content)
            
        except Exception:
            data = {}
    
        draft = state.draft.model_copy(deep=True)
    
        # Merge extracted fields if present
        host = data["host_full_name"]
        attendee = data["attendee_full_name"]
        subject = data["subject"]
        start_time_text = data["start_time_text"]
        duration = data["duration_minutes"]
        tz = data.get("timezone") or draft.timezone or default_tz
    
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
    
    async def ask_missing_node(state: AgentState) -> dict:
    
        """
        Use LLM to format human a understandable question to get missing fields.
        """
    
        m = state.draft.model_dump_json()    
        #msgs = [SystemMessage(content=ASK_MISSING_SYSTEM)] + format_messages(state, [ HumanMessage(content=f"draft: {json.dumps(state.draft)}")])
        msgs = [SystemMessage(content=ASK_MISSING_SYSTEM)] + [ HumanMessage(content=f"draft: {m}")]
        
        res = await invoke_llm(llm, msgs)
        #return {"messages": [HumanMessage(content=f"draft: {json.dumps(draft)}"), res], "status": "ask_human" }
        return {"messages": [res.content], "status": "ask_human" }
    
    
    async def check_availability_node(state: AgentState) -> dict:
        draft = state.draft
        tz = ensure_tz(draft.timezone or default_tz)
    
        start = dt.datetime.fromisoformat(draft.start_time_iso)
        if start.tzinfo is None:
            start = tz.localize(start)
    
        dur = draft.duration_minutes or 30
    
        if state.override or calendar.is_available(draft.attendee_full_name):
            # Confirm booking directly (you can require explicit "yes" if you want)
            event = calendar.book(draft.host_full_name, draft.attendee_full_name, draft.subject, start, dur)
            last_agent_message =  f"Booked: {event['subject']} with {event['attendee_full_name']} " + f"at {event['start_time_iso']} for {event['duration_minutes']} minutes by host {event['host_full_name']}."
        
            #return {"status": "booked", "booked_event": event, "messages": [AIMessage(content=last_agent_message)] }
    
            return {"status": "booked", "booked_event": event, "messages": [last_agent_message] }
    
        # Busy → propose alternatives
        suggestions = calendar.suggest_alternatives(draft.attendee_full_name, start, dur, count=3)
    
        
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
               # "status": "ask_human"}
        
        if last_agent_message is not None:
            #d["messages"] = [AIMessage(content=last_agent_message)]
            d["messages"] = [last_agent_message]
    
        return d
    
    
    async def human_node(state: AgentState) -> dict:
        new_messages = []
        new_messages.append(state.messages[-1])
    
        return {"messages": new_messages}
        
    
    
    async def ask_alternative_node(state: AgentState) -> dict:
    
        """
        Use LLM to format human to get alternative time slits
        """
    
        m = state.draft.model_dump_json()
        s = state.messages[-1]
        #msgs = [SystemMessage(content=ASK_SUGGESTIONS_SYSTEM)] + format_messages(state, [ HumanMessage(content=f"draft: {m}")])
    
        msgs = [SystemMessage(content=ASK_SUGGESTIONS_SYSTEM)] + [HumanMessage(content=f"draft: {m}")] + [s]
        
        res = await invoke_llm(llm, msgs)
        #return {"messages": [HumanMessage(content=f"draft: {m}"), res], "status": "ask_human" }
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
    
    
    
    async def summarize_node(state: AgentState) -> dict:
    
        """
        Use LLM to format human to get alternative time slits
        """
    
        #m = state.draft.model_dump_json()    
        #msgs = [SystemMessage(content=SUMMARIZE_SYSTEM)] + format_messages(state, [ HumanMessage(content=f"draft: {m}")])
        m = [state.messages[-1]]
        msgs = [SystemMessage(content=SUMMARIZE_SYSTEM)] + [ HumanMessage(content=f"draft: {m}")]
        res = await invoke_llm(llm, msgs)
        #return {"messages": [HumanMessage(content=f"draft: {m}"), res]}
        return {"messages": [res.content]}
    
   
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
 
