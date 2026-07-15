"""Planner Agent — inspects document and creates a processing plan using tools."""
import json
import logging
from core.llm_client import call_llm_structured
from core.events import emit_event

logger = logging.getLogger(__name__)

PLANNER_SYSTEM = (
    "You are a medical document processing planner. You have access to tools that inspect the document.\n"
    "Analyze the document text and decide: what type of document is this, what steps are needed, "
    "and are there any quality issues?\n\n"
    "Available steps: ocr_partial, extract_lab_data, extract_prescription, extract_claims_csv, "
    "verify_extraction, chunk_and_embed\n\n"
    "Available doc_types: lab_report, prescription, insurance_claim, discharge_summary, unknown\n\n"
    "Return JSON: {\n"
    '  "doc_type": "...",\n'
    '  "plan": ["step1", "step2", ...],\n'
    '  "has_tables": true/false,\n'
    '  "quality_issues": ["blurry_scan", "missing_pages", "none"],\n'
    '  "reasoning": "..."\n'
    "}"
)

PLANNER_TOOLS_PROMPT = (
    "\n\nAvailable tools you can simulate:\n"
    "- inspect_document_structure: page count, has tables, has images\n"
    "- classify_type: determine doc type\n"
    "- check_quality: blurry scan? missing pages?\n\n"
    "Based on the text content, reason about which tools you would call and what they'd return."
)


def planner_agent(state) -> None:
    start = __import__("time").time()
    emit_event(state.tenant_id, state.document_id, "planner", "started")

    truncated = state.raw_text[:4000]
    prompt = (
        f"Document filename: {state.source_filename}\n\n"
        f"Document text (first 4000 chars):\n{truncated}\n\n"
        f"{PLANNER_TOOLS_PROMPT}\n"
        "Create a processing plan for this document."
    )

    try:
        result = call_llm_structured(prompt=prompt, system_prompt=PLANNER_SYSTEM)
        parsed = json.loads(result)
        state.doc_type = parsed.get("doc_type", "unknown")
        state.plan = parsed.get("plan", ["verify_extraction", "chunk_and_embed"])
        _trace(state, "planner", start, f"doc_type={state.doc_type}, plan={state.plan}")
        emit_event(state.tenant_id, state.document_id, "planner", "completed",
                   f"doc_type={state.doc_type}")
    except Exception as e:
        logger.error(f"Planner agent failed: {e}")
        from core.llm_client import classify_document
        state.doc_type = classify_document(state.raw_text)
        state.plan = ["verify_extraction", "chunk_and_embed"]
        emit_event(state.tenant_id, state.document_id, "planner", "failed", str(e))


def _trace(state, node: str, start: float, output: str = ""):
    import time
    latency_ms = int((time.time() - start) * 1000)
    state.trace_log.append({
        "stage": node,
        "latency_ms": latency_ms,
        "output_summary": output[:500],
    })
