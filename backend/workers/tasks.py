"""Celery tasks for document processing pipeline."""
import os
import json
import logging
import uuid
import cloudinary
from datetime import datetime
from workers.celery_app import celery_app
from config import get_settings
from models.database import get_sync_session_factory
from models.models import (
    Document, LabResult, Prescription, Claim,
    DocumentChunk, ProcessingTrace, ManualReviewQueue, RiskFlag,
)
from pipeline.graph import build_graph
from pipeline.state import GraphState
from utils.parsers import parse_document
from core.events import emit_event
from sqlalchemy import select, delete as sqldelete

logger = logging.getLogger(__name__)
settings = get_settings()

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


@celery_app.task(bind=True, max_retries=2, default_retry_delay=10)
def process_document(self, document_id: str, tenant_id: str, patient_id: str):
    session = get_sync_session_factory()()
    try:
        doc = session.execute(
            select(Document).where(Document.id == document_id)
        ).scalar_one_or_none()
        if not doc:
            logger.error(f"Document {document_id} not found")
            return
        if doc.upload_status == "processed":
            logger.info(f"Document {document_id} already processed, skipping")
            return

        doc.upload_status = "processing"
        session.commit()

        file_path = doc.raw_storage_path
        # If file_path is a Cloudinary public_id, download to temp file
        if not os.path.exists(file_path):
            import cloudinary.utils as cu
            import requests, tempfile
            from config import get_settings
            _s = get_settings()
            cloudinary.config(
                cloud_name=_s.CLOUDINARY_CLOUD_NAME,
                api_key=_s.CLOUDINARY_API_KEY,
                api_secret=_s.CLOUDINARY_API_SECRET,
            )
            # Try signed URL first, fallback to unsigned
            url, _ = cu.cloudinary_url(file_path, resource_type="raw", sign_url=True, secure=True)
            resp = requests.get(url)
            if resp.status_code != 200:
                url, _ = cu.cloudinary_url(file_path, resource_type="raw", sign_url=False, secure=True)
                resp = requests.get(url)
            resp.raise_for_status()
            suffix = os.path.splitext(doc.source_filename)[1] if doc.source_filename else ".pdf"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(resp.content)
            tmp.close()
            file_path = tmp.name

        raw_text = parse_document(file_path)

        if not raw_text:
            raise ValueError("Failed to extract text from document")

        graph = get_graph()
        initial_state = GraphState(
            document_id=document_id,
            tenant_id=tenant_id,
            patient_id=patient_id,
            raw_text=raw_text,
            source_filename=doc.source_filename,
        )

        final_state = graph.invoke(initial_state)

        _persist_data(session, final_state, document_id, tenant_id, patient_id)

        for trace in final_state.get("trace_log", []):
            db_trace = ProcessingTrace(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                document_id=document_id,
                stage=trace["stage"],
                output_summary=trace.get("output_summary", ""),
                latency_ms=trace.get("latency_ms", 0),
            )
            session.add(db_trace)

        doc.upload_status = "processed"
        doc.doc_type = final_state.get("doc_type", "unknown")
        session.commit()

        # Trigger risk flagging on full patient record
        _run_risk_flagging_sync(tenant_id, patient_id, document_id)

        logger.info(f"Document {document_id} processed successfully")

    except Exception as e:
        logger.error(f"Processing failed for {document_id}: {e}")
        doc.upload_status = "failed"
        session.commit()
        emit_event(tenant_id, document_id, "pipeline", "failed", str(e))
        raise
    finally:
        session.close()


