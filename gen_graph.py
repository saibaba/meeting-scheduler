from __future__ import annotations
import os
from fastapi import FastAPI
from dotenv import load_dotenv

from app.schemas import ChatRequest, ChatResponse, AgentState
from app.calendar_mock import MockCalendar
from app.naive_agent import create_human_in_loop_graph, create_revivable_graph
from app.multi_agent import build_multi_agent

from langchain_core.runnables.graph import MermaidDrawMethod
from langchain_core.runnables.graph_mermaid import draw_mermaid_png
from langgraph.checkpoint.memory import MemorySaver

load_dotenv()

app = FastAPI(title="Meeting Agent")

def create_image(agent_graph, filename):
    graph_object = agent_graph.get_graph()
    mermaid_syntax = graph_object.draw_mermaid()
    draw_mermaid_png(mermaid_syntax=mermaid_syntax, output_file_path=filename)


hil_graph = create_human_in_loop_graph()
create_image(hil_graph, "workflow_graph_human.png")

naive_graph = create_revivable_graph()
create_image(naive_graph, "workflow_graph_iterative.png")

input_graph, booking_graph, planner_graph = build_multi_agent()
create_image(input_graph, "multiagent_workflow_graph_input.png")
create_image(booking_graph, "multiagent_workflow_graph_booking.png")
create_image(planner_graph, "multiagent_workflow_graph_planner.png")

