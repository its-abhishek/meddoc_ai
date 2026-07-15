"""Multi-step Query Agent — can do multiple retrieval rounds before answering."""
import json
import logging
from typing import List, Dict, Any, Optional
from core.llm_client import call_llm, call_llm_structured
from core.embeddings import embed_text

logger = logging.getLogger(__name__)

QUERY_SYSTEM = (
    "You are a medical document query agent. Answer questions about a patient's medical records.\n\n"
    "You have access to context from document chunks and structured data.\n"
    "Answer ONLY based on the provided context. If the context doesn't contain enough information, "
    "say 'I don't have that information available.'\n\n"
    "Be precise and cite specific values. "
    "This is for informational purposes only — always recommend consulting a healthcare provider."
)


async def multi_step_query(
    question: str,
    tenant_id: str,
    patient_id: str,
    db_session,
    max_retrieval_rounds: int = 3,
) -> Dict[str, Any]:
    """Multi-step query: agent can retrieve multiple times before answering.

    Returns: {"answer": str, "source_chunks": list, "source_records": list, "retrieval_rounds": int}
    """
    from sqlalchemy import select, text
    from models.models import LabResult, Prescription, Claim, DocumentChunk

    all_chunks = []
    all_records = []
    current_question = question
    retrieval_rounds = 0
    clarification_needed = False

    for round_num in range(max_retrieval_rounds):
        retrieval_rounds += 1

        question_embedding = embed_text(current_question)

        result = await db_session.execute(
            text("""
                SELECT chunk_text, 1 - (embedding <=> :embedding) as similarity
                FROM document_chunks
                WHERE tenant_id = :tenant_id AND patient_id = :patient_id
                ORDER BY embedding <=> :embedding
                LIMIT 5
            """),
            {"embedding": str(question_embedding), "tenant_id": tenant_id, "patient_id": patient_id},
        )
        chunks = result.fetchall()

        q_lower = current_question.lower()
        structured_context = ""

        if any(w in q_lower for w in ["medication", "drug", "prescription", "taking"]):
            rx_result = await db_session.execute(
                select(Prescription).where(
                    Prescription.tenant_id == tenant_id, Prescription.patient_id == patient_id
                )
            )
            rxs = rx_result.scalars().all()
            if rxs:
                rx_text = "\n".join(
                    f"- {r.drug_name} {r.dosage} {r.frequency} (prescribed: {r.prescribed_date})"
                    for r in rxs
                )
                structured_context += f"\n\nCurrent prescriptions:\n{rx_text}"
                all_records.extend(
                    {"type": "prescription", "drug_name": r.drug_name, "dosage": r.dosage}
                    for r in rxs
                )

        if any(w in q_lower for w in ["lab", "test", "result", "cholesterol", "glucose", "hemoglobin"]):
            lab_result = await db_session.execute(
                select(LabResult).where(
                    LabResult.tenant_id == tenant_id, LabResult.patient_id == patient_id
                )
            )
            labs = lab_result.scalars().all()
            if labs:
                lab_text = "\n".join(
                    f"- {l.test_name}: {l.value} {l.unit} (ref: {l.reference_range}, abnormal: {l.flagged_abnormal})"
                    for l in labs
                )
                structured_context += f"\n\nLab results:\n{lab_text}"
                all_records.extend(
                    {"type": "lab", "test_name": l.test_name, "value": l.value, "flagged_abnormal": l.flagged_abnormal}
                    for l in labs
                )

        chunk_texts = [c[0] for c in chunks]
        all_chunks.extend(
            {"text": c[0], "similarity": float(c[1])} for c in chunks if c[0] not in {x["text"] for x in all_chunks}
        )

        context = "\n\n".join(chunk_texts) + structured_context

        if not context.strip():
            return {
                "answer": "I don't have any information about this patient's documents yet.",
                "source_chunks": [],
                "source_records": [],
                "retrieval_rounds": retrieval_rounds,
            }

        if round_num == 0:
            system_check = (
                "Determine: do you have enough information to answer the question?\n\n"
                "Return JSON: {\n"
                '  "can_answer": true/false,\n'
                '  "needs_more_info": "...what specific info is missing",\n'
                '  "clarifying_question": "..." or null\n'
                "}"
            )
            check_prompt = f"Context:\n{context[:4000]}\n\nQuestion: {current_question}"
            try:
                check_result = call_llm_structured(prompt=check_prompt, system_prompt=system_check)
                check = json.loads(check_result)
                if not check.get("can_answer", True):
                    clarifying = check.get("clarifying_question")
                    if clarifying and not clarification_needed:
                        clarification_needed = True
                        return {
                            "answer": clarifying,
                            "source_chunks": all_chunks,
                            "source_records": all_records,
                            "retrieval_rounds": retrieval_rounds,
                            "clarification": True,
                        }
            except Exception:
                pass

        system = (
            "You are a medical document assistant. Answer questions ONLY based on the provided context. "
            "If the context doesn't contain enough information to answer, say "
            "'I don't have that information available.' "
            "Be precise and cite specific values from the context. "
            "This is for informational purposes only — always recommend consulting a healthcare provider."
        )

        answer = call_llm(
            prompt=f"Context:\n{context[:6000]}\n\nQuestion: {current_question}",
            system_prompt=system,
        )

        if "don't have that information" not in answer.lower() or round_num == max_retrieval_rounds - 1:
            return {
                "answer": answer,
                "source_chunks": all_chunks[:5],
                "source_records": all_records,
                "retrieval_rounds": retrieval_rounds,
            }

        current_question = f"{question} (previous context was insufficient, try broader search)"

    return {
        "answer": "I don't have that information available.",
        "source_chunks": all_chunks[:5],
        "source_records": all_records,
        "retrieval_rounds": retrieval_rounds,
    }
