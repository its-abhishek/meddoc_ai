"""FastAPI application — MedDocs AI backend with agentic pipeline, reports, trends, risk flags, export."""
import os
import io
import uuid
import json
import logging
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete as sqldelete, text
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import DataError
from pydantic import BaseModel
from fpdf import FPDF

from config import get_settings
from models.database import get_db, init_db
from models.models import (
    Tenant, Patient, Document, LabResult, Prescription, Claim,
    DocumentChunk, ManualReviewQueue, AuditLog, RiskFlag,
    Report, ReportBlock, Notification, User,
)
from workers.tasks import process_document
from core.embeddings import embed_text
from core.llm_client import call_llm, call_llm_structured
from core.events import emit_event
from pipeline.agents.risk_flagger import risk_flagging_agent, check_interaction_rules
from pipeline.agents.query_agent import multi_step_query
from pipeline.agents.report_generator import generate_report, should_generate_report, has_stale_report

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="MedDocs AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if "UUID" in str(exc) and "invalid" in str(exc).lower():
        return JSONResponse(status_code=404, content={"detail": "Resource not found"})
    raise exc

@app.on_event("startup")
async def startup():
    await init_db()
    os.makedirs(settings.STORAGE_PATH, exist_ok=True)


# ── Pydantic schemas ──────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str

class PatientCreate(BaseModel):
    name: str
    external_ref: Optional[str] = None
    dob: Optional[str] = None

class QueryRequest(BaseModel):
    question: str

class SummaryResponse(BaseModel):
    summary: str
    patient_name: str
    lab_count: int
    prescription_count: int
    claim_count: int

class ReportBlockUpdate(BaseModel):
    content: str

class CustomNoteBlock(BaseModel):
    content: str
    block_type: str = "custom_note"

class RiskFlagAction(BaseModel):
    action: str
    reason: Optional[str] = None

class SignupRequest(BaseModel):
    tenant_name: str
    user_email: str


# ── Tenant endpoints ───────────────────────────────────────────

@app.post("/api/tenants")
async def create_tenant(data: TenantCreate, db: AsyncSession = Depends(get_db)):
    tenant = Tenant(id=str(uuid.uuid4()), name=data.name)
    db.add(tenant)
    await db.flush()
    return {"id": tenant.id, "name": tenant.name, "created_at": str(tenant.created_at)}


@app.post("/api/tenants/signup")
async def signup_tenant(data: SignupRequest, db: AsyncSession = Depends(get_db)):
    tenant = Tenant(id=str(uuid.uuid4()), name=data.tenant_name)
    db.add(tenant)
    await db.flush()
    user = User(
        id=str(uuid.uuid4()),
        tenant_id=tenant.id,
        email=data.user_email,
    )
    db.add(user)
    await db.flush()
    return {"tenant_id": tenant.id, "user_id": user.id, "name": tenant.name}


@app.get("/api/tenants/{tenant_id}")
async def get_tenant(tenant_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(404, "Tenant not found")
    return {"id": tenant.id, "name": tenant.name}


@app.get("/api/tenants/{tenant_id}/dashboard")
async def tenant_dashboard(tenant_id: str, db: AsyncSession = Depends(get_db)):
    doc_count = await db.execute(
        select(func.count()).select_from(Document).where(Document.tenant_id == tenant_id)
    )
    total_docs = doc_count.scalar()

    status_result = await db.execute(
        select(Document.upload_status, func.count()).where(
            Document.tenant_id == tenant_id
        ).group_by(Document.upload_status)
    )
    status_breakdown = {row[0]: row[1] for row in status_result.fetchall()}

    patient_count = await db.execute(
        select(func.count()).select_from(Patient).where(Patient.tenant_id == tenant_id)
    )
    total_patients = patient_count.scalar()

    return {
        "total_documents": total_docs,
        "status_breakdown": status_breakdown,
        "total_patients": total_patients,
    }


# ── Patient endpoints ──────────────────────────────────────────

@app.post("/api/tenants/{tenant_id}/patients")
async def create_patient(tenant_id: str, data: PatientCreate, db: AsyncSession = Depends(get_db)):
    patient = Patient(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=data.name,
        external_ref=data.external_ref,
    )
    if data.dob:
        try:
            patient.dob = datetime.fromisoformat(data.dob)
        except ValueError:
            pass
    db.add(patient)
    await db.flush()
    return {"id": patient.id, "name": patient.name}


@app.get("/api/tenants/{tenant_id}/patients")
async def list_patients(tenant_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Patient).where(Patient.tenant_id == tenant_id)
    )
    patients = result.scalars().all()
    return [{"id": p.id, "name": p.name, "external_ref": p.external_ref} for p in patients]


