from __future__ import annotations
import os
from fastapi import FastAPI
from dotenv import load_dotenv

from app.schemas import ChatRequest, ChatResponse, AgentState
from app.calendar_mock import MockCalendar
from app.agent import build_agent

from langchain_core.runnables.graph import MermaidDrawMethod
from langchain_core.runnables.graph_mermaid import draw_mermaid_png

load_dotenv()

app = FastAPI(title="Meeting Agent")

calendar = MockCalendar()
agent_graph = build_agent(calendar)

graph_object = agent_graph.get_graph()
mermaid_syntax = graph_object.draw_mermaid()

draw_mermaid_png(mermaid_syntax=mermaid_syntax, output_file_path="workflow_graph.png")
