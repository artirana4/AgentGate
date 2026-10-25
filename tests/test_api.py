"""
Unit and integration tests for AgentGate Gateway HTTP API endpoints.
Uses FastAPI TestClient to test all routes, validation, and status codes.
"""

import pytest
from fastapi.testclient import TestClient
from gateway.main import app
from gateway.storage import init_db
from backend_mock.tools import init_mock_db


@pytest.fixture(autouse=True)
def setup_api_db(temp_db, monkeypatch):
    """Point default database path to the test fixture DB for all API requests."""
    monkeypatch.setattr("gateway.storage.DEFAULT_DB_PATH", temp_db)
    monkeypatch.setattr("backend_mock.tools.DEFAULT_DB_PATH", temp_db)
    monkeypatch.setattr("gateway.audit.DEFAULT_DB_PATH", temp_db)
    init_mock_db(temp_db)
    init_db(temp_db)


@pytest.fixture
def client():
    return TestClient(app)


def test_health_check(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "HEALTHY"


def test_session_lifecycle_and_trace(client):
    # 1. Create Session
    create_res = client.post("/sessions", json={
        "user_instruction": "Please reconcile vendor invoice 101"
    })
    assert create_res.status_code == 201
    sess_data = create_res.json()
    session_id = sess_data["id"]
    assert sess_data["user_instruction"] == "Please reconcile vendor invoice 101"

    # 2. List sessions
    list_res = client.get("/sessions")
    assert list_res.status_code == 200
    assert any(s["id"] == session_id for s in list_res.json())

    # 3. Execute benign action (ALLOW)
    exec_res = client.post("/execute", json={
        "session_id": session_id,
        "tool": "read_customers",
        "arguments": {"limit": 5, "fields": ["id", "name"]}
    })
    assert exec_res.status_code == 200
    assert exec_res.json()["decision"] == "ALLOW"
    assert exec_res.json()["executed"] is True

    # 4. Inspect session detail and trace
    get_res = client.get(f"/sessions/{session_id}")
    assert get_res.status_code == 200
    assert len(get_res.json()["trace"]) == 1


def test_pause_and_approval_flow_via_api(client):
    # Create session
    sess_res = client.post("/sessions", json={"user_instruction": "Process customer refunds"})
    session_id = sess_res.json()["id"]

    # Execute action triggering PAUSE (refund > 10,000)
    exec_res = client.post("/execute", json={
        "session_id": session_id,
        "tool": "issue_refund",
        "arguments": {"order_id": "ORD-1007", "amount": 15000.0}
    })
    assert exec_res.status_code == 200
    data = exec_res.json()
    assert data["decision"] == "PAUSE"
    assert data["executed"] is False
    approval_id = data["pending_approval_id"]
    assert approval_id is not None

    # List pending approvals
    apps_res = client.get("/approvals?status=PENDING")
    assert apps_res.status_code == 200
    assert any(a["id"] == approval_id for a in apps_res.json())

    # Approve
    approve_res = client.post(f"/approvals/{approval_id}/approve")
    assert approve_res.status_code == 200
    assert approve_res.json()["status"] == "SUCCESS"
    assert approve_res.json()["approval"]["status"] == "APPROVED"


def test_killswitch_and_audit_verify_api(client):
    # Activate kill switch
    ks_post = client.post("/killswitch", json={
        "scope": "global",
        "active": True,
        "reason": "Test emergency"
    })
    assert ks_post.status_code == 200
    assert ks_post.json()["active"] is True

    # Verify audit chain
    audit_res = client.get("/audit/verify")
    assert audit_res.status_code == 200
    assert audit_res.json()["verified"] is True
