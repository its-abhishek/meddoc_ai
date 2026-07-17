"""Graph state definition."""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class ExtractionResult(BaseModel):
    """Structured extraction output — varies by doc_type."""
    lab_results: List[Dict[str, Any]] = Field(default_factory=list)
    prescriptions: List[Dict[str, Any]] = Field(default_factory=list)
    claims: List[Dict[str, Any]] = Field(default_factory=list)
    clinical_notes: List[str] = Field(default_factory=list)


class GraphState(BaseModel):
    document_id: str
    tenant_id: str
    patient_id: str
    raw_text: str = ""
    doc_type: str = ""
    source_filename: str = ""
    extraction_result: Optional[ExtractionResult] = None
    validation_errors: List[str] = Field(default_factory=list)
    trace_log: List[Dict[str, Any]] = Field(default_factory=list)
    plan: List[str] = Field(default_factory=list)
    verification_issues: List[str] = Field(default_factory=list)
    verification_accepted: bool = False
    extraction_attempts: int = 0
    max_extraction_attempts: int = 2
    chunks_data: List[Dict[str, Any]] = Field(default_factory=list)
    manual_review_reason: str = ""

    class Config:
        arbitrary_types_allowed = True
