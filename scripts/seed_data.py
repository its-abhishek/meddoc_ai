"""Seed script — creates demo tenant, patient, and synthetic test data."""
import asyncio
import uuid
import sys
sys.path.insert(0, ".")

from models.database import engine, async_session, init_db
from models.models import Tenant, Patient, Document, LabResult, Prescription, Claim


async def seed():
    await init_db()

    async with async_session() as db:
        # Create tenant
        tenant_id = "demo-tenant-001"
        existing = await db.get(Tenant, tenant_id)
        if not existing:
            db.add(Tenant(id=tenant_id, name="RVR Healthcare"))
            await db.commit()

        # Create patient
        patient_id = "demo-patient-001"
        existing = await db.get(Patient, patient_id)
        if not existing:
            db.add(Patient(
                id=patient_id,
                tenant_id=tenant_id,
                name="John Smith",
                external_ref="PAT-2024-001",
            ))
            await db.commit()

        # Create synthetic lab results
        labs = [
            ("Complete Blood Count", 7.2, "g/dL", "13.5-17.5", False, "2024-01-15"),
            ("Glucose, Fasting", 126, "mg/dL", "70-100", True, "2024-01-15"),
            ("Total Cholesterol", 242, "mg/dL", "<200", True, "2024-01-15"),
            ("HDL Cholesterol", 38, "mg/dL", ">40", True, "2024-01-15"),
            ("LDL Cholesterol", 165, "mg/dL", "<100", True, "2024-01-15"),
            ("Triglycerides", 198, "mg/dL", "<150", True, "2024-01-15"),
            ("Hemoglobin A1c", 7.8, "%", "<5.7", True, "2024-01-15"),
            ("Creatinine", 1.0, "mg/dL", "0.7-1.3", False, "2024-01-15"),
            ("Blood Urea Nitrogen", 18, "mg/dL", "7-20", False, "2024-01-15"),
            ("Sodium", 140, "mEq/L", "136-145", False, "2024-01-15"),
        ]

        for test_name, value, unit, ref_range, flagged, date_str in labs:
            doc_id = str(uuid.uuid4())
            db.add(Document(
                id=doc_id, tenant_id=tenant_id, patient_id=patient_id,
                source_filename=f"lab_{test_name.lower().replace(' ', '_')}.pdf",
                doc_type="lab_report", upload_status="processed",
            ))
            db.add(LabResult(
                id=str(uuid.uuid4()), tenant_id=tenant_id, patient_id=patient_id,
                document_id=doc_id, test_name=test_name, value=value,
                unit=unit, reference_range=ref_range, flagged_abnormal=flagged,
            ))

        # Create synthetic prescriptions
        rxs = [
            ("Metformin", "500mg", "Twice daily", "2024-01-15", "Dr. Patel"),
            ("Lisinopril", "10mg", "Once daily", "2024-01-15", "Dr. Patel"),
            ("Atorvastatin", "20mg", "Once daily at bedtime", "2024-01-15", "Dr. Patel"),
            ("Aspirin", "81mg", "Once daily", "2024-01-15", "Dr. Patel"),
        ]

        for drug, dose, freq, date_str, doctor in rxs:
            doc_id = str(uuid.uuid4())
            db.add(Document(
                id=doc_id, tenant_id=tenant_id, patient_id=patient_id,
                source_filename=f"rx_{drug.lower()}.pdf",
                doc_type="prescription", upload_status="processed",
            ))
            db.add(Prescription(
                id=str(uuid.uuid4()), tenant_id=tenant_id, patient_id=patient_id,
                document_id=doc_id, drug_name=drug, dosage=dose,
                frequency=freq, prescribing_doctor=doctor,
            ))

        await db.commit()
        print("Seed data created successfully!")
        print(f"  Tenant:  {tenant_id}")
        print(f"  Patient: {patient_id}")
        print(f"  Labs:    {len(labs)} records")
        print(f"  Rx:      {len(rxs)} records")


if __name__ == "__main__":
    asyncio.run(seed())