@app.get("/api/tenants/{tenant_id}/patients/{patient_id}")
async def get_patient(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.tenant_id == tenant_id)
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")
    return {
        "id": patient.id, "name": patient.name,
        "external_ref": patient.external_ref, "dob": str(patient.dob) if patient.dob else None,
    }


# ── Document upload ────────────────────────────────────────────

@app.post("/api/tenants/{tenant_id}/patients/{patient_id}/documents")
async def upload_document(
    tenant_id: str,
    patient_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    allowed_types = {".pdf", ".csv"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_types:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {allowed_types}")

    content = await file.read()
    max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(400, f"File too large: {len(content)} bytes. Max: {max_size}")

    doc_id = str(uuid.uuid4())
    tenant_dir = os.path.join(settings.STORAGE_PATH, tenant_id)
    os.makedirs(tenant_dir, exist_ok=True)
    file_path = os.path.join(tenant_dir, f"{doc_id}{ext}")

    with open(file_path, "wb") as f:
        f.write(content)

    doc = Document(
        id=doc_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        source_filename=file.filename,
        upload_status="queued",
        raw_storage_path=file_path,
    )
    db.add(doc)
    await db.flush()

    emit_event(tenant_id, doc_id, "pipeline", "started", f"uploaded {file.filename}")

    process_document.delay(doc_id, tenant_id, patient_id)

    return {"document_id": doc_id, "status": "queued", "filename": file.filename}


@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/documents")
async def list_documents(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.patient_id == patient_id,
        ).order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()
    return [
        {
            "id": d.id, "filename": d.source_filename,
            "doc_type": d.doc_type, "status": d.upload_status,
            "created_at": str(d.created_at),
        }
        for d in docs
    ]


@app.get("/api/tenants/{tenant_id}/documents/{document_id}")
async def get_document(tenant_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    return {
        "id": doc.id, "filename": doc.source_filename,
        "doc_type": doc.doc_type, "status": doc.upload_status,
        "created_at": str(doc.created_at),
    }


# ── Structured data endpoints ─────────────────────────────────

@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/lab-results")
async def get_lab_results(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    _log_audit(tenant_id, None, patient_id, "read_lab_results", db)
    result = await db.execute(
        select(LabResult).where(
            LabResult.tenant_id == tenant_id,
            LabResult.patient_id == patient_id,
        ).order_by(LabResult.test_date.desc())
    )
    labs = result.scalars().all()
    return [
        {
            "id": l.id, "test_name": l.test_name, "value": l.value,
            "unit": l.unit, "reference_range": l.reference_range,
            "flagged_abnormal": l.flagged_abnormal, "test_date": str(l.test_date) if l.test_date else None,
        }
        for l in labs
    ]


@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/prescriptions")
async def get_prescriptions(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    _log_audit(tenant_id, None, patient_id, "read_prescriptions", db)
    result = await db.execute(
        select(Prescription).where(
            Prescription.tenant_id == tenant_id,
            Prescription.patient_id == patient_id,
        ).order_by(Prescription.prescribed_date.desc())
    )
    rxs = result.scalars().all()
    return [
        {
            "id": r.id, "drug_name": r.drug_name, "dosage": r.dosage,
            "frequency": r.frequency, "prescribed_date": str(r.prescribed_date) if r.prescribed_date else None,
            "prescribing_doctor": r.prescribing_doctor,
        }
        for r in rxs
    ]


@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/claims")
async def get_claims(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    _log_audit(tenant_id, None, patient_id, "read_claims", db)
    result = await db.execute(
        select(Claim).where(
            Claim.tenant_id == tenant_id,
            Claim.patient_id == patient_id,
        ).order_by(Claim.claim_date.desc())
    )
    claims = result.scalars().all()
    return [
        {
            "id": c.id, "procedure_code": c.procedure_code,
            "claim_amount": c.claim_amount, "claim_status": c.claim_status,
            "claim_date": str(c.claim_date) if c.claim_date else None,
        }
        for c in claims
    ]


# ── Phase 2: Trends ────────────────────────────────────────────

@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/trends/{test_name}")
async def get_lab_trends(tenant_id: str, patient_id: str, test_name: str, db: AsyncSession = Depends(get_db)):
    _log_audit(tenant_id, None, patient_id, f"read_trends:{test_name}", db)
    result = await db.execute(
        select(LabResult).where(
            LabResult.tenant_id == tenant_id,
            LabResult.patient_id == patient_id,
            LabResult.test_name.ilike(f"%{test_name}%"),
        ).order_by(LabResult.test_date.asc())
    )
    labs = result.scalars().all()

    if not labs:
        raise HTTPException(404, f"No lab results found for test: {test_name}")

    time_series = [
        {
            "date": str(l.test_date) if l.test_date else None,
            "value": l.value,
            "unit": l.unit,
            "reference_range": l.reference_range,
            "flagged_abnormal": l.flagged_abnormal,
        }
        for l in labs
    ]

    values = [l.value for l in labs if l.value is not None]
    trend_direction = "stable"
    if len(values) >= 3:
        if values[-1] > values[0] * 1.1:
            trend_direction = "increasing"
        elif values[-1] < values[0] * 0.9:
            trend_direction = "decreasing"

    commentary_prompt = (
        f"Lab test: {test_name}\n"
        f"Values over time: {json.dumps(time_series, indent=2)}\n"
        f"Trend direction: {trend_direction}\n\n"
        "Provide a brief one-sentence clinical commentary on this trend. "
        "This is AI-generated commentary — always verify with a clinician."
    )
    commentary = call_llm(prompt=commentary_prompt, system_prompt="You are a clinical data commentator.")

    return {
        "test_name": labs[0].test_name,
        "unit": labs[0].unit,
        "trend_direction": trend_direction,
        "time_series": time_series,
        "commentary": commentary,
    }


# ── Phase 2: Risk Flags ────────────────────────────────────────

@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/risk-flags")
async def get_risk_flags(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(RiskFlag).where(
            RiskFlag.tenant_id == tenant_id,
            RiskFlag.patient_id == patient_id,
        ).order_by(RiskFlag.created_at.desc())
    )
    flags = result.scalars().all()
    return [
        {
            "id": f.id, "flag_type": f.flag_type, "severity": f.severity,
            "description": f.description, "source": f.source,
            "status": f.status, "created_at": str(f.created_at),
        }
        for f in flags
    ]


@app.post("/api/tenants/{tenant_id}/patients/{patient_id}/risk-flags/{flag_id}/action")
async def risk_flag_action(
    tenant_id: str, patient_id: str, flag_id: str,
    data: RiskFlagAction, db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(RiskFlag).where(
            RiskFlag.id == flag_id,
            RiskFlag.tenant_id == tenant_id,
            RiskFlag.patient_id == patient_id,
        )
    )
    flag = result.scalar_one_or_none()
    if not flag:
        raise HTTPException(404, "Risk flag not found")

    if data.action == "dismiss":
        if not data.reason:
            raise HTTPException(400, "Dismissal reason is required")
        flag.status = "dismissed"
    elif data.action == "acknowledge":
        flag.status = "acknowledged"
    else:
        raise HTTPException(400, f"Unknown action: {data.action}")
    await db.flush()
    return {"id": flag.id, "status": flag.status}


# ── RAG Query endpoint (multi-step) ────────────────────────────

@app.post("/api/tenants/{tenant_id}/patients/{patient_id}/query")
async def query_patient(
    tenant_id: str,
    patient_id: str,
    req: QueryRequest,
    db: AsyncSession = Depends(get_db),
):
    _log_audit(tenant_id, None, patient_id, "query", db)
    result = await multi_step_query(
        question=req.question,
        tenant_id=tenant_id,
        patient_id=patient_id,
        db_session=db,
    )
    return result


# ── Summary endpoint ───────────────────────────────────────────

@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/summary")
async def get_summary(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    _log_audit(tenant_id, None, patient_id, "read_summary", db)
    patient_result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.tenant_id == tenant_id)
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    lab_result = await db.execute(
        select(LabResult).where(LabResult.tenant_id == tenant_id, LabResult.patient_id == patient_id)
    )
    labs = lab_result.scalars().all()

    rx_result = await db.execute(
        select(Prescription).where(Prescription.tenant_id == tenant_id, Prescription.patient_id == patient_id)
    )
    rxs = rx_result.scalars().all()

    claim_result = await db.execute(
        select(Claim).where(Claim.tenant_id == tenant_id, Claim.patient_id == patient_id)
    )
    claims = claim_result.scalars().all()

    flag_result = await db.execute(
        select(RiskFlag).where(
            RiskFlag.tenant_id == tenant_id,
            RiskFlag.patient_id == patient_id,
            RiskFlag.status == "open",
        ).order_by(
            func.array_position(["high", "medium", "low"], RiskFlag.severity)
        )
    )
    flags = flag_result.scalars().all()

    data_parts = []
    if labs:
        lab_text = "\n".join(
            f"- {l.test_name}: {l.value} {l.unit} (ref: {l.reference_range}, abnormal: {l.flagged_abnormal})"
            for l in labs
        )
        data_parts.append(f"Lab Results:\n{lab_text}")
    if rxs:
        rx_text = "\n".join(
            f"- {r.drug_name} {r.dosage} {r.frequency} (prescribed: {r.prescribed_date})"
            for r in rxs
        )
        data_parts.append(f"Prescriptions:\n{rx_text}")
    if claims:
        claim_text = "\n".join(
            f"- {c.procedure_code}: ${c.claim_amount} ({c.claim_status})"
            for c in claims
        )
        data_parts.append(f"Insurance Claims:\n{claim_text}")
    if flags:
        flag_text = "\n".join(
            f"- [{f.severity.upper()}] {f.flag_type}: {f.description[:100]}"
            for f in flags
        )
        data_parts.append(f"Open Risk Flags:\n{flag_text}")

    if not data_parts:
        return SummaryResponse(
            summary="No data available for this patient yet.",
            patient_name=patient.name,
            lab_count=0,
            prescription_count=0,
            claim_count=0,
        )

    context = "\n\n".join(data_parts)
    system = (
        "You are a medical document summarizer. Create a concise clinical summary from the structured data. "
        "Organize by category. Flag any abnormal values. Include open risk flags sorted by severity. "
        "This summary is for informational purposes — always recommend consulting a healthcare provider."
    )

    summary = call_llm(
        prompt=f"Patient: {patient.name}\n\nData:\n{context[:6000]}",
        system_prompt=system,
    )

    return SummaryResponse(
        summary=summary,
        patient_name=patient.name,
        lab_count=len(labs),
        prescription_count=len(rxs),
        claim_count=len(claims),
    )


# ── Report endpoints ───────────────────────────────────────────

@app.post("/api/tenants/{tenant_id}/patients/{patient_id}/reports/generate")
async def generate_report_manual(
    tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db),
):
    report_id = await generate_report(tenant_id, patient_id, db)
    if not report_id:
        raise HTTPException(500, "Failed to generate report")
    return {"report_id": report_id, "status": "draft"}


@app.get("/api/tenants/{tenant_id}/reports/{report_id}")
async def get_report(tenant_id: str, report_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Report).where(Report.id == report_id, Report.tenant_id == tenant_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")

    blocks_result = await db.execute(
        select(ReportBlock).where(
            ReportBlock.report_id == report_id,
            ReportBlock.tenant_id == tenant_id,
        ).order_by(ReportBlock.order_index)
    )
    blocks = blocks_result.scalars().all()

    return {
        "id": report.id,
        "patient_id": report.patient_id,
        "status": report.status,
        "generated_at": str(report.generated_at),
        "last_edited_at": str(report.last_edited_at) if report.last_edited_at else None,
        "blocks": [
            {
                "id": b.id,
                "block_type": b.block_type,
                "order_index": b.order_index,
                "content": b.content,
                "ai_generated": b.ai_generated,
                "edited_by_user": b.edited_by_user,
            }
            for b in blocks
        ],
    }


@app.patch("/api/tenants/{tenant_id}/reports/{report_id}/blocks/{block_id}")
async def update_report_block(
    tenant_id: str, report_id: str, block_id: str,
    data: ReportBlockUpdate, db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ReportBlock).where(
            ReportBlock.id == block_id,
            ReportBlock.report_id == report_id,
            ReportBlock.tenant_id == tenant_id,
        )
    )
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(404, "Report block not found")

    block.content = data.content
    block.edited_by_user = True

    report_result = await db.execute(
        select(Report).where(Report.id == report_id, Report.tenant_id == tenant_id)
    )
    report = report_result.scalar_one_or_none()
    if report:
        report.last_edited_at = datetime.utcnow()

    await db.flush()
    return {
        "id": block.id, "block_type": block.block_type,
        "content": block.content, "edited_by_user": True,
    }


@app.post("/api/tenants/{tenant_id}/reports/{report_id}/blocks/{block_id}/regenerate")
async def regenerate_block(
    tenant_id: str, report_id: str, block_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ReportBlock).where(
            ReportBlock.id == block_id,
            ReportBlock.report_id == report_id,
            ReportBlock.tenant_id == tenant_id,
        )
    )
    block = result.scalar_one_or_none()
    if not block:
        raise HTTPException(404, "Report block not found")

    from pipeline.agents.report_generator import generate_section

    report_result = await db.execute(
        select(Report).where(Report.id == report_id, Report.tenant_id == tenant_id)
    )
    report = report_result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")

    patient_data = await _build_patient_data(tenant_id, report.patient_id, db)
    gen_result = generate_section(block.block_type, patient_data, [])
    block.content = gen_result.get("content", "")
    block.ai_generated = True
    block.edited_by_user = False
    await db.flush()

    return {
        "id": block.id, "block_type": block.block_type,
        "content": block.content, "ai_generated": True,
    }


@app.post("/api/tenants/{tenant_id}/reports/{report_id}/blocks")
async def add_custom_block(
    tenant_id: str, report_id: str,
    data: CustomNoteBlock, db: AsyncSession = Depends(get_db),
):
    report_result = await db.execute(
        select(Report).where(Report.id == report_id, Report.tenant_id == tenant_id)
    )
    report = report_result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")

    max_order = await db.execute(
        select(func.max(ReportBlock.order_index)).where(
            ReportBlock.report_id == report_id,
            ReportBlock.tenant_id == tenant_id,
        )
    )
    next_index = (max_order.scalar() or 0) + 1

    block = ReportBlock(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        report_id=report_id,
        block_type=data.block_type,
        order_index=next_index,
        content=data.content,
        ai_generated=False,
        edited_by_user=True,
    )
    db.add(block)
    await db.flush()

    return {
        "id": block.id, "block_type": block.block_type,
        "content": block.content, "order_index": next_index,
    }


@app.post("/api/tenants/{tenant_id}/reports/{report_id}/finalize")
async def finalize_report(tenant_id: str, report_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Report).where(Report.id == report_id, Report.tenant_id == tenant_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")
    report.status = "finalized"
    report.last_edited_at = datetime.utcnow()
    await db.flush()
    return {"id": report.id, "status": "finalized"}


def _pdf_safe(text: str) -> str:
    """Replace Unicode chars unsupported by Helvetica with ASCII equivalents."""
    replacements = {
        "\u2014": "--", "\u2013": "-", "\u201c": '"', "\u201d": '"',
        "\u2018": "'", "\u2019": "'", "\u2026": "...", "\u2022": "*",
        "\u00a0": " ", "\u2032": "'", "\u2033": '"',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", errors="replace").decode("latin-1")


@app.get("/api/tenants/{tenant_id}/reports/{report_id}/pdf")
async def download_report_pdf(tenant_id: str, report_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Report).where(Report.id == report_id, Report.tenant_id == tenant_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(404, "Report not found")

    patient_result = await db.execute(
        select(Patient).where(Patient.id == report.patient_id, Patient.tenant_id == tenant_id)
    )
    patient = patient_result.scalar_one_or_none()

    blocks_result = await db.execute(
        select(ReportBlock).where(
            ReportBlock.report_id == report_id,
            ReportBlock.tenant_id == tenant_id,
        ).order_by(ReportBlock.order_index)
    )
    blocks = blocks_result.scalars().all()

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.cell(0, 12, "MedDocs AI Clinical Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(100, 100, 100)
    if patient:
        pdf.cell(0, 6, _pdf_safe(f"Patient: {patient.name}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _pdf_safe(f"Report ID: {report_id}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _pdf_safe(f"Generated: {report.generated_at}"), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, _pdf_safe(f"Status: {report.status}"), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(6)

    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(6)

    for block in blocks:
        title = block.block_type.replace("_", " ").title()
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _pdf_safe(title), new_x="LMARGIN", new_y="NEXT")

        if block.ai_generated and not block.edited_by_user:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(59, 130, 246)
            pdf.cell(0, 5, "[AI Generated]", new_x="LMARGIN", new_y="NEXT")
        elif block.edited_by_user:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(34, 197, 94)
            pdf.cell(0, 5, "[Edited by User]", new_x="LMARGIN", new_y="NEXT")

        pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 6, _pdf_safe(block.content))
        pdf.ln(4)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 6, _pdf_safe("Generated by MedDocs AI - For informational purposes only. Consult a healthcare provider."),
             new_x="LMARGIN", new_y="NEXT", align="C")

    pdf_buffer = io.BytesIO()
    pdf_bytes = pdf.output()
    pdf_buffer.write(pdf_bytes)
    pdf_buffer.seek(0)

    filename = f"report_{report_id[:8]}.pdf"
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Export endpoint (Phase 3) ──────────────────────────────────

@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/export")
async def export_patient(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    _log_audit(tenant_id, None, patient_id, "export", db)

    patient_result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.tenant_id == tenant_id)
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    labs = (await db.execute(
        select(LabResult).where(LabResult.tenant_id == tenant_id, LabResult.patient_id == patient_id)
    )).scalars().all()

    rxs = (await db.execute(
        select(Prescription).where(Prescription.tenant_id == tenant_id, Prescription.patient_id == patient_id)
    )).scalars().all()

    claims = (await db.execute(
        select(Claim).where(Claim.tenant_id == tenant_id, Claim.patient_id == patient_id)
    )).scalars().all()

    flags = (await db.execute(
        select(RiskFlag).where(RiskFlag.tenant_id == tenant_id, RiskFlag.patient_id == patient_id)
    )).scalars().all()

    export_data = {
        "patient": {"name": patient.name, "id": patient.id, "dob": str(patient.dob) if patient.dob else None},
        "lab_results": [{"test_name": l.test_name, "value": l.value, "unit": l.unit,
                         "reference_range": l.reference_range, "flagged_abnormal": l.flagged_abnormal,
                         "test_date": str(l.test_date)} for l in labs],
        "prescriptions": [{"drug_name": r.drug_name, "dosage": r.dosage, "frequency": r.frequency,
                           "prescribed_date": str(r.prescribed_date), "prescribing_doctor": r.prescribing_doctor}
                          for r in rxs],
        "claims": [{"procedure_code": c.procedure_code, "claim_amount": c.claim_amount,
                     "claim_status": c.claim_status, "claim_date": str(c.claim_date)} for c in claims],
        "risk_flags": [{"flag_type": f.flag_type, "severity": f.severity, "description": f.description,
                         "source": f.source, "status": f.status} for f in flags],
    }

    # Return as PDF text for now (full PDF generation deferred)
    text_lines = [
        f"=== MedDocs AI Export ===",
        f"Patient: {patient.name}",
        f"Generated: {datetime.utcnow().isoformat()}",
        "",
        "--- Lab Results ---",
    ]
    for l in labs:
        flag = " [ABNORMAL]" if l.flagged_abnormal else ""
        text_lines.append(f"  {l.test_name}: {l.value} {l.unit} (ref: {l.reference_range}){flag}")
    text_lines.extend(["", "--- Prescriptions ---"])
    for r in rxs:
        text_lines.append(f"  {r.drug_name} {r.dosage} {r.frequency}")
    text_lines.extend(["", "--- Risk Flags ---"])
    for f in flags:
        text_lines.append(f"  [{f.severity.upper()}] {f.flag_type}: {f.description[:100]}")

    return {
        "export_text": "\n".join(text_lines),
        "export_json": export_data,
        "generated_at": datetime.utcnow().isoformat(),
    }


# ── Health check ───────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── Internal helpers ───────────────────────────────────────────

async def _log_audit(tenant_id, user_id, patient_id, action, db):
    try:
        audit = AuditLog(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            user_id=user_id,
            patient_id=patient_id,
            action=action,
        )
        db.add(audit)
    except Exception as e:
        logger.warning(f"Audit log failed: {e}")


async def _build_patient_data(tenant_id: str, patient_id: str, db) -> dict:
    patient_result = await db.execute(
        select(Patient).where(Patient.id == patient_id, Patient.tenant_id == tenant_id)
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        return {}

    labs = (await db.execute(
        select(LabResult).where(LabResult.tenant_id == tenant_id, LabResult.patient_id == patient_id)
    )).scalars().all()

    rxs = (await db.execute(
        select(Prescription).where(Prescription.tenant_id == tenant_id, Prescription.patient_id == patient_id)
    )).scalars().all()

    claims = (await db.execute(
        select(Claim).where(Claim.tenant_id == tenant_id, Claim.patient_id == patient_id)
    )).scalars().all()

    flags = (await db.execute(
        select(RiskFlag).where(RiskFlag.tenant_id == tenant_id, RiskFlag.patient_id == patient_id)
    )).scalars().all()

    return {
        "name": patient.name,
        "dob": str(patient.dob) if patient.dob else "N/A",
        "id": patient.id,
        "labs": [{"test_name": l.test_name, "value": l.value, "unit": l.unit,
                   "reference_range": l.reference_range, "flagged_abnormal": l.flagged_abnormal,
                   "test_date": str(l.test_date)} for l in labs],
        "prescriptions": [{"drug_name": r.drug_name, "dosage": r.dosage, "frequency": r.frequency,
                           "prescribed_date": str(r.prescribed_date)} for r in rxs],
        "claims": [{"procedure_code": c.procedure_code, "claim_amount": c.claim_amount,
                     "claim_status": c.claim_status} for c in claims],
        "risk_flags": [{"flag_type": f.flag_type, "severity": f.severity, "description": f.description}
                       for f in flags],
    }
