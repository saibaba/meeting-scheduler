from __future__ import annotations
import os
from typing import Dict, Any, List, Optional
import datetime as dt
import pytz

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END

from .schemas import AgentState, MeetingDraft, SlotSuggestion
from .prompts import EXTRACTION_SYSTEM, DIALOG_SYSTEM, ASK_MISSING_SYSTEM, ASK_SUGGESTIONS_SYSTEM, SUMMARIZE_SYSTEM, PLANNER_SYSTEM, SUMMARIZE_REQUEST
from .utils import parse_natural_datetime, now_in_tz, to_iso, ensure_tz, parse_user_date
from .calendar_mock import MockCalendar
from langchain_core.output_parsers import JsonOutputParser
from langgraph.checkpoint.memory import MemorySaver

from .llm import invoke_llm

json_parser = JsonOutputParser(pydantic_object=MeetingDraft)


default_tz = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles")

async def human_node(state: AgentState) -> dict:
    new_messages = []
    new_messages.append(state.messages[-1])
    return {"messages": new_messages}

def build_input_agent(llm, memory, config):

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
   
        print("extract_node called") 
    
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
    
    
    async def summarize_request_node(state: AgentState) -> dict:

        """
        Use LLM to format human to get alternative time slits
        """

        s = state.draft.model_dump_json()
        msgs = [SystemMessage(content=SUMMARIZE_REQUEST)] + [HumanMessage(content=s)]
        res = await invoke_llm(llm, msgs)
        return {"messages": [res.content]}

    async def extract_decide_next_node(state: AgentState) -> str:
        miss = missing_fields(state.draft)
        if miss:
            return "ask_missing"
        return "summarize_request"
    
    def create_graph():

        input_graph = StateGraph(AgentState)
        input_graph.add_node("extract", extract_node)
        input_graph.add_node("ask_missing", ask_missing_node)
        input_graph.add_node("human", human_node)
        input_graph.add_node("summarize_request", summarize_request_node)

        input_graph.add_conditional_edges("extract", extract_decide_next_node, {
            "ask_missing": "ask_missing",
            "summarize_request": "summarize_request",
        })

        input_graph.add_edge("ask_missing", "human")
        input_graph.add_edge("human", "extract")

        input_graph.set_entry_point("extract")

        input_graph.add_edge("summarize_request", END)

        input_workflow = input_graph.compile(checkpointer=memory, interrupt_before=["human"])

        return input_workflow

    return create_graph()

def build_booking_agent(llm, calendar, memory, config):

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
        
        if last_agent_message is not None:
            d["messages"] = [last_agent_message]
    
        return d
    
    async def ask_alternative_node(state: AgentState) -> dict:
    
        """
        Use LLM to format human to get alternative time slits
        """
    
        m = state.draft.model_dump_json()
        s = state.messages[-1]
    
        msgs = [SystemMessage(content=ASK_SUGGESTIONS_SYSTEM)] + [HumanMessage(content=f"draft: {m}")] + [s]
        
        res = await invoke_llm(llm, msgs)
        return {"messages": [res.content], "status": "ask_human" }
    
    
    async def availability_decide_next_node(state: AgentState) -> str:
        if state.status == "booked":
            return "summarize"
        else:
            return "ask_alternative"

    
    async def summarize_node(state: AgentState) -> dict:
    
        """
        Use LLM to format human to get alternative time slits
        """
    
        m = [state.messages[-1]]
        msgs = [SystemMessage(content=SUMMARIZE_SYSTEM)] + [ HumanMessage(content=f"draft: {m}")]
        res = await invoke_llm(llm, msgs)
        return {"messages": [res.content]}
    
    
    def create_graph():

        booking_graph = StateGraph(AgentState)

        booking_graph.add_node("check_availability", check_availability_node)
        booking_graph.add_node("ask_alternative", ask_alternative_node)
        booking_graph.add_node("human", human_node)
        booking_graph.add_node("summarize", summarize_node)

        booking_graph.add_edge("ask_alternative", "human")
        booking_graph.add_edge("human", "check_availability")

        booking_graph.add_conditional_edges("check_availability", availability_decide_next_node, {
            "summarize": "summarize",
            "ask_alternative" : "ask_alternative"});


        booking_graph.add_edge("summarize", END)

        booking_graph.set_entry_point("check_availability")

        booking_workflow = booking_graph.compile(checkpointer=memory, interrupt_before=["human"])

        return booking_workflow

    return create_graph()



