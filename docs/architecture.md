# MedDocs AI Architecture Diagram

Paste this into [mermaid.live](https://mermaid.live) to generate a PNG/SVG.

```mermaid
flowchart TB
    subgraph Frontend["  Frontend — Next.js :3000"]
        UI_PATIENTS["  Patients"]
        UI_UPLOAD["⬆️ Upload"]
        UI_DETAIL["  Patient Detail"]
        UI_MONITOR["  Monitoring"]
    end

    subgraph Backend["  Backend — FastAPI :8000"]
        REST["REST API"]
        RAG["  RAG Query"]
        REPORT["  Report Gen"]
        EXPORT["  PDF Export"]
    end

    subgraph Pipeline["⚙️ LangGraph Pipeline"]
        PLANNER["  Planner"]
        EXTRACT["⚙️ Extractors"]
        VERIFY["  Verifier"]
        PERSIST["  Persist"]
        EMBED["  Embed"]
        FINAL["✅ Final"]
    end

    subgraph Worker["  Celery Worker"]
        TASK["process_document"]
    end

    subgraph Monitor["  Monitoring :8001"]
        SSE["SSE Stream"]
    end

    subgraph Infra["  Infrastructure"]
        PG[("PostgreSQL\n+ pgvector")]
        REDIS[("Redis")]
        STORAGE["  Storage"]
    end

    subgraph External["☁️ External"]
        GROQ["Groq API\nLlama 3.3"]
        HF["HuggingFace\nBGE-small"]
    end

    UI_PATIENTS --> REST
    UI_UPLOAD --> REST
    UI_DETAIL --> REST
    UI_MONITOR -.->|SSE| SSE

    REST --> TASK
    TASK --> PLANNER --> EXTRACT --> VERIFY
    VERIFY -->|accepted| PERSIST --> EMBED --> FINAL
    VERIFY -->|retry| EXTRACT

    PLANNER --> GROQ
    EXTRACT --> GROQ
    VERIFY --> GROQ
    EMBED --> HF

    TASK --> PG
    PERSIST --> PG
    EMBED --> PG
    RAG --> PG
    TASK --> REDIS
    SSE --> REDIS
    TASK --> STORAGE
```

## How to Generate Image

1. Go to [mermaid.live](https://mermaid.live)
2. Paste the mermaid code above
3. Click **Actions → Download SVG** or **Download PNG**
4. Save to `docs/architecture.png`
