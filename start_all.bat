@echo off
echo Starting AgentGate Backend and Frontend...
start "AgentGate Backend (Port 8000)" start_backend.bat
timeout /t 2 >nul
start "AgentGate Frontend (Port 8501)" start_frontend.bat
echo Both services launched in separate windows!
echo Backend:  http://localhost:8000 (Docs: http://localhost:8000/docs)
echo Frontend: http://localhost:8501
pause
