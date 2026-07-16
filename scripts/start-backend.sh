#!/bin/bash
# Start the MedDocs AI backend
set -e

cd "$(dirname "$0")/../backend"

# Create virtual environment if needed
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt -q

# Start PostgreSQL and Redis (via Docker)
echo "Starting infrastructure..."
docker-compose up -d postgres redis

# Wait for postgres
echo "Waiting for PostgreSQL..."
sleep 3

# Create database
echo "Creating database..."
psql -U postgres -h localhost -c "CREATE DATABASE meddocs" 2>/dev/null || true

# Threading vars to prevent SIGABRT on macOS with sentence-transformers
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export TOKENIZERS_PARALLELISM=false
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0

# Start the FastAPI server
echo "Starting FastAPI server..."
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &

# Start Celery worker
echo "Starting Celery worker..."
celery -A workers.celery_app:celery_app worker --loglevel=info --concurrency=2 &

echo "Backend started! API at http://localhost:8000"
wait
