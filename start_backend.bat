@echo off
echo Starting AgentGate FastAPI Gateway (Backend) on http://localhost:8000 ...
py -3.12 -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload
pause
