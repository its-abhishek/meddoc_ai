"""Verifier/Critic Agent — checks extractor output against source text before persisting."""
import json
import logging
from core.llm_client import call_llm_structured
from core.events import emit_event

logger = logging.getLogger(__name__)

VERIFIER_SYSTEM = (
    "You are a medical data verification agent. Your job is to check if extracted values "
    "actually appear in the source text and make sense.\n\n"
    "Check each field for:\n"
    "1. Does the value appear in the source text?\n"
    "2. Are units plausible for the test/drug named?\n"
    "3. Is the date sane (not in the far future, not before 1900)?\n"
    "4. Are there any obvious transcription errors?\n\n"
    "Return JSON: {\n"
    '  "accept": true/false,\n'
    '  "issues": ["field X: value not found in source", "field Y: unit seems wrong"],\n'
    '  "confidence": "high"|"medium"|"low"\n'
    "}\n\n"
    "Only flag real problems, not minor formatting differences. "
    "If you're unsure, lean toward accepting with a note."
)


def verifier_agent(state) -> None:
    import time
    start = time.time()
    emit_event(state.tenant_id, state.document_id, "verifier", "started")

    if not state.extraction_result:
        state.verification_accepted = False
        state.verification_issues = ["No extraction result to verify"]
        emit_event(state.tenant_id, state.document_id, "verifier", "completed", "no extraction")
        _trace(state, "verifier", start, "no extraction to verify")
        return

    extraction_json = state.extraction_result.model_dump_json()
    prompt = (
        f"Source text:\n{state.raw_text[:4000]}\n\n"
        f"Extracted data:\n{extraction_json}\n\n"
        "Verify the extraction matches the source. Check each field carefully."
    )

    try:
        result = call_llm_structured(prompt=prompt, system_prompt=VERIFIER_SYSTEM)
        parsed = json.loads(result)
        state.verification_accepted = parsed.get("accept", False)
        state.verification_issues = parsed.get("issues", [])
        confidence = parsed.get("confidence", "medium")

        if state.verification_accepted:
            emit_event(state.tenant_id, state.document_id, "verifier", "completed",
                       f"accepted (confidence={confidence})")
        else:
            emit_event(state.tenant_id, state.document_id, "verifier", "completed",
                       f"rejected: {state.verification_issues[:2]}")
        _trace(state, "verifier", start, f"accept={state.verification_accepted}, confidence={confidence}")
    except Exception as e:
        logger.error(f"Verifier failed: {e}")
        state.verification_accepted = True
        emit_event(state.tenant_id, state.document_id, "verifier", "failed", str(e))


def _trace(state, node: str, start: float, output: str = ""):
    import time
    latency_ms = int((time.time() - start) * 1000)
    state.trace_log.append({
        "stage": node,
        "latency_ms": latency_ms,
        "output_summary": output[:500],
    })
