# AgentGate DevHack 2026 Makefile

.PHONY: install seed gateway dashboard demo test benchmark replay docker-up docker-down clean help

PYTHON ?= python

help:
	@echo "Available commands:"
	@echo "  make install     - Install required dependencies"
	@echo "  make seed        - Initialize and seed mock database"
	@echo "  make gateway     - Start FastAPI gateway on port 8000"
	@echo "  make dashboard   - Start Streamlit dashboard on port 8501"
	@echo "  make demo        - Execute side-by-side end-to-end demo"
	@echo "  make test        - Run full pytest test suite"
	@echo "  make benchmark   - Execute 64-scenario benchmark suite"
	@echo "  make replay      - Run Policy Time Machine trace replay"
	@echo "  make docker-up   - Start gateway & dashboard using Docker Compose"
	@echo "  make docker-down - Stop Docker Compose services"
	@echo "  make clean       - Remove cached files and temporary databases"

install:
	$(PYTHON) -m pip install -r requirements.txt

seed:
	$(PYTHON) -c "from backend_mock.tools import init_mock_db; from gateway.storage import init_db; init_mock_db(); init_db(); print('Databases initialized and seeded successfully.')"

gateway:
	$(PYTHON) -m uvicorn gateway.main:app --host 0.0.0.0 --port 8000 --reload

dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py --server.port 8501

demo:
	$(PYTHON) scripts/demo.py

test:
	$(PYTHON) -m pytest tests/ -v

benchmark:
	$(PYTHON) benchmark/run_benchmark.py

replay:
	$(PYTHON) scripts/replay_policies.py --policy policies/strict.yaml

docker-up:
	docker-compose up --build -d

docker-down:
	docker-compose down

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache *.db demo_*.db
