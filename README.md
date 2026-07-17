# MedDocs AI

### Turn messy medical documents into structured, searchable, actionable patient intelligence.

MedDocs AI is an AI-powered medical document processing platform that automatically extracts, organizes, and understands clinical data from uploaded documents — so healthcare teams can focus on care, not data entry.

---

## Architecture

```mermaid
graph TB
    subgraph Frontend ["Frontend — Next.js + React :3000"]
        UI[Dashboard]
        UP[Upload]
        MON[Monitoring]
    end

    subgraph Backend ["Backend — FastAPI :8000"]
        API[REST API]
        MON_API[Monitoring Service :8001]
    end

    subgraph AI["AI Pipeline — LangGraph"]
        P[Planner]
        E[Extractor]
        V[Verifier]
        PE[Persist + Embed]
        Q[RAG Query]
        RG[Report Gen]
        RF[Risk Flag]
    end

    subgraph Workers ["Celery Workers"]
        CW[process_document]
        RT[generate_report]
    end

    subgraph Data ["Data Layer"]
        PG[(PostgreSQL + pgvector)]
        RD[(Redis)]
        FS[(File Storage)]
    end

    subgraph LLM ["External LLM"]
        GROQ[Groq API — Llama 3.1/3.3]
    end

    UI --> API
    UP --> API
    MON --> MON_API
    API --> CW
    CW --> P --> E --> V --> PE
    PE --> PG
    PE --> FS
    E --> GROQ
    V --> GROQ
    P --> GROQ
    Q --> PG
    Q --> GROQ
    RG --> GROQ
    RF --> GROQ
    CW --> RD
    MON_API --> RD
    RD --> MON
    CW -.->|Pub/Sub| RD

    style Frontend fill:#e0f2fe,stroke:#0284c7
    style Backend fill:#f0fdf4,stroke:#16a34a
    style AI fill:#fef3c7,stroke:#d97706
    style Workers fill:#fce7f3,stroke:#db2777
    style Data fill:#f3e8ff,stroke:#9333ea
    style LLM fill:#fff7ed,stroke:#ea580c
```

---

## What it does

**Upload** any medical document — lab reports, prescriptions, insurance claims, discharge summaries — and MedDocs AI handles the rest.

### Intelligent Document Processing

Drop in a PDF or CSV. Our AI pipeline automatically:

- **Classifies** the document type (lab report, prescription, claim, etc.)
- **Extracts** structured data — test results, medication details, claim amounts, diagnoses
- **Validates** extraction quality with automatic retry on low-confidence results
- **Indexes** everything for instant natural language search

All of this happens in real-time, with live pipeline monitoring so you always know where things stand.

### Structured Data, Ready to Use

Once processed, your documents become:

| Extracted Data | What you see |
|---|---|
| **Lab Results** | Test names, values, units, reference ranges, abnormal flags, dates |
| **Prescriptions** | Drug names, dosages, frequencies, prescribing doctors |
| **Insurance Claims** | Procedure codes, amounts, status, dates |
| **Clinical Notes** | Full extracted text, chunked and embedded for search |

### Ask Anything

Query patient records in plain English:

> *"What were John's last 3 hemoglobin levels?"*
> *"Which medications is Sarah currently prescribed?"*
> *"Show me all abnormal lab results from this month."*

MedDocs AI uses RAG (Retrieval-Augmented Generation) to find relevant document chunks and generate accurate, sourced answers.

### AI-Generated Reports

Generate comprehensive clinical reports with one click. The AI planner decides which sections to include based on available patient data:

| Section | When Included |
|---|---|
| **Patient Overview** | Always — demographics, visit context, summary |
| **Lab Results Summary** | When lab data exists — key findings, abnormal values |
| **Medication Summary** | When prescriptions exist — current meds, dosages |
| **Risk Flags** | When open risk flags exist — clinical advisories |
| **Lab Trends** | When multiple lab tests available — directional analysis |

Each section is independently editable:

- Click **Edit** to modify any section's content
- Click **Regenerate** to have AI rewrite a section with fresh context
- Click **+ Add Note** to insert custom clinical notes
- Click **Finalize** to lock the report
- Click **Download PDF** to export a formatted clinical report

**[View Sample Report →](report_5f665ad4.pdf)**

Reports are generated in the background via Celery workers — the UI polls until the report is ready, then displays the full editable canvas.

### Risk Detection

Automatically flags potential concerns:

- Drug interactions between current prescriptions
- Abnormal lab trends that warrant attention
- Claims requiring follow-up or appeal

Flags are surfaced in real-time and can be acknowledged or dismissed with documented reasons.

---

## How it works

```
Upload Document
      ↓
  Classify ──→ What type of document is this?
      ↓
  Extract ──→ Pull structured data from the document
      ↓
  Verify ──→ Is the extraction accurate? Retry if needed.
      ↓
  Persist ──→ Save structured data to the patient record
      ↓
  Embed ──→ Index text for semantic search
      ↓
  Done ──→ Document is searchable, queryable, and report-ready
```

All steps run asynchronously via background workers, with real-time status updates streamed to the dashboard.

---

## Multi-Tenant by Design

Every piece of data is scoped to a tenant. Patient records, documents, extracted data, reports — nothing leaks across organizations. MedDocs AI is built for environments where data isolation isn't optional.

---

## Built with

### Backend