def build_planner_agent(llm, memory, input_workflow, booking_workflow, config):


    async def input_agent(state: AgentState)->dict:
        if state.status == "ask_human":
            print("Input agent continuing flow with state ", state)
            input_workflow.update_state(config, {"messages": state.messages})        
            return await input_workflow.ainvoke(None, config)

        print("invoking input_workflow first time in the dialog")
        return await input_workflow.ainvoke(state, config)

    async def booking_agent(state: AgentState)->dict:
        if state.status == "ask_human":
            print("Booking agent continuing flow...")
            booking_workflow.update_state(config, {"messages": state.messages})                
            return await booking_workflow.ainvoke(None, config)
        return await booking_workflow.ainvoke(state, config)

    async def done_node(state: AgentState)->dict:
        return state

    async def planner_decide_next_node(state: AgentState) -> str:
        if state.turns == 0:
            return "done"
        
        if state.planner_status == "done":
            return "done"
        else:
            return "invoke_agent"

    async def invoke_agent_node(state: AgentState) -> dict:
    
        ret = {}
        snapshot = None
        next_value = None
        m = state.messages[-1]
    
        if state.agent_name == "input_agent":
            ret =  await input_agent(state)
            snapshot = input_workflow.get_state(config)
            next_value = snapshot.next  
            if not next_value:
                m = "\ninput_agent completed with " + ret["messages"][-1] + ", decide next step."
                ret["planner_status"] = "planner"
                ret["status"] = "checking_availability"
            else:
                m = ret["messages"][-1]
        if state.agent_name == "booking_agent":
            ret =  await booking_agent(state)
            snapshot = booking_workflow.get_state(config)
            next_value = snapshot.next    
        
            if not next_value:
                m = "\nbooking_agent completed with " +  ret["messages"][-1]  + ", decide next step."
                ret["planner_status"] = "planner"
            else:
                m = ret["messages"][-1]

        print("Agent invoke ", state.agent_name, " next: ", next_value, "; response ", ret)

        ret["messages"] = [m]
    
        return ret

    async def planning_node(state: AgentState) -> dict:


        m = state.messages[-1]

        ### latest added
        if state.status == "ask_human":
            return {"messages": [m], "planner_status": "invoke_agent", "agent_name": state.agent_name}
        
        msgs = [SystemMessage(content=PLANNER_SYSTEM)] + [ HumanMessage(content=m)]
        res = await invoke_llm(llm, msgs)
        if res.content == "done":
            planner_status  = "done"
        else:
            planner_status  = "invoke_agent"
        return {"messages": [m], "planner_status": planner_status, "agent_name": res.content}

    def invoke_agent_decide_next_node(state: AgentState) -> str:

        if state.planner_status == "planner":
            return "planner"
        
        if state.status == "ask_human":
            return "human"
        return "planner"


    def create_graph():
        

        planner_graph = StateGraph(AgentState)
        planner_graph.add_node("planner", planning_node)
        planner_graph.add_node("invoke_agent", invoke_agent_node)
        planner_graph.add_node("human", human_node)
        planner_graph.add_node("done", done_node)

        planner_graph.add_conditional_edges("planner", planner_decide_next_node, {
            "invoke_agent" : "invoke_agent",
            "done": "done"})

        planner_graph.add_conditional_edges("invoke_agent", invoke_agent_decide_next_node, {
            "planner": "planner",
            "human": "human"})

        planner_graph.add_edge("human", "planner")
        planner_graph.add_edge("done", END)

        planner_graph.set_entry_point("planner")

        planner_workflow = planner_graph.compile(checkpointer=MemorySaver(), interrupt_before=["human"])

        return planner_workflow

    return create_graph()

def build_multi_agent(calendar, memory, config):
    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        temperature=0.2,
    )

    input_graph = build_input_agent(llm, MemorySaver(), config)
    booking_graph = build_booking_agent(llm, calendar, MemorySaver(), config)
    planner_graph = build_planner_agent(llm, memory, input_graph, booking_graph, config)

    return planner_graph
