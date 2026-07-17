"""Autonomous Report Generation Agent — decides structure and generates block-based reports."""
import json
import logging
import re
import time
from datetime import datetime
from typing import List, Dict, Any, Optional
from core.llm_client import call_llm_structured, call_llm
from core.events import emit_event

logger = logging.getLogger(__name__)

REPORT_PLANNER_SYSTEM = (
    "You are a medical report planner. Given a patient's available data, decide which sections "
    "the report should include and in what order.\n\n"
    "Available section types:\n"
    "- patient_overview: patient name, DOB, ID\n"
    "- lab_summary: summary of lab results with abnormal flags\n"
    "- medication_summary: current prescriptions\n"
    "- risk_flags: open clinical risk flags\n"
    "- trends: lab value trends over time\n\n"
    "Return JSON: {\n"
    '  "sections": [\n'
    '    {"block_type": "patient_overview", "reason": "..."},'
    '    {"block_type": "lab_summary", "reason": "..."}\n'
    "  ],\n"
    '  "reasoning": "..."\n'
    "}"
)

SECTION_GENERATORS = {
    "patient_overview": (
        "You are generating a patient overview section for a medical report.\n"
        "Include: patient name, date of birth, patient ID, and number of documents on file.\n"
        "Return JSON: {\"content\": \"...\"}"
    ),
    "lab_summary": (
        "You are generating a lab results summary section.\n"
        "Summarize all lab results. Flag abnormal values. Group by test type.\n"
        "Return JSON: {\"content\": \"...\"}"
    ),
    "medication_summary": (
        "You are generating a medication summary section.\n"
        "List all current prescriptions with dosage and frequency.\n"
        "Return JSON: {\"content\": \"...\"}"
    ),
    "risk_flags": (
        "You are generating a risk flags section.\n"
        "List all open clinical risk flags by severity.\n"
        "This is an advisory section -- review by a clinician is required.\n"
        "Return JSON: {\"content\": \"...\"}"
    ),
    "trends": (
        "You are generating a lab trends section.\n"
        "Describe how key lab values have changed over time.\n"
        "Return JSON: {\"content\": \"...\"}"
    ),
}


async def should_generate_report(
    tenant_id: str,
    patient_id: str,
    db_session,
) -> bool:
    """Check if report should be auto-generated after pipeline completion."""
    from sqlalchemy import select, func
    from models.models import Report

    result = await db_session.execute(
        select(func.count()).select_from(Report).where(
            Report.tenant_id == tenant_id,
            Report.patient_id == patient_id,
        )
    )
    count = result.scalar()
    return count == 0


async def has_stale_report(tenant_id: str, patient_id: str, db_session) -> bool:
    """Check if a report exists but might be stale (new risk flags added)."""
    from sqlalchemy import select
    from models.models import Report, RiskFlag

    report_result = await db_session.execute(
        select(Report).where(
            Report.tenant_id == tenant_id,
            Report.patient_id == patient_id,
        ).order_by(Report.generated_at.desc())
    )
    report = report_result.scalar_one_or_none()
    if not report:
        return False

    flag_result = await db_session.execute(
        select(RiskFlag).where(
            RiskFlag.tenant_id == tenant_id,
            RiskFlag.patient_id == patient_id,
            RiskFlag.created_at > report.generated_at,
        )
    )
    new_flag = flag_result.scalar_one_or_none()
    return new_flag is not None


def plan_report_sections(
    has_labs: bool,
    has_prescriptions: bool,
    has_claims: bool,
    has_risk_flags: bool,
    lab_test_names: List[str],
) -> List[Dict[str, str]]:
    """Let the LLM decide report structure based on available data."""
    data_summary = {
        "has_labs": has_labs,
        "has_prescriptions": has_prescriptions,
        "has_claims": has_claims,
        "has_risk_flags": has_risk_flags,
        "lab_tests": lab_test_names,
    }

    prompt = f"Available patient data:\n{json.dumps(data_summary, indent=2)}"
    try:
        result = call_llm_structured(prompt=prompt, system_prompt=REPORT_PLANNER_SYSTEM)
        parsed = json.loads(result)
        return parsed.get("sections", [])
    except Exception as e:
        logger.error(f"Report planning failed, using defaults: {e}")
        sections = []
        sections.append({"block_type": "patient_overview", "reason": "always include"})
        if has_labs:
            sections.append({"block_type": "lab_summary", "reason": "lab data available"})
        if has_prescriptions:
            sections.append({"block_type": "medication_summary", "reason": "prescriptions available"})
        if has_risk_flags:
            sections.append({"block_type": "risk_flags", "reason": "risk flags open"})
        if has_labs and len(set(lab_test_names)) > 1:
            sections.append({"block_type": "trends", "reason": "multiple lab tests"})
        return sections


def _extract_json_from_text(text: str) -> dict:
    """Try to extract JSON object from LLM text that may contain extra content."""
    match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