| Technology | Purpose |
|---|---|
| Python 3.14 | Backend runtime |
| FastAPI | Async REST API framework |
| SQLAlchemy 2.0 | ORM (async + sync engines) |
| Celery | Distributed background task queue |
| Redis | Task broker + Pub/Sub event streaming |
| PostgreSQL 16 | Primary database |
| pgvector | Vector similarity search for RAG (384-dim embeddings) |
| Alembic | Database migrations |

### AI / ML

| Technology | Purpose |
|---|---|
| LangGraph | Agentic pipeline with conditional routing and state management |
| LangChain | LLM orchestration abstractions |
| Groq (Llama 3.1/3.3) | Fast LLM inference for classification, extraction, verification, RAG, reports |
| sentence-transformers | Local embedding model loading |
| BAAI/bge-small-en-v1.5 | 384-dimensional text embeddings for semantic search |

### Document Processing

| Technology | Purpose |
|---|---|
| pdfplumber | PDF text and table extraction |
| pytesseract + pdf2image | OCR fallback for scanned documents |
| pandas | CSV parsing and tabular data manipulation |
| fpdf2 | PDF report generation and export |

### Frontend

| Technology | Purpose |
|---|---|
| Next.js 15 | React framework with App Router |
| React 19 | UI library |
| TypeScript 5.7 | Type-safe development |
| Tailwind CSS 3.4 | Utility-first styling |
| react-dropzone | Drag-and-drop file uploads |
| EventSource (SSE) | Real-time pipeline monitoring |

### Infrastructure

| Technology | Purpose |
|---|---|
| Docker Compose | Container orchestration (PostgreSQL + Redis) |
| Uvicorn | ASGI server |
| sse-starlette | Server-Sent Events streaming |
| Bash scripts | Automated startup orchestration |

### Patterns & Architecture

| Pattern | Implementation |
|---|---|
| Multi-tenancy | Tenant-scoped data isolation across all 15 database tables |
| Agentic AI Pipeline | LangGraph StateGraph with planner → extractor → verifier → persist → embed flow |
| RAG | pgvector cosine search + structured data augmentation for natural language queries |
| Event-Driven | Redis Pub/Sub → SSE for real-time pipeline status |
| Background Processing | Celery async tasks with retry/backoff |
| Rate Limiting | Custom limiter with exponential backoff for LLM API calls |
| Graceful Degradation | OCR fallback, verification with auto-retry, fallback classifiers |

---

## Getting Started

### Prerequisites

- Docker Desktop
- Python 3.14+
- Node.js 18+
- Groq API key (free at [console.groq.com](https://console.groq.com))

### Quick Start

```bash
# Clone
git clone https://github.com/its-abhishek/meddoc_ai.git
cd meddoc_ai

# Configure
echo "GROQ_API_KEY=your_key_here" > .env

# Start everything
./scripts/start-all.sh
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:3000 |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| Monitoring | http://localhost:8001 |

Logs are written to `/tmp/meddocs/`.

---

## API

<details>
<summary>Patient & Tenant Management</summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tenants` | Create tenant |
| `POST` | `/api/tenants/signup` | Create tenant + user |
| `GET` | `/api/tenants/{id}/dashboard` | Dashboard stats |
| `POST` | `/api/tenants/{id}/patients` | Create patient |
| `GET` | `/api/tenants/{id}/patients` | List patients |

</details>

<details>
<summary>Document Processing</summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tenants/{id}/patients/{pid}/documents` | Upload PDF/CSV |
| `GET` | `/api/tenants/{id}/patients/{pid}/documents` | List documents |
| `GET` | `/api/tenants/{id}/documents/{did}` | Document detail + extracted data |
| `GET` | `/api/tenants/{id}/documents/{did}/file` | View/download original file |
| `DELETE` | `/api/tenants/{id}/documents/{did}` | Delete document + data |

</details>

<details>
<summary>Structured Data</summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tenants/{id}/patients/{pid}/lab-results` | Lab results |
| `GET` | `/api/tenants/{id}/patients/{pid}/prescriptions` | Prescriptions |
| `GET` | `/api/tenants/{id}/patients/{pid}/claims` | Insurance claims |
| `GET` | `/api/tenants/{id}/patients/{pid}/risk-flags` | Risk flags |
| `GET` | `/api/tenants/{id}/patients/{pid}/trends/{test}` | Lab value trends |

</details>

<details>
<summary>AI Features</summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tenants/{id}/patients/{pid}/query` | Natural language search |
| `GET` | `/api/tenants/{id}/patients/{pid}/summary` | AI patient summary |
| `POST` | `/api/tenants/{id}/patients/{pid}/reports/generate` | Generate report |
| `GET` | `/api/tenants/{id}/reports/{rid}/pdf` | Download report PDF |

</details>

<details>
<summary>Monitoring</summary>

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/monitor/tenants/{id}/active` | Active pipeline runs |
| `GET` | `/monitor/documents/{did}/status` | Document processing status |
| `GET` | `/monitor/documents/{did}/stream` | SSE live events |

</details>

---

## Environment Variables

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5433/meddocs
DATABASE_URL_SYNC=postgresql://postgres:password@localhost:5433/meddocs
REDIS_URL=redis://localhost:6379/0
GROQ_API_KEY=gsk_...
GROQ_REASONING_MODEL=llama-3.1-8b-instant
GROQ_CLASSIFICATION_MODEL=llama-3.1-8b-instant
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DIM=384
STORAGE_PATH=./storage
MAX_UPLOAD_SIZE_MB=20
```

---

## License

MIT
