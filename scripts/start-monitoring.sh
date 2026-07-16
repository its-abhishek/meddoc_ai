#!/bin/bash
# Start the monitoring microservice
cd "$(dirname "$0")/../backend"
source .venv/bin/activate 2>/dev/null || true
uvicorn monitoring_service.app:app --host 0.0.0.0 --port 8001 --reload

# Uses monitoring_service (underscore)
