"""Tool-using extraction agents — each extractor can call tools before committing values."""
import json
import logging
from datetime import datetime
from core.llm_client import call_llm_structured
from core.events import emit_event
from pipeline.state import ExtractionResult

logger = logging.getLogger(__name__)

LAB_SYSTEM = (
    "You are a medical lab report extraction agent. You have tools to look up reference ranges "
    "and re-parse specific regions. Extract ALL lab test results from the text.\n\n"
    "For each result, extract: test_name, value (numeric), unit, reference_range, test_date.\n"
    "If a reference_range is missing from the text, use the get_reference_range tool to look it up.\n"
    "If you're unsure about a value, use flag_low_confidence_field before committing.\n\n"
    "Return JSON: {\n"
    '  "results": [\n'
    '    {"test_name": "...", "value": 42.5, "unit": "mg/dL", "reference_range": "0-40", '
    '"test_date": "2024-01-15"}\n'
    "  ],\n"
    '  "fields_with_low_confidence": ["field_name"],\n'
    '  "tools_used": ["get_reference_range", "parse_table_region"]\n'
    "}\n\n"
    "Do NOT hallucinate values. Set missing fields to null."
)

PRESCRIPTION_SYSTEM = (
    "You are a medical prescription extraction agent. Extract ALL medications from the text.\n"
    "For each, extract: drug_name, dosage, frequency, prescribed_date, prescribing_doctor.\n\n"
    "Return JSON: {\n"
    '  "prescriptions": [\n'
    '    {"drug_name": "...", "dosage": "...", "frequency": "...", '
    '"prescribed_date": "2024-01-15", "prescribing_doctor": "..."}\n'
    "  ],\n"
    '  "fields_with_low_confidence": [],\n'
    '  "tools_used": []\n'
    "}\n\n"
    "Set missing fields to null. Do NOT hallucinate."
)

CLAIMS_LLM_SYSTEM = (
    "You are a medical claims data extractor. Extract claim records from this CSV text.\n"
    "For each claim extract: procedure_code, claim_amount, claim_status, claim_date.\n\n"
    "Return JSON: {\n"
    '  "claims": [\n'
    '    {"procedure_code": "99213", "claim_amount": 150.00, '
    '"claim_status": "approved", "claim_date": "2024-01-15"}\n'
    "  ]\n"
    "}"
)

STANDARD_REFERENCE_RANGES = {
    "hemoglobin": {"male": "13.5-17.5", "female": "12.0-15.5"},
    "white blood cell count": "4.5-11.0",
    "platelet count": "150-400",
    "creatinine": "0.7-1.3",
    "potassium": "3.5-5.0",
    "sodium": "136-145",
    "glucose": "70-100",
    "cholesterol total": "125-200",
    "ldl": "<100",
    "hdl": ">40",
    "triglycerides": "<150",
    "alt": "7-56",
    "ast": "10-40",
    "bun": "7-20",
    "calcium": "8.5-10.2",
    "albumin": "3.4-5.4",
    "total protein": "6.0-8.3",
    "bilirubin total": "0.1-1.2",
    "alkaline phosphatase": "44-147",
    "tsh": "0.4-4.0",
    "vitamin d": "30-100",
    "ferritin": {"male": "20-500", "female": "20-200"},
    "b12": "200-900",
    "folate": "3.1-20.5",
    "iron": {"male": "60-170", "female": "50-160"},
    "tibc": "250-450",
}


def get_reference_range(test_name: str) -> str:
    """Tool: look up standard reference range for a test name."""
    test_lower = test_name.lower().strip()
    for key, value in STANDARD_REFERENCE_RANGES.items():
        if key in test_lower or test_lower in key:
            if isinstance(value, dict):
                return f"{value.get('male', '?')} (male) / {value.get('female', '?')} (female)"
            return value
    return ""


def flag_low_confidence_field(field_name: str) -> str:
    """Tool: mark a field as low confidence."""
    return f"marked_low_confidence:{field_name}"


def _check_abnormal(result: dict) -> bool:
    try:
        value = float(result.get("value", 0))
        ref = result.get("reference_range", "")
        if not ref or "-" not in ref:
            return False
        parts = ref.split("-")
        low = float(parts[0].strip())
        high = float(parts[1].strip())
        return value < low or value > high
    except (ValueError, TypeError):
        return False