def _persist_data(session, state, document_id, tenant_id, patient_id):
    extraction = state.get("extraction_result")
    if not extraction:
        if state.get("doc_type") == "needs_manual_review":
            session.add(ManualReviewQueue(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                document_id=document_id,
                reason=state.get("manual_review_reason", "Unknown classification"),
            ))
            session.commit()
        return

    session.execute(sqldelete(LabResult).where(LabResult.document_id == document_id))
    session.execute(sqldelete(Prescription).where(Prescription.document_id == document_id))
    session.execute(sqldelete(Claim).where(Claim.document_id == document_id))
    session.execute(sqldelete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    session.commit()

    extraction_dict = extraction
    if hasattr(extraction, "model_dump"):
        extraction_dict = extraction.model_dump()

    for lr in extraction_dict.get("lab_results", []):
        test_date = None
        if lr.get("test_date"):
            try:
                test_date = datetime.fromisoformat(lr["test_date"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        session.add(LabResult(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            patient_id=patient_id,
            document_id=document_id,
            test_name=lr["test_name"],
            value=lr.get("value"),
            unit=lr.get("unit"),
            reference_range=lr.get("reference_range"),
            flagged_abnormal=lr.get("flagged_abnormal", False),
            test_date=test_date,
        ))

    for rx in extraction_dict.get("prescriptions", []):
        prescribed_date = None
        if rx.get("prescribed_date") or rx.get("start_date"):
            date_str = rx.get("prescribed_date") or rx.get("start_date")
            try:
                prescribed_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        session.add(Prescription(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            patient_id=patient_id,
            document_id=document_id,
            drug_name=rx["drug_name"],
            dosage=rx.get("dosage"),
            frequency=rx.get("frequency"),
            prescribed_date=prescribed_date,
            prescribing_doctor=rx.get("prescribing_doctor"),
        ))

    for cl in extraction_dict.get("claims", []):
        claim_date = None
        if cl.get("claim_date"):
            try:
                claim_date = datetime.fromisoformat(cl["claim_date"].replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass

        session.add(Claim(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            patient_id=patient_id,
            document_id=document_id,
            procedure_code=cl.get("procedure_code"),
            claim_amount=cl.get("claim_amount"),
            claim_status=cl.get("claim_status"),
            claim_date=claim_date,
        ))

    clinical_notes = extraction_dict.get("clinical_notes", [])
    if clinical_notes:
        doc = session.execute(
            select(Document).where(Document.id == document_id)
        ).scalar_one_or_none()
        if doc:
            doc.clinical_notes = clinical_notes

    chunks_data = state.get("chunks_data", [])
    for chunk in chunks_data:
        session.add(DocumentChunk(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            patient_id=patient_id,
            document_id=document_id,
            chunk_text=chunk["text"],
            embedding=chunk["embedding"],
        ))

    if state.get("doc_type") == "needs_manual_review":
        session.add(ManualReviewQueue(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            document_id=document_id,
            reason=state.get("manual_review_reason", "Unknown classification"),
        ))

    session.commit()


def _run_risk_flagging_sync(tenant_id: str, patient_id: str, document_id: str):
    try:
        session = get_sync_session_factory()()
        try:
            from pipeline.agents.risk_flagger import check_interaction_rules

            labs = session.execute(
                select(LabResult).where(
                    LabResult.tenant_id == tenant_id, LabResult.patient_id == patient_id
                )
            ).scalars().all()

            rxs = session.execute(
                select(Prescription).where(
                    Prescription.tenant_id == tenant_id, Prescription.patient_id == patient_id
                )
            ).scalars().all()

            existing_flags = session.execute(
                select(RiskFlag).where(
                    RiskFlag.tenant_id == tenant_id,
                    RiskFlag.patient_id == patient_id,
                    RiskFlag.status == "open",
                )
            ).scalars().all()

            lab_data = [
                {"id": l.id, "test_name": l.test_name, "value": l.value,
                 "unit": l.unit, "reference_range": l.reference_range}
                for l in labs
            ]
            drug_data = [r.drug_name for r in rxs]

            rule_flags = check_interaction_rules(drug_data, lab_data)
            existing_flag_types = [f.flag_type for f in existing_flags]

            for flag in rule_flags:
                if flag["flag_type"] in existing_flag_types:
                    continue
                session.add(RiskFlag(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    patient_id=patient_id,
                    flag_type=flag["flag_type"],
                    severity=flag.get("severity", "medium"),
                    description=flag.get("description", ""),
                    source_record_ids=flag.get("source_record_ids", []),
                    source="rule_match",
                    status="open",
                ))

            session.commit()
            emit_event(tenant_id, document_id, "risk_flagging", "completed",
                       f"checked {len(rule_flags)} rule flags")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Risk flagging failed: {e}")
        emit_event(tenant_id, document_id, "risk_flagging", "failed", str(e))


@celery_app.task(bind=True, max_retries=1, default_retry_delay=30)
def generate_report_task(self, tenant_id: str, patient_id: str):
    """Background task for report generation — runs outside the HTTP request."""
    session = get_sync_session_factory()()
    try:
        from pipeline.agents.report_generator import generate_report_sync
        report_id = generate_report_sync(tenant_id, patient_id, session)
        if report_id:
            logger.info(f"Report {report_id} generated for patient {patient_id}")
        else:
            logger.warning(f"Report generation returned None for patient {patient_id}")
    except Exception as e:
        logger.error(f"Report generation failed for patient {patient_id}: {e}")
        emit_event(tenant_id, patient_id, "report_generation", "failed", str(e))
    finally:
        session.close()
