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
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
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
    Report, ReportBlock, Notification, User, ProcessingTrace, MonitoringEvent,
)

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(title="MedDocs AI", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
    from utils.cloud_storage import upload_file

    allowed_types = {".pdf", ".csv"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_types:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {allowed_types}")

    content = await file.read()
    max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(content) > max_size:
        raise HTTPException(400, f"File too large: {len(content)} bytes. Max: {max_size}")

    doc_id = str(uuid.uuid4())

    # Upload to Cloudinary
    public_id = upload_file(content, file.filename, doc_id, tenant_id)

    doc = Document(
        id=doc_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        source_filename=file.filename,
        upload_status="queued",
        raw_storage_path=public_id,
    )
    db.add(doc)
    await db.flush()

    from core.events import emit_event
    emit_event(tenant_id, doc_id, "pipeline", "started", f"uploaded {file.filename}")

    from workers.tasks import process_document
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

    lab_result = await db.execute(
        select(LabResult).where(LabResult.document_id == document_id, LabResult.tenant_id == tenant_id)
    )
    rx_result = await db.execute(
        select(Prescription).where(Prescription.document_id == document_id, Prescription.tenant_id == tenant_id)
    )
    claim_result = await db.execute(
        select(Claim).where(Claim.document_id == document_id, Claim.tenant_id == tenant_id)
    )
    chunk_result = await db.execute(
        select(DocumentChunk).where(DocumentChunk.document_id == document_id, DocumentChunk.tenant_id == tenant_id)
    )
    trace_result = await db.execute(
        select(ProcessingTrace).where(ProcessingTrace.document_id == document_id, ProcessingTrace.tenant_id == tenant_id)
    )

    chunks = chunk_result.scalars().all()

    return {
        "id": doc.id, "filename": doc.source_filename,
        "doc_type": doc.doc_type, "status": doc.upload_status,
        "created_at": str(doc.created_at),
        "patient_id": str(doc.patient_id),
        "clinical_notes": doc.clinical_notes or [],
        "lab_results": [
            {"id": l.id, "test_name": l.test_name, "value": l.value, "unit": l.unit,
             "reference_range": l.reference_range, "flagged_abnormal": l.flagged_abnormal,
             "test_date": str(l.test_date) if l.test_date else None}
            for l in lab_result.scalars().all()
        ],
        "prescriptions": [
            {"id": r.id, "drug_name": r.drug_name, "dosage": r.dosage,
             "frequency": r.frequency, "prescribed_date": str(r.prescribed_date) if r.prescribed_date else None,
             "prescribing_doctor": r.prescribing_doctor}
            for r in rx_result.scalars().all()
        ],
        "claims": [
            {"id": c.id, "procedure_code": c.procedure_code, "claim_amount": c.claim_amount,
             "claim_status": c.claim_status, "claim_date": str(c.claim_date) if c.claim_date else None}
            for c in claim_result.scalars().all()
        ],
        "chunks_count": len(chunks),
        "extracted_text": "\n\n".join(c.chunk_text for c in chunks),
        "processing_traces": [
            {"stage": t.stage, "input_summary": t.input_summary,
             "output_summary": t.output_summary, "latency_ms": t.latency_ms,
             "created_at": str(t.created_at) if t.created_at else None}
            for t in trace_result.scalars().all()
        ],
    }


@app.get("/api/tenants/{tenant_id}/documents/{document_id}/file")
async def download_document_file(tenant_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    from utils.cloud_storage import get_file_url

    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")
    if not doc.raw_storage_path:
        raise HTTPException(404, "No file associated with this document")

    url = get_file_url(doc.raw_storage_path)
    ext = os.path.splitext(doc.source_filename)[1].lower() if doc.source_filename else ".pdf"
    media_types = {".pdf": "application/pdf", ".csv": "text/csv", ".txt": "text/plain"}
    content_type = media_types.get(ext, "application/octet-stream")

    return {"url": url, "content_type": content_type}


@app.delete("/api/tenants/{tenant_id}/documents/{document_id}")
async def delete_document(tenant_id: str, document_id: str, db: AsyncSession = Depends(get_db)):
    from utils.cloud_storage import delete_file

    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.tenant_id == tenant_id)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(404, "Document not found")

    for model in [LabResult, Prescription, Claim, DocumentChunk, ProcessingTrace, ManualReviewQueue, MonitoringEvent, Notification]:
        await db.execute(
            sqldelete(model).where(model.document_id == document_id, model.tenant_id == tenant_id)
        )

    if doc.raw_storage_path:
        delete_file(doc.raw_storage_path)

    await db.delete(doc)
    await db.flush()
    await _log_audit(tenant_id, document_id, doc.patient_id, "delete_document", db)
    await db.flush()
    return {"deleted": document_id}


# ── Structured data endpoints ─────────────────────────────────

@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/lab-results")
async def get_lab_results(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    await _log_audit(tenant_id, None, patient_id, "read_lab_results", db)
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
    await _log_audit(tenant_id, None, patient_id, "read_prescriptions", db)
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
    await _log_audit(tenant_id, None, patient_id, "read_claims", db)
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
    await _log_audit(tenant_id, None, patient_id, f"read_trends:{test_name}", db)
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
    from core.llm_client import call_llm
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
    await _log_audit(tenant_id, None, patient_id, "query", db)
    from pipeline.agents.query_agent import multi_step_query
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
    await _log_audit(tenant_id, None, patient_id, "read_summary", db)
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
    from workers.tasks import generate_report_task
    generate_report_task.delay(tenant_id, patient_id)
    return {"status": "generating", "message": "Report generation started in background"}


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


@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/reports/latest")
async def get_latest_report(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Report).where(
            Report.tenant_id == tenant_id, Report.patient_id == patient_id,
        ).order_by(Report.generated_at.desc()).limit(1)
    )
    report = result.scalar_one_or_none()
    if not report:
        return None

    blocks_result = await db.execute(
        select(ReportBlock).where(
            ReportBlock.report_id == report.id, ReportBlock.tenant_id == tenant_id,
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


class ClinicalReportPDF(FPDF):
    """Custom PDF with branded header/footer."""

    def __init__(self, patient_name: str = "", report_date: str = ""):
        super().__init__()
        self.patient_name = patient_name
        self.report_date = report_date

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, _pdf_safe(f"MedDocs AI  |  {self.patient_name}  |  {self.report_date}"),
                  new_x="LMARGIN", new_y="NEXT", align="L")
        self.set_draw_color(0, 102, 204)
        self.set_line_width(0.5)
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def section_title(self, title: str):
        self.ln(4)
        self.set_fill_color(0, 102, 204)
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, f"  {_pdf_safe(title)}", new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(3)
        self.set_text_color(0, 0, 0)

    def sub_heading(self, text: str):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(60, 60, 60)
        self.cell(0, 6, _pdf_safe(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def table_header(self, cols: list, widths: list):
        self.set_font("Helvetica", "B", 8)
        self.set_fill_color(230, 240, 250)
        self.set_text_color(40, 40, 40)
        for i, col in enumerate(cols):
            self.cell(widths[i], 7, _pdf_safe(col), border=1, fill=True, align="C")
        self.ln()
        self.set_text_color(0, 0, 0)

    def table_row(self, cells: list, widths: list, aligns: list = None, fill: bool = False):
        self.set_font("Helvetica", "", 8)
        if fill:
            self.set_fill_color(248, 250, 252)
        for i, cell in enumerate(cells):
            a = (aligns[i] if aligns else "L")
            self.cell(widths[i], 6, _pdf_safe(str(cell)), border=1, fill=fill, align=a)
        self.ln()


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

    labs = (await db.execute(
        select(LabResult).where(LabResult.tenant_id == tenant_id, LabResult.patient_id == report.patient_id)
    )).scalars().all()

    rxs = (await db.execute(
        select(Prescription).where(Prescription.tenant_id == tenant_id, Prescription.patient_id == report.patient_id)
    )).scalars().all()

    flags = (await db.execute(
        select(RiskFlag).where(RiskFlag.tenant_id == tenant_id, RiskFlag.patient_id == report.patient_id)
    )).scalars().all()

    blocks = (await db.execute(
        select(ReportBlock).where(
            ReportBlock.report_id == report_id, ReportBlock.tenant_id == tenant_id,
        ).order_by(ReportBlock.order_index)
    )).scalars().all()

    doc = (await db.execute(
        select(Document).where(Document.tenant_id == tenant_id, Document.patient_id == report.patient_id)
    )).scalars().all()

    gen_date = report.generated_at.strftime("%B %d, %Y") if report.generated_at else "N/A"
    patient_name = patient.name if patient else "Unknown Patient"
    patient_dob = patient.dob.strftime("%B %d, %Y") if patient and patient.dob else "N/A"

    pdf = ClinicalReportPDF(patient_name=patient_name, report_date=gen_date)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Cover Page ──────────────────────────────────────────────
    pdf.add_page()
    pdf.ln(30)
    pdf.set_font("Helvetica", "B", 28)
    pdf.set_text_color(0, 102, 204)
    pdf.cell(0, 14, "MedDocs AI", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 10, "Clinical Patient Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(8)

    pdf.set_draw_color(0, 102, 204)
    pdf.set_line_width(0.8)
    pdf.line(60, pdf.get_y(), 150, pdf.get_y())
    pdf.ln(12)

    info_w = 100
    lx = (210 - info_w) // 2
    pdf.set_x(lx)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(info_w, 8, _pdf_safe(f"Patient: {patient_name}"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_x(lx)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(info_w, 7, _pdf_safe(f"Date of Birth: {patient_dob}"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_x(lx)
    pdf.cell(info_w, 7, _pdf_safe(f"Patient ID: {report.patient_id}"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_x(lx)
    pdf.cell(info_w, 7, _pdf_safe(f"Report Date: {gen_date}"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_x(lx)
    pdf.cell(info_w, 7, _pdf_safe(f"Report ID: {report_id}"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_x(lx)
    status_label = report.status.upper()
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 150, 0) if report.status == "finalized" else pdf.set_text_color(200, 150, 0)
    pdf.cell(info_w, 7, _pdf_safe(f"Status: {status_label}"), new_x="LMARGIN", new_y="NEXT", align="C")

    pdf.ln(15)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 6, _pdf_safe(f"Documents on file: {len(doc)}"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.cell(0, 6, _pdf_safe(f"Lab results: {len(labs)}  |  Medications: {len(rxs)}  |  Risk flags: {len([f for f in flags if f.status == 'open'])}"),
             new_x="LMARGIN", new_y="NEXT", align="C")

    # ── Table of Contents ───────────────────────────────────────
    pdf.ln(20)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 102, 204)
    pdf.cell(0, 8, "Table of Contents", new_x="LMARGIN", new_y="NEXT", align="L")
    pdf.set_draw_color(0, 102, 204)
    pdf.set_line_width(0.3)
    pdf.line(10, pdf.get_y(), 80, pdf.get_y())
    pdf.ln(4)

    toc_items = []
    if labs:
        toc_items.append("1.  Laboratory Results")
    if rxs:
        toc_items.append(f"{len(toc_items)+1}.  Medication Summary")
    if flags:
        toc_items.append(f"{len(toc_items)+1}.  Risk Flags & Clinical Alerts")
    for b in blocks:
        if b.block_type not in ("patient_overview", "lab_summary", "medication_summary", "risk_flags"):
            toc_items.append(f"{len(toc_items)+1}.  {b.block_type.replace('_', ' ').title()}")
    toc_items.append(f"{len(toc_items)+1}.  Disclaimer")

    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(40, 40, 40)
    for item in toc_items:
        pdf.cell(0, 7, _pdf_safe(item), new_x="LMARGIN", new_y="NEXT")

    # ── Lab Results Table ───────────────────────────────────────
    if labs:
        pdf.add_page()
        pdf.section_title("Laboratory Results")

        lab_widths = [52, 22, 16, 18, 28, 16, 22]
        lab_cols = ["Test Name", "Value", "Unit", "Flag", "Reference Range", "Date", "Status"]
        pdf.table_header(lab_cols, lab_widths)

        for i, lab in enumerate(labs):
            flagged = "HIGH" if lab.flagged_abnormal and lab.value else ("LOW" if lab.flagged_abnormal else "")
            flag_color = (200, 40, 40) if flagged == "HIGH" else ((40, 40, 200) if flagged == "LOW" else (0, 0, 0))
            test_date = lab.test_date.strftime("%Y-%m-%d") if lab.test_date else "N/A"
            status = "Abnormal" if lab.flagged_abnormal else "Normal"

            fill = (i % 2 == 0)
            aligns = ["L", "R", "C", "C", "C", "C", "C"]
            cells = [
                str(lab.test_name or ""),
                str(lab.value) if lab.value is not None else "",
                str(lab.unit or ""),
                flagged or "-",
                str(lab.reference_range or ""),
                test_date,
                status,
            ]
            if flagged:
                pdf.set_font("Helvetica", "B", 8)
                pdf.set_text_color(*flag_color)
                pdf.table_row(cells, lab_widths, aligns, fill)
                pdf.set_text_color(0, 0, 0)
            else:
                pdf.table_row(cells, lab_widths, aligns, fill)

        pdf.ln(3)
        abnormal_count = sum(1 for l in labs if l.flagged_abnormal)
        if abnormal_count > 0:
            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(200, 40, 40)
            pdf.cell(0, 6, _pdf_safe(f"{abnormal_count} abnormal value(s) detected -- clinical review recommended."),
                     new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)

    # ── Medications Table ───────────────────────────────────────
    if rxs:
        if pdf.get_y() > 200:
            pdf.add_page()
        pdf.section_title("Medication Summary")

        rx_widths = [40, 25, 35, 30, 30, 20]
        rx_cols = ["Drug Name", "Dosage", "Frequency", "Start Date", "Doctor", "Status"]
        pdf.table_header(rx_cols, rx_widths)

        for i, rx in enumerate(rxs):
            start = ""
            if rx.prescribed_date:
                start = rx.prescribed_date.strftime("%Y-%m-%d")
            cells = [
                str(rx.drug_name or ""),
                str(rx.dosage or ""),
                str(rx.frequency or ""),
                start or "N/A",
                str(rx.prescribing_doctor or ""),
                "Active",
            ]
            pdf.table_row(cells, rx_widths, ["L", "C", "L", "C", "L", "C"], fill=(i % 2 == 0))

    # ── Risk Flags Section ──────────────────────────────────────
    open_flags = [f for f in flags if f.status == "open"]
    if open_flags:
        if pdf.get_y() > 200:
            pdf.add_page()
        pdf.section_title("Risk Flags & Clinical Alerts")

        severity_colors = {
            "high": (200, 40, 40),
            "medium": (220, 150, 0),
            "low": (60, 150, 60),
        }

        for flag in open_flags:
            color = severity_colors.get(flag.severity, (80, 80, 80))

            pdf.set_font("Helvetica", "B", 9)
            pdf.set_text_color(*color)
            sev_label = flag.severity.upper() if flag.severity else "UNKNOWN"
            pdf.cell(30, 6, _pdf_safe(f"[{sev_label}]"), new_x="END")
            pdf.set_text_color(0, 0, 0)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(0, 6, _pdf_safe(flag.flag_type.replace("_", " ").title()), new_x="LMARGIN", new_y="NEXT")

            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(60, 60, 60)
            pdf.multi_cell(0, 5, _pdf_safe(flag.description or ""))
            pdf.ln(2)

        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(120, 120, 120)
        pdf.cell(0, 5, _pdf_safe("All flags are advisory -- review by a clinician is required."),
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)

    # ── Additional Report Blocks (trends, clinical notes, etc.) ─
    for block in blocks:
        if block.block_type in ("patient_overview", "lab_summary", "medication_summary", "risk_flags"):
            continue
        if pdf.get_y() > 230:
            pdf.add_page()
        title = block.block_type.replace("_", " ").title()
        pdf.section_title(title)
        if block.ai_generated and not block.edited_by_user:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(59, 130, 246)
            pdf.cell(0, 5, "[AI Generated]", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        elif block.edited_by_user:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(34, 197, 94)
            pdf.cell(0, 5, "[Edited by User]", new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(0, 5, _pdf_safe(block.content))
        pdf.ln(3)

    # ── Disclaimer ──────────────────────────────────────────────
    pdf.add_page()
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(0, 102, 204)
    pdf.cell(0, 8, "Disclaimer", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    disclaimer = (
        "This report was generated by MedDocs AI for informational purposes only. "
        "It is not a substitute for professional medical advice, diagnosis, or treatment. "
        "All flagged values and risk alerts are advisory and require review by a qualified "
        "healthcare provider. Do not make clinical decisions based solely on this report.\n\n"
        "Data sources: laboratory results, prescriptions, claims, and clinical notes on file "
        f"as of {gen_date}. Report ID: {report_id}."
    )
    pdf.multi_cell(0, 5, _pdf_safe(disclaimer), align="C")

    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(150, 150, 150)
    pdf.cell(0, 5, _pdf_safe("Generated by MedDocs AI"), new_x="LMARGIN", new_y="NEXT", align="C")

    pdf_buffer = io.BytesIO()
    pdf_bytes = pdf.output()
    pdf_buffer.write(pdf_bytes)
    pdf_buffer.seek(0)

    filename = f"report_{patient_name.replace(' ', '_')}_{report_id[:8]}.pdf"
    return StreamingResponse(
        pdf_buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Export endpoint (Phase 3) ──────────────────────────────────

@app.get("/api/tenants/{tenant_id}/patients/{patient_id}/export")
async def export_patient(tenant_id: str, patient_id: str, db: AsyncSession = Depends(get_db)):
    await _log_audit(tenant_id, None, patient_id, "export", db)

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
