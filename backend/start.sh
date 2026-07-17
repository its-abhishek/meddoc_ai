#!/bin/sh
echo "Starting MedDocs API..."
echo "PORT=${PORT}"
echo "Working dir: $(pwd)"
echo "Files:"
ls -la
echo "---"
echo "Testing imports..."
python -c "from api.main import app; print('App loaded OK')" 2>&1
echo "---"
echo "Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8000}
