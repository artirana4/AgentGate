@echo off
echo Starting AgentGate Streamlit Dashboard (Frontend) on http://localhost:8501 ...
py -3.12 -m streamlit run dashboard/app.py --server.port 8501
pause
