from __future__ import annotations
import os
from fastapi import FastAPI
from dotenv import load_dotenv

from .schemas import ChatRequest, ChatResponse, AgentState
from .calendar_mock import MockCalendar
from .agent import build_agent

load_dotenv()

app = FastAPI(title="Meeting Agent")

calendar = MockCalendar()
agent_graph = build_agent(calendar)

# In-memory session store (demo)
SESSIONS: dict[str, AgentState] = {}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    state = SESSIONS.get(req.session_id, AgentState())
    state.last_user_message = req.message

    new_state = await agent_graph.ainvoke(state)
    SESSIONS[req.session_id] = new_state

    print("New state (", type(new_state), "): ", new_state)

    return ChatResponse(
        session_id=req.session_id,
        reply=new_state['last_agent_message'],
        state=new_state,
    )
