pkill -f "uvicorn app.main" ; sleep 1
cd /home/claude/gsho-costing/backend
psql -h localhost -U gsho -d postgres -qc "drop database if exists gsho_qa" -c "create database gsho_qa"
export DATABASE_URL=postgresql+psycopg://gsho:gsho@localhost:5432/gsho_qa APP_ENV=dev AUTH_PROVIDER=dev STORAGE_PROVIDER=local STORAGE_LOCAL_ROOT=/tmp/gsho-qa
python -c "from app.db import init_db; init_db()
from scripts.seed import run; run()" 2>&1 | tail -1
nohup uvicorn app.main:app --port 8000 > /tmp/uvicorn.log 2>&1 &
sleep 4; python3 /home/claude/qa/prep.py
