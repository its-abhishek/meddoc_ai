#!/bin/bash
# Full startup script for MedDocs AI
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="/tmp/meddocs"
mkdir -p "$LOG_DIR"

echo "=== MedDocs AI ==="
echo "Starting all services..."

# Start infrastructure
echo "[1/4] Starting PostgreSQL and Redis..."
cd "$PROJECT_DIR"
docker compose up -d

sleep 3

# Start backend
echo "[2/4] Starting backend API..."
cd "$PROJECT_DIR/backend"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt -q
else
    source .venv/bin/activate
fi

# Create database
psql -U postgres -h localhost -p 5433 -c "CREATE DATABASE meddocs" 2>/dev/null || true

OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false MKL_NUM_THREADS=1 \
  uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload \
  > "$LOG_DIR/backend.log" 2>&1 &
BACKEND_PID=$!

# Start celery worker
echo "[3/4] Starting Celery worker..."
OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false MKL_NUM_THREADS=1 \
  celery -A workers.celery_app:celery_app worker --loglevel=info --concurrency=1 \
  > "$LOG_DIR/celery.log" 2>&1 &
WORKER_PID=$!

# Start monitoring service
echo "[4/4] Starting monitoring service..."
OMP_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false \
  uvicorn monitoring_service.app:app --host 0.0.0.0 --port 8001 --reload \
  > "$LOG_DIR/monitoring.log" 2>&1 &
MONITOR_PID=$!

# Start frontend
echo "[5/5] Starting frontend..."
cd "$PROJECT_DIR/frontend"
if [ ! -d "node_modules" ]; then
    npm install
fi
npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
FRONTEND_PID=$!

echo ""
echo "=== All services started ==="
echo "  Backend API:    http://localhost:8000"
echo "  Monitoring:     http://localhost:8001"
echo "  Frontend:       http://localhost:3000"
echo "  PostgreSQL:     localhost:5433"
echo "  Redis:          localhost:6379"
echo ""
echo "Logs are in: $LOG_DIR/"
echo ""
echo "Tail logs with:"
echo "  tail -f $LOG_DIR/backend.log"
echo "  tail -f $LOG_DIR/celery.log"
echo "  tail -f $LOG_DIR/monitoring.log"
echo "  tail -f $LOG_DIR/frontend.log"
echo ""
echo "Press Ctrl+C to stop all services"

# Trap to kill all on exit
trap "kill $BACKEND_PID $WORKER_PID $MONITOR_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait

# Concurrency set to 1 for Groq rate limits

# Uses docker compose v2 and .venv
