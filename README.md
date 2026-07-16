# MedDocs AI

> Multi-tenant medical document processing platform powered by LangGraph, Groq LLM, and pgvector.

Upload lab reports, prescriptions, and insurance claims → AI extracts structured data → Query with natural language → Generate clinical reports → Export as PDF.

---

## Architecture
<img src="docs/architecture.png" alt="MedDocs AI Architecture" width="900" height="600">

## Getting Started

### Prerequisites

- Docker Desktop (for PostgreSQL + Redis)
- Python 3.14+
- Node.js 18+
- Groq API key (free at [console.groq.com](https://console.groq.com))

### Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/its-abhishek/meddoc_ai.git
cd meddoc_ai

# 2. Start infrastructure
docker compose up -d

# 3. Backend setup
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Configure API key
echo "GROQ_API_KEY=your_key_here" > .env

# 5. Start backend (terminal 1)
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# 6. Start Celery worker (terminal 2)
celery -A workers.celery_app:celery_app worker --loglevel=info --concurrency=1

# 7. Start monitoring (terminal 3)
uvicorn monitoring_service.app:app --host 0.0.0.0 --port 8001

# 8. Frontend (terminal 4)
cd ../frontend
npm install
npm run dev
```

Or use the all-in-one script:

```bash
./scripts/start-all.sh
# Logs at /tmp/meddocs/*.log
```

### Access

| Service | URL |
|---------|-----|
| Frontend | http://localhost:3000 |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| Monitoring | http://localhost:8001 |

## API Endpoints

### Tenants & Patients
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tenants` | Create tenant |
| `POST` | `/api/tenants/signup` | Create tenant + user |
| `GET` | `/api/tenants/{id}/dashboard` | Dashboard stats |
| `POST` | `/api/tenants/{id}/patients` | Create patient |
| `GET` | `/api/tenants/{id}/patients` | List patients |

### Documents
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tenants/{id}/patients/{pid}/documents` | Upload PDF/CSV |
| `GET` | `/api/tenants/{id}/patients/{pid}/documents` | List documents |
| `GET` | `/api/tenants/{id}/patients/{pid}/lab-results` | Lab results |
| `GET` | `/api/tenants/{id}/patients/{pid}/prescriptions` | Prescriptions |
| `GET` | `/api/tenants/{id}/patients/{pid}/claims` | Insurance claims |

### AI Features
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/tenants/{id}/patients/{pid}/query` | RAG natural language query |
| `GET` | `/api/tenants/{id}/patients/{pid}/summary` | AI-generated patient summary |
| `POST` | `/api/tenants/{id}/patients/{pid}/reports/generate` | Generate clinical report |
| `GET` | `/api/tenants/{id}/reports/{rid}/pdf` | Download report as PDF |

### Risk & Monitoring
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/tenants/{id}/patients/{pid}/risk-flags` | Drug interaction flags |
| `POST` | `/api/tenants/{id}/patients/{pid}/risk-flags/{fid}/action` | Dismiss/acknowledge flag |
| `GET` | `/monitor/tenants/{id}/active` | Active document processing |
| `GET` | `/monitor/documents/{id}/stream` | SSE live pipeline events |

## Pipeline Flow

1. **Upload** → File saved, Celery task dispatched
2. **Planner** → LLM classifies document type (lab/prescription/claim)
3. **Extractor** → LLM extracts structured data based on type
4. **Verifier** → LLM validates extraction quality, retries if needed
5. **Persist** → Structured data saved to PostgreSQL
6. **Chunk + Embed** → Text chunked (500 chars), embedded via BGE-small
7. **Finalize** → Document marked as processed, risk flags checked

All steps emit real-time events via Redis pub/sub → SSE to frontend.

## Key Design Decisions

- **Multi-tenant isolation**: Every table has `tenant_id` column, all queries filtered
- **Groq over OpenAI**: Faster inference, free tier, rate-limited with exponential backoff
- **pgvector over Pinecone**: Self-hosted, no vendor lock-in, 384-dim BGE-small embeddings
- **LangGraph over LangChain chains**: Visual pipeline, conditional routing, state management
- **Concurrency=1**: Groq free tier limits ~30 req/min, single worker prevents 429 storms
- **Monitoring as separate service**: SSE requires long-lived connections, isolated from API

## Environment Variables

```env
DATABASE_URL=postgresql+asyncpg://postgres:password@localhost:5433/meddocs
DATABASE_URL_SYNC=postgresql://postgres:password@localhost:5433/meddocs
REDIS_URL=redis://localhost:6379/0
GROQ_API_KEY=gsk_...
GROQ_REASONING_MODEL=llama-3.3-70b-versatile
GROQ_CLASSIFICATION_MODEL=llama-3.1-8b-instant
EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
EMBEDDING_DIM=384
STORAGE_PATH=./storage
MAX_UPLOAD_SIZE_MB=20
```

## License

MIT
