"""Risk-Flagging Agent — ReAct loop that investigates drug interactions and lab concerns."""
import json
import logging
from typing import List, Dict, Any
from core.llm_client import call_llm_structured
from core.events import emit_event

logger = logging.getLogger(__name__)

RISK_SYSTEM = (
    "You are a clinical risk investigation agent. Given patient data, you investigate potential "
    "drug interactions, abnormal lab trends, and missing follow-ups.\n\n"
    "You have access to tools:\n"
    "- get_patient_history: returns past flags for this patient\n"
    "- check_interaction_rules: checks known drug-drug and drug-lab interactions\n"
    "- get_similar_past_flags: checks if this combo was flagged before\n\n"
    "Investigate step by step. Return JSON:\n"
    "{\n"
    '  "flags": [\n'
    '    {\n'
    '      "flag_type": "drug_interaction"|"abnormal_lab"|"missing_followup"|"general_concern",\n'
    '      "severity": "high"|"medium"|"low",\n'
    '      "description": "description ending with clinical-review disclaimer",\n'
    '      "source_record_ids": ["id1", "id2"],\n'
    '      "source": "rule_match"|"llm_reasoning"\n'
    "    }\n"
    "  ],\n"
    '  "investigation_steps": ["step1", "step2"],\n'
    '  "tools_used": ["check_interaction_rules"]\n'
    "}\n\n"
    "Every description must end with: 'This is an advisory flag — review by a clinician is required.'\n"
    "Use rule_match source when the flag matches known interaction rules."
)

KNOWN_INTERACTIONS = [
    {"drugs": ["ace inhibitor", "lisinopril", "enalapril", "ramipril"],
     "lab": "potassium", "condition": "elevated potassium >5.0",
     "flag_type": "drug_interaction", "severity": "high",
     "description": "ACE inhibitor + elevated potassium — risk of hyperkalemia"},
    {"drugs": ["warfarin"],
     "lab": "inr", "condition": "INR >3.0",
     "flag_type": "drug_interaction", "severity": "high",
     "description": "Warfarin with elevated INR — increased bleeding risk"},
    {"drugs": ["metformin"],
     "lab": "creatinine", "condition": "creatinine >1.5",
     "flag_type": "drug_interaction", "severity": "high",
     "description": "Metformin with elevated creatinine — risk of lactic acidosis"},
    {"drugs": ["nsaid", "ibuprofen", "naproxen", "diclofenac"],
     "lab": "creatinine", "condition": "creatinine >1.3",
     "flag_type": "drug_interaction", "severity": "medium",
     "description": "NSAID use with elevated creatinine — risk of renal impairment"},
    {"drugs": ["digoxin"],
     "lab": "potassium", "condition": "potassium <3.5",
     "flag_type": "drug_interaction", "severity": "high",
     "description": "Digoxin with low potassium — increased risk of digoxin toxicity"},
    {"drugs": ["statins", "atorvastatin", "simvastatin", "rosuvastatin"],
     "lab": "alt", "condition": "ALT >3x upper normal",
     "flag_type": "drug_interaction", "severity": "medium",
     "description": "Statin use with elevated liver enzymes — monitor hepatic function"},
    {"drugs": ["furosemide", "hydrochlorothiazide", "hctz"],
     "lab": "potassium", "condition": "potassium <3.5",
     "flag_type": "drug_interaction", "severity": "medium",
     "description": "Diuretic use with low potassium — monitor electrolyte levels"},
    {"drugs": ["sglt2 inhibitor", "empagliflozin", "dapagliflozin"],
     "lab": "glucose", "condition": "glucose <70",
     "flag_type": "drug_interaction", "severity": "high",
     "description": "SGLT2 inhibitor with low glucose — hypoglycemia risk"},
]

FOLLOWUP_RULES = [
    {"condition": "elevated hba1c >7.0", "recheck_months": 3,
     "description": "HbA1c >7.0 — recheck recommended within 3 months"},
    {"condition": "elevated ldl >190", "recheck_months": 6,
     "description": "LDL >190 — recheck recommended within 6 months"},
    {"condition": "creatinine >1.4", "recheck_months": 3,
     "description": "Elevated creatinine — recheck recommended within 3 months"},
    {"condition": "thyroid disorder", "recheck_months": 6,
     "description": "Abnormal TSH — recheck recommended within 6 months"},
]


