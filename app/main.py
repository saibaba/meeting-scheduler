from __future__ import annotations
import os
from fastapi import FastAPI
from dotenv import load_dotenv

from .schemas import ChatRequest, ChatResponse, AgentState, RuntimeContext, MeetingDraft
from .calendar_mock import MockCalendar
from .naive_agent import create_human_in_loop_graph, create_revivable_graph
from .multi_agent import build_multi_agent
from langchain_core.output_parsers import JsonOutputParser

from langchain_openai import ChatOpenAI

from langgraph.checkpoint.memory import MemorySaver
import langchain

load_dotenv()

app = FastAPI(title="Meeting Agent")

langchain.verbose = False

llm = ChatOpenAI(
    model=os.getenv("OPENAI_MODEL", "gpt-4o"),
    temperature=0.2,
)


class WorkflowState:
    def __init__(self, state: AgentState, graph: StateGraph, memory: MemorySaver, config: dict, context: RuntimeContext):
        self.state = state
        self.graph = graph
        self.memory = memory
        self.config = config
        self.state_dict = None
        self.context = context

# In-memory session store (demo)
WORKFLOWS: dict[str, WorkflowState] = {}

ITERATE = 1
HUMAN_IN_LOOP = 2
MULTI_AGENT = 3

@app.get("/healthz")
def healthz():
    return {"ok": True}

async def chat_human_in_loop_mode(req: ChatRequest):

    new_state = None

    if req.session_id not in WORKFLOWS:
        print("HIL: Creating new session")
        config = {"configurable": {"thread_id": req.session_id}}
        graph = create_human_in_loop_graph()
        context = RuntimeContext(
            json_parser = JsonOutputParser(pydantic_object=MeetingDraft),
            llm = llm,
            default_tz = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"),
            calendar = MockCalendar(["jeff", "mike"]),
            input_workflow = None,
            booking_workflow = None
        )
        state = AgentState()
        workflow_state = WorkflowState(state, graph, None, config, context)
        WORKFLOWS[req.session_id] = workflow_state
        state.messages = [req.message]
        new_state = await graph.ainvoke(state, config=config, context=context)
    else:
        print("HIL: Resumeing existing session")
        workflow_state = WORKFLOWS[req.session_id]
        graph = workflow_state.graph
        config = workflow_state.config
        context = workflow_state.context
        graph.update_state(config, {"messages": [req.message]})
        new_state = await graph.ainvoke(None, config=config, context=context) 

    print("New state (", type(new_state), "): ", new_state)

    return ChatResponse(
        session_id=req.session_id,
        reply=new_state['messages'][-1],
        state=new_state,
    )

async def chat_iterate(req: ChatRequest):

    new_state = None

    if req.session_id not in WORKFLOWS:
        print("Iterate: Creating new session")
        config = {"configurable": {"thread_id": req.session_id}}
        graph = create_revivable_graph()
        context = RuntimeContext(
            json_parser = JsonOutputParser(pydantic_object=MeetingDraft),
            llm = llm,
            default_tz = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"),
            calendar = MockCalendar(["jeff", "mike"]),
            input_workflow = None,
            booking_workflow = None
        )
        state = AgentState()
        workflow_state = WorkflowState(state, graph, None, config, context)
        WORKFLOWS[req.session_id] = workflow_state
        state.messages = [req.message]
        new_state = await graph.ainvoke(state, config=config, context=context)
    else:
        print("Iterate: Resumeing existing session")
        workflow_state = WORKFLOWS[req.session_id]
        graph = workflow_state.graph
        state_dict = workflow_state.state_dict
        state_dict['messages'] = [req.message]
        context = workflow_state.context
        config = workflow_state.config
        new_state = await graph.ainvoke(state_dict, config=config, context=context)

    print("New state (", type(new_state), "): ", new_state)

    WORKFLOWS[req.session_id].state_dict = new_state

    return ChatResponse(
        session_id=req.session_id,
        reply=new_state['messages'][-1],
        state=new_state,
    )


async def chat_multiagent(req: ChatRequest):

    new_state = None

    if req.session_id not in WORKFLOWS:
        print("MA: Creating new session")
        config = {"configurable": {"thread_id": req.session_id}}

        input_workflow, booking_workflow, planner_workflow = build_multi_agent()

        context = RuntimeContext(
            json_parser = JsonOutputParser(pydantic_object=MeetingDraft),
            llm = llm,
            default_tz = os.getenv("DEFAULT_TIMEZONE", "America/Los_Angeles"),
            calendar = MockCalendar(["jeff", "mike"]),
            input_workflow = input_workflow,
            booking_workflow = booking_workflow
        )

        state = AgentState()
        workflow_state = WorkflowState(state, planner_workflow, None, config, context=context)
        WORKFLOWS[req.session_id] = workflow_state
        state.messages = [req.message]
        new_state = await planner_workflow.ainvoke(state, config=config, context=context)
    else:
        print("MA: Resumeing existing session")
        workflow_state = WORKFLOWS[req.session_id]
        graph = workflow_state.graph
        context = workflow_state.context
        config = workflow_state.config
        graph.update_state(config, {"messages": [req.message]})
        new_state = await graph.ainvoke(None, config=config, context=context)

    print("New state (", type(new_state), "): ", new_state)

    return ChatResponse(
        session_id=req.session_id,
        reply=new_state['messages'][-1],
        state=new_state,
    )


mode = MULTI_AGENT

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):

    if mode == HUMAN_IN_LOOP:
        response = await chat_human_in_loop_mode(req)
        return response
    elif mode == ITERATE:
        response = await chat_iterate(req)
        return response
    else:
        response = await chat_multiagent(req)
        return response
