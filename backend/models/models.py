"""SQLAlchemy models — all tables have tenant_id."""
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, DateTime, Text, Float, Boolean, Integer, ForeignKey, JSON
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from pgvector.sqlalchemy import Vector
from models.database import Base


def gen_uuid():
    return str(uuid.uuid4())


def utcnow():
    return datetime.utcnow()


class Tenant(Base):
    __tablename__ = "tenants"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=utcnow)


class User(Base):
    __tablename__ = "users"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    email = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, default=utcnow)


class Patient(Base):
    __tablename__ = "patients"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    external_ref = Column(String(255))
    name = Column(String(255), nullable=False)
    dob = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class Document(Base):
    __tablename__ = "documents"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False, index=True)
    doc_type = Column(String(50))
    source_filename = Column(String(500), nullable=False)
    upload_status = Column(String(50), default="queued")
    raw_storage_path = Column(String(1000))
    clinical_notes = Column(JSONB, default=list)
    created_at = Column(DateTime, default=utcnow)


class LabResult(Base):
    __tablename__ = "lab_results"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=False, index=True)
    test_name = Column(String(255), nullable=False)
    value = Column(Float)
    unit = Column(String(50))
    reference_range = Column(String(100))
    flagged_abnormal = Column(Boolean, default=False)
    test_date = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class Prescription(Base):
    __tablename__ = "prescriptions"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=False, index=True)
    drug_name = Column(String(255), nullable=False)
    dosage = Column(String(100))
    frequency = Column(String(100))
    prescribed_date = Column(DateTime)
    prescribing_doctor = Column(String(255))
    created_at = Column(DateTime, default=utcnow)


class Claim(Base):
    __tablename__ = "claims"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=False, index=True)
    procedure_code = Column(String(50))
    claim_amount = Column(Float)
    claim_status = Column(String(50))
    claim_date = Column(DateTime)
    created_at = Column(DateTime, default=utcnow)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=False, index=True)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(Vector(384))
    created_at = Column(DateTime, default=utcnow)


class ProcessingTrace(Base):
    __tablename__ = "processing_traces"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=False, index=True)
    stage = Column(String(100), nullable=False)
    input_summary = Column(Text)
    output_summary = Column(Text)
    latency_ms = Column(Integer)
    created_at = Column(DateTime, default=utcnow)


class ManualReviewQueue(Base):
    __tablename__ = "manual_review_queue"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)


# Phase 2 models
class RiskFlag(Base):
    __tablename__ = "risk_flags"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False, index=True)
    flag_type = Column(String(100), nullable=False)
    severity = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    source_record_ids = Column(JSONB)
    source = Column(String(50))
    status = Column(String(50), default="open")
    created_at = Column(DateTime, default=utcnow)


# Phase 3 models
class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"))
    action = Column(String(255), nullable=False)
    timestamp = Column(DateTime, default=utcnow)


# Report models
class Report(Base):
    __tablename__ = "reports"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=False), ForeignKey("patients.id"), nullable=False, index=True)
    status = Column(String(50), default="draft")
    generated_at = Column(DateTime, default=utcnow)
    last_edited_at = Column(DateTime)


class ReportBlock(Base):
    __tablename__ = "report_blocks"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    report_id = Column(UUID(as_uuid=False), ForeignKey("reports.id"), nullable=False, index=True)
    block_type = Column(String(50), nullable=False)
    order_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    ai_generated = Column(Boolean, default=True)
    edited_by_user = Column(Boolean, default=False)
    source_record_ids = Column(JSONB)


# Phase 1B monitoring models
class MonitoringEvent(Base):
    __tablename__ = "monitoring_events"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"), nullable=False, index=True)
    node = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)
    detail = Column(Text)
    timestamp = Column(DateTime, default=utcnow)


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(UUID(as_uuid=False), primary_key=True, default=gen_uuid)
    tenant_id = Column(UUID(as_uuid=False), ForeignKey("tenants.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"))
    document_id = Column(UUID(as_uuid=False), ForeignKey("documents.id"))
    message = Column(Text, nullable=False)
    read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utcnow)
