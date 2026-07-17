"""LangGraph pipeline — fully agentic document processing with planner, tool-using extractors, verifier."""
import json
import time
import logging
from typing import Literal
from langgraph.graph import StateGraph, END

from pipeline.state import GraphState
from pipeline.agents.planner import planner_agent
from pipeline.agents.extractors import (
    extract_lab_data_agent,
    extract_prescription_agent,
    extract_claims_csv_agent,
    extract_discharge_summary_agent,
)
from pipeline.agents.verifier import verifier_agent
from core.events import emit_event
from core.embeddings import embed_texts
from utils.parsers import parse_document
from utils.chunking import chunk_text

logger = logging.getLogger(__name__)


def _emit(state: GraphState, node: str, status: str, detail: str = ""):
    emit_event(state.tenant_id, state.document_id, node, status, detail)


def _trace(state: GraphState, node: str, start: float, output: str = ""):
    latency_ms = int((time.time() - start) * 1000)
    state.trace_log.append({
        "stage": node,
        "latency_ms": latency_ms,
        "output_summary": output[:500],
    })


def planner_node(state: GraphState) -> GraphState:
    planner_agent(state)
    return state


def extract_lab_node(state: GraphState) -> GraphState:
    extract_lab_data_agent(state)
    return state


def extract_prescription_node(state: GraphState) -> GraphState:
    extract_prescription_agent(state)
    return state


def extract_claims_node(state: GraphState) -> GraphState:
    extract_claims_csv_agent(state)
    return state


def extract_discharge_summary_node(state: GraphState) -> GraphState:
    extract_discharge_summary_agent(state)
    return state


def verifier_node(state: GraphState) -> GraphState:
    verifier_agent(state)
    return state


def _mark_needs_manual_review(state: GraphState) -> GraphState:
    start = time.time()
    _emit(state, "manual_review", "started")
    if not state.manual_review_reason:
        state.manual_review_reason = f"no extractor defined for doc_type: {state.doc_type}"
    state.doc_type = "needs_manual_review"
    _trace(state, "manual_review", start, state.manual_review_reason)
    _emit(state, "manual_review", "completed", state.manual_review_reason)
    return state


def route_after_planner(state: GraphState) -> str:
    if state.doc_type == "lab_report":
        return "extract_lab_data"
    elif state.doc_type == "prescription":
        return "extract_prescription"
    elif state.doc_type == "insurance_claim":
        return "extract_claims_csv"
    elif state.doc_type == "discharge_summary":
        return "extract_discharge_summary"
    else:
        return "manual_review"


def route_after_verification(state: GraphState) -> str:
    if state.verification_accepted:
        return "persist"
    if state.extraction_attempts < state.max_extraction_attempts:
        return route_after_planner(state)
    return "manual_review"


def persist_node(state: GraphState) -> GraphState:
    start = time.time()
    _emit(state, "persist", "started")
    _trace(state, "persist", start, "structured data ready for persistence")
    _emit(state, "persist", "completed")
    return state


def chunk_embed_node(state: GraphState) -> GraphState:
    start = time.time()
    _emit(state, "chunk_embed", "started")

    raw_text = state.raw_text
    if not raw_text:
        _emit(state, "chunk_embed", "completed", "no text to chunk")
        return state

    chunks = chunk_text(raw_text, chunk_size=500, overlap=50)
    if chunks:
        embeddings = embed_texts(chunks)
        state.chunks_data = [
            {"text": c, "embedding": e} for c, e in zip(chunks, embeddings)
        ]
        _trace(state, "chunk_embed", start, f"{len(chunks)} chunks embedded")
        _emit(state, "chunk_embed", "completed", f"{len(chunks)} chunks")
    else:
        _emit(state, "chunk_embed", "completed", "no chunks to embed")

    return state


def finalize_node(state: GraphState) -> GraphState:
    start = time.time()
    _emit(state, "pipeline", "completed", f"doc_type={state.doc_type}")
    _trace(state, "finalize", start, "pipeline completed")
    return state


def build_graph() -> StateGraph:
    graph = StateGraph(GraphState)

    graph.add_node("planner", planner_node)
    graph.add_node("extract_lab_data", extract_lab_node)
    graph.add_node("extract_prescription", extract_prescription_node)
    graph.add_node("extract_claims_csv", extract_claims_node)
    graph.add_node("extract_discharge_summary", extract_discharge_summary_node)
    graph.add_node("manual_review", _mark_needs_manual_review)
    graph.add_node("verifier", verifier_node)
    graph.add_node("persist", persist_node)
    graph.add_node("chunk_embed", chunk_embed_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("planner")

    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "extract_lab_data": "extract_lab_data",
            "extract_prescription": "extract_prescription",
            "extract_claims_csv": "extract_claims_csv",
            "extract_discharge_summary": "extract_discharge_summary",
            "manual_review": "manual_review",
        },
    )

    graph.add_edge("extract_lab_data", "verifier")
    graph.add_edge("extract_prescription", "verifier")
    graph.add_edge("extract_claims_csv", "verifier")
    graph.add_edge("extract_discharge_summary", "verifier")

    graph.add_conditional_edges(
        "verifier",
        route_after_verification,
        {
            "persist": "persist",
            "extract_lab_data": "extract_lab_data",
            "extract_prescription": "extract_prescription",
            "extract_claims_csv": "extract_claims_csv",
            "extract_discharge_summary": "extract_discharge_summary",
            "manual_review": "manual_review",
        },
    )

    graph.add_edge("persist", "chunk_embed")
    graph.add_edge("chunk_embed", "finalize")
    graph.add_edge("finalize", END)
    graph.add_edge("manual_review", END)

    return graph.compile()