SECTION_DATA_KEYS = {
    "patient_overview": ["name", "dob", "id"],
    "lab_summary": ["labs"],
    "medication_summary": ["prescriptions"],
    "risk_flags": ["risk_flags"],
    "trends": ["labs"],
}


def generate_section(
    block_type: str,
    patient_data: Dict[str, Any],
    source_record_ids: List[str],
) -> Dict[str, Any]:
    """Generate content for a single report block — sends only the relevant data subset."""
    system_prompt = SECTION_GENERATORS.get(block_type, SECTION_GENERATORS["patient_overview"])
    keys = SECTION_DATA_KEYS.get(block_type)
    section_data = {k: patient_data[k] for k in keys if k in patient_data} if keys else patient_data
    prompt = f"Generate this section using the following patient data:\n\n{json.dumps(section_data, indent=2)}"

    try:
        result = call_llm_structured(prompt=prompt, system_prompt=system_prompt)
        parsed = json.loads(result)
        content = parsed.get("content", "")
        if content:
            return {"content": content, "source_record_ids": source_record_ids}
    except Exception as e:
        logger.warning(f"Section generation failed for {block_type}: {e}")

    return {
        "content": f"[{block_type} section generation failed]",
        "source_record_ids": source_record_ids,
    }


async def generate_report(
    tenant_id: str,
    patient_id: str,
    db_session,
    trigger_node: str = "report_generation",
) -> Optional[str]:
    """Autonomously generate a report for a patient. Returns report_id or None."""
    from sqlalchemy import select
    from models.models import (
        Patient, LabResult, Prescription, Claim, RiskFlag, Report, ReportBlock
    )
    import uuid

    emit_event(tenant_id, patient_id, trigger_node, "started", "autonomous report generation")

    patient_result = await db_session.execute(
        select(Patient).where(Patient.id == patient_id, Patient.tenant_id == tenant_id)
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        emit_event(tenant_id, patient_id, trigger_node, "failed", "patient not found")
        return None

    lab_result = await db_session.execute(
        select(LabResult).where(LabResult.tenant_id == tenant_id, LabResult.patient_id == patient_id)
    )
    labs = lab_result.scalars().all()

    rx_result = await db_session.execute(
        select(Prescription).where(Prescription.tenant_id == tenant_id, Prescription.patient_id == patient_id)
    )
    rxs = rx_result.scalars().all()

    claim_result = await db_session.execute(
        select(Claim).where(Claim.tenant_id == tenant_id, Claim.patient_id == patient_id)
    )
    claims = claim_result.scalars().all()

    flag_result = await db_session.execute(
        select(RiskFlag).where(
            RiskFlag.tenant_id == tenant_id,
            RiskFlag.patient_id == patient_id,
            RiskFlag.status == "open",
        )
    )
    flags = flag_result.scalars().all()

    has_labs = len(labs) > 0
    has_prescriptions = len(rxs) > 0
    has_claims = len(claims) > 0
    has_risk_flags = len(flags) > 0
    lab_test_names = list(set(l.test_name for l in labs))

    sections = plan_report_sections(has_labs, has_prescriptions, has_claims, has_risk_flags, lab_test_names)

    patient_data = {
        "name": patient.name,
        "dob": str(patient.dob) if patient.dob else "N/A",
        "id": patient.id,
        "labs": [{"test_name": l.test_name, "value": l.value, "unit": l.unit,
                   "reference_range": l.reference_range, "flagged_abnormal": l.flagged_abnormal,
                   "test_date": str(l.test_date)} for l in labs],
        "prescriptions": [{"drug_name": r.drug_name, "dosage": r.dosage, "frequency": r.frequency,
                           "prescribed_date": str(r.prescribed_date), "prescribing_doctor": r.prescribing_doctor}
                          for r in rxs],
        "claims": [{"procedure_code": c.procedure_code, "claim_amount": c.claim_amount,
                     "claim_status": c.claim_status, "claim_date": str(c.claim_date)} for c in claims],
        "risk_flags": [{"flag_type": f.flag_type, "severity": f.severity, "description": f.description}
                       for f in flags],
    }

    report_id = str(uuid.uuid4())
    report = Report(
        id=report_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        status="draft",
        generated_at=datetime.utcnow(),
    )
    db_session.add(report)
    await db_session.flush()

    for idx, section in enumerate(sections):
        block_type = section["block_type"]
        if idx > 0:
            time.sleep(1)
        gen_result = generate_section(block_type, patient_data, [])
        block = ReportBlock(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            report_id=report_id,
            block_type=block_type,
            order_index=idx,
            content=gen_result.get("content", ""),
            ai_generated=True,
            edited_by_user=False,
            source_record_ids=gen_result.get("source_record_ids", []),
        )
        db_session.add(block)

    await db_session.commit()
    emit_event(tenant_id, patient_id, trigger_node, "completed",
               f"report {report_id} with {len(sections)} sections")
    return report_id

# Rate limit spacing between LLM calls