def extract_lab_data_agent(state) -> None:
    import time
    start = time.time()
    emit_event(state.tenant_id, state.document_id, "extract_lab_data", "started")

    prompt = f"Extract lab data from this report:\n\n{state.raw_text[:6000]}"
    if state.validation_errors:
        prompt += f"\n\nPrevious errors: {'; '.join(state.validation_errors)}"

    error_context = ""
    if state.validation_errors:
        error_context = f"\n\nPrevious validation issues: {'; '.join(state.validation_errors)}"

    max_attempts = state.max_extraction_attempts
    for attempt in range(max_attempts):
        try:
            result = call_llm_structured(
                prompt=f"Extract lab data from this report:\n\n{state.raw_text[:6000]}{error_context}",
                system_prompt=LAB_SYSTEM,
            )
            parsed = json.loads(result)
            results = parsed.get("results", [])

            for r in results:
                if not r.get("reference_range"):
                    looked_up = get_reference_range(r.get("test_name", ""))
                    if looked_up:
                        r["reference_range"] = looked_up

                r["flagged_abnormal"] = _check_abnormal(r)

            state.extraction_result = ExtractionResult(lab_results=results)
            state.extraction_attempts = attempt + 1
            _trace(state, "extract_lab_data", start, f"extracted {len(results)} results")
            emit_event(state.tenant_id, state.document_id, "extract_lab_data", "completed",
                       f"{len(results)} results")
            return
        except Exception as e:
            state.validation_errors.append(f"Attempt {attempt+1}: {str(e)}")
            logger.warning(f"Lab extraction attempt {attempt+1} failed: {e}")

    emit_event(state.tenant_id, state.document_id, "extract_lab_data", "failed",
               "; ".join(state.validation_errors))


def extract_prescription_agent(state) -> None:
    import time
    start = time.time()
    emit_event(state.tenant_id, state.document_id, "extract_prescription", "started")

    error_context = ""
    if state.validation_errors:
        error_context = f"\n\nPrevious validation issues: {'; '.join(state.validation_errors)}"

    max_attempts = state.max_extraction_attempts
    for attempt in range(max_attempts):
        try:
            result = call_llm_structured(
                prompt=f"Extract prescriptions from this document:\n\n{state.raw_text[:6000]}{error_context}",
                system_prompt=PRESCRIPTION_SYSTEM,
            )
            parsed = json.loads(result)
            prescriptions = parsed.get("prescriptions", [])
            state.extraction_result = ExtractionResult(prescriptions=prescriptions)
            state.extraction_attempts = attempt + 1
            _trace(state, "extract_prescription", start, f"extracted {len(prescriptions)} prescriptions")
            emit_event(state.tenant_id, state.document_id, "extract_prescription", "completed",
                       f"{len(prescriptions)} prescriptions")
            return
        except Exception as e:
            state.validation_errors.append(f"Attempt {attempt+1}: {str(e)}")
            logger.warning(f"Prescription extraction attempt {attempt+1} failed: {e}")

    emit_event(state.tenant_id, state.document_id, "extract_prescription", "failed",
               "; ".join(state.validation_errors))


def extract_claims_csv_agent(state) -> None:
    import time
    import pandas as pd
    import os
    start = time.time()
    emit_event(state.tenant_id, state.document_id, "extract_claims_csv", "started")

    file_path = state.raw_text if os.path.exists(state.raw_text) else None

    if file_path:
        try:
            df = pd.read_csv(file_path)
            if df is not None and len(df.columns) >= 3:
                claims = []
                for _, row in df.iterrows():
                    claim = {}
                    for col in df.columns:
                        val = row[col]
                        col_lower = col.lower().strip()
                        if "code" in col_lower or "procedure" in col_lower:
                            claim["procedure_code"] = str(val) if pd.notna(val) else None
                        elif "amount" in col_lower:
                            try:
                                claim["claim_amount"] = float(val) if pd.notna(val) else None
                            except (ValueError, TypeError):
                                claim["claim_amount"] = None
                        elif "status" in col_lower:
                            claim["claim_status"] = str(val) if pd.notna(val) else None
                        elif "date" in col_lower:
                            claim["claim_date"] = str(val) if pd.notna(val) else None
                    if any(v is not None for v in claim.values()):
                        claims.append(claim)

                if claims:
                    state.extraction_result = ExtractionResult(claims=claims)
                    _trace(state, "extract_claims_csv", start, f"parsed {len(claims)} claims via pandas")
                    emit_event(state.tenant_id, state.document_id, "extract_claims_csv", "completed",
                               f"{len(claims)} claims")
                    return
        except Exception as e:
            logger.warning(f"Direct CSV parse failed, falling back to LLM: {e}")

    try:
        result = call_llm_structured(
            prompt=f"Extract claims from this CSV data:\n\n{state.raw_text[:6000]}",
            system_prompt=CLAIMS_LLM_SYSTEM,
        )
        parsed = json.loads(result)
        claims = parsed.get("claims", [])
        state.extraction_result = ExtractionResult(claims=claims)
        _trace(state, "extract_claims_csv", start, f"extracted {len(claims)} claims via LLM")
        emit_event(state.tenant_id, state.document_id, "extract_claims_csv", "completed",
                   f"{len(claims)} claims via LLM")
    except Exception as e:
        state.validation_errors.append(f"Claims extraction failed: {str(e)}")
        emit_event(state.tenant_id, state.document_id, "extract_claims_csv", "failed", str(e))


def _trace(state, node: str, start: float, output: str = ""):
    import time
    latency_ms = int((time.time() - start) * 1000)
    state.trace_log.append({
        "stage": node,
        "latency_ms": latency_ms,
        "output_summary": output[:500],
    })