def check_interaction_rules(drugs: List[str], labs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Tool: match patient's drugs and labs against known interaction rules."""
    flags = []
    drug_lower = [d.lower() for d in drugs]

    for interaction in KNOWN_INTERACTIONS:
        drug_match = any(
            any(keyword in d for keyword in interaction["drugs"])
            for d in drug_lower
        )
        if not drug_match:
            continue

        for lab in labs:
            lab_name = lab.get("test_name", "").lower()
            lab_value = lab.get("value")
            lab_id = lab.get("id", "")

            condition = interaction["condition"].lower()
            if interaction["lab"] in lab_name:
                if ">" in condition:
                    try:
                        threshold = float(condition.split(">")[1].split()[0])
                        if lab_value and float(lab_value) > threshold:
                            flag = interaction.copy()
                            flag["source_record_ids"] = [lab_id]
                            flag["source"] = "rule_match"
                            flag["description"] += (
                                f" (lab: {lab.get('test_name')}={lab_value}). "
                                "This is an advisory flag — review by a clinician is required."
                            )
                            flags.append(flag)
                    except (ValueError, IndexError):
                        pass
                elif "<" in condition:
                    try:
                        threshold = float(condition.split("<")[1].split()[0])
                        if lab_value and float(lab_value) < threshold:
                            flag = interaction.copy()
                            flag["source_record_ids"] = [lab_id]
                            flag["source"] = "rule_match"
                            flag["description"] += (
                                f" (lab: {lab.get('test_name')}={lab_value}). "
                                "This is an advisory flag — review by a clinician is required."
                            )
                            flags.append(flag)
                    except (ValueError, IndexError):
                        pass
    return flags


def get_similar_past_flags(flag_type: str, patient_existing_flags: List[str]) -> bool:
    """Tool: check if similar flags exist (and were dismissed)."""
    if not patient_existing_flags:
        return False
    for existing_type in patient_existing_flags:
        if flag_type in existing_type.lower():
            return True
    return False


def risk_flagging_agent(state, db_session=None) -> List[Dict[str, Any]]:
    """Run risk flagging on the full patient record.

    Called after persist_structured_data completes. Queries the whole patient's data,
    not just the current document. Returns list of risk flags.
    """
    import time
    start = time.time()
    emit_event(state.tenant_id, state.document_id, "risk_flagging", "started")

    labs_data = []
    if state.extraction_result:
        labs_data = state.extraction_result.lab_results or []

    drugs = []
    if state.extraction_result:
        drugs = [rx.get("drug_name", "") for rx in (state.extraction_result.prescriptions or [])]

    rule_flags = check_interaction_rules(drugs, labs_data)

    if rule_flags:
        emit_event(state.tenant_id, state.document_id, "risk_flagging", "completed",
                   f"{len(rule_flags)} rule-match flags")
        _trace(state, "risk_flagging", start, f"{len(rule_flags)} rule-match flags")
        return rule_flags

    prompt = (
        f"Patient has the following:\n\n"
        f"Drugs: {drugs}\n"
        f"Lab results: {json.dumps(labs_data, indent=2)}\n\n"
        f"Investigate whether any flags should be raised. Consider drug interactions, "
        f"abnormal labs, and missing follow-ups."
    )

    try:
        result = call_llm_structured(prompt=prompt, system_prompt=RISK_SYSTEM)
        parsed = json.loads(result)
        flags = parsed.get("flags", [])
        for f in flags:
            if "advisory" not in f.get("description", "").lower():
                f["description"] += (
                    " This is an advisory flag — review by a clinician is required."
                )

        emit_event(state.tenant_id, state.document_id, "risk_flagging", "completed",
                   f"{len(flags)} flags (llm_reasoning)")
        _trace(state, "risk_flagging", start, f"{len(flags)} llm-reasoned flags")
        return flags
    except Exception as e:
        logger.error(f"Risk flagging failed: {e}")
        emit_event(state.tenant_id, state.document_id, "risk_flagging", "failed", str(e))
        return []


def _trace(state, node: str, start: float, output: str = ""):
    import time
    latency_ms = int((time.time() - start) * 1000)
    state.trace_log.append({
        "stage": node,
        "latency_ms": latency_ms,
        "output_summary": output[:500],
    })
