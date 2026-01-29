from __future__ import annotations
import os
from fastapi import FastAPI
from dotenv import load_dotenv

from .schemas import ChatRequest, ChatResponse, AgentState
from .calendar_mock import MockCalendar
from .agent import build_agent

from langgraph.checkpoint.memory import MemorySaver

load_dotenv()

app = FastAPI(title="Meeting Agent")


# In-memory session store (demo)
SESSIONS: dict[str, AgentState] = {}

class WorkflowState:
    def __init__(self, state: AgentState, graph: StateGraph, memory: MemorySaver, config: dict):
        self.state = state
        self.graph = graph
        self.memory = memory
        self.config = config
        self.state_dict = None

WORKFLOWS: dict[str, WorkflowState] = {}

ITERATE = 1
HUMAN_IN_LOOP = 2


@app.get("/healthz")
def healthz():
    return {"ok": True}

async def chat_human_in_loop_mode(req: ChatRequest):

    new_state = None

    if req.session_id not in WORKFLOWS:
        print("HIL: Creating new session")
        config = {"configurable": {"thread_id": req.session_id}}
        memory = MemorySaver()
        calendar = MockCalendar()
        graph = build_agent(calendar, memory, config)
        state = AgentState()
        workflow_state = WorkflowState(state, graph, memory, config)
        WORKFLOWS[req.session_id] = workflow_state
        state.messages = [req.message]
        new_state = await graph.ainvoke(state, config) 
    else:
        print("HIL: Resumeing existing session")
        workflow_state = WORKFLOWS[req.session_id]
        graph = workflow_state.graph
        config = workflow_state.config
        graph.update_state(config, {"messages": [req.message]})
        new_state = await graph.ainvoke(None, config) 

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
        calendar = MockCalendar()
        graph = build_agent(calendar)
        state = AgentState()
        workflow_state = WorkflowState(state, graph, None, None)
        WORKFLOWS[req.session_id] = workflow_state
        state.messages = [req.message]
        new_state = await graph.ainvoke(state)
    else:
        print("Iterate: Resumeing existing session")
        workflow_state = WORKFLOWS[req.session_id]
        graph = workflow_state.graph
        state_dict = workflow_state.state_dict
        state_dict['messages'] = [req.message]
        new_state = await graph.ainvoke(state_dict)

    print("New state (", type(new_state), "): ", new_state)

    WORKFLOWS[req.session_id].state_dict = new_state

    return ChatResponse(
        session_id=req.session_id,
        reply=new_state['messages'][-1],
        state=new_state,
    )


mode = ITERATE

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):

    if mode == HUMAN_IN_LOOP:
        response = await chat_human_in_loop_mode(req)
        return response
    else:
        response = await chat_iterate(req)
        return response
