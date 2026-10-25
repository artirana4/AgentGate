"""
Unit tests for Human Approvals workflow.
Verifies approval creation, impact preview, atomic execution, killswitch re-check,
rejection, and stale/duplicate request protection.
"""

from gateway.storage import create_session, get_db_connection
from gateway.approvals import (
    create_approval,
    get_approval,
    resolve_approval,
    set_killswitch,
    list_approvals
)


def test_approval_creation_and_preview(temp_db):
    sess = create_session("sess_app_1", "Process refunds", db_path=temp_db)
    app_item = create_approval(
        session_id=sess["id"],
        tool="issue_refund",
        arguments={"order_id": "ORD-1007", "amount": 15000.0},
        decision_reasons=["Refund amount exceeds threshold"],
        db_path=temp_db
    )
    assert app_item["status"] == "PENDING"
    assert "₹15,000.00" in app_item["impact_preview"]
    assert "ORD-1007" in app_item["impact_preview"]


def test_approve_executes_at_most_once(temp_db):
    sess = create_session("sess_app_2", "Process large refund", db_path=temp_db)
    app_item = create_approval(
        session_id=sess["id"],
        tool="issue_refund",
        arguments={"order_id": "ORD-1007", "amount": 15000.0},
        decision_reasons=["Large refund"],
        db_path=temp_db
    )
    approval_id = app_item["id"]

    # First approval -> Executes
    res = resolve_approval(approval_id, action="approve", db_path=temp_db)
    assert res["status"] == "SUCCESS"
    assert res["approval"]["status"] == "APPROVED"
    assert res["approval"]["execution_result"]["status"] == "SUCCESS"

    # Verify refund in mock_refunds
    conn = get_db_connection(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM mock_refunds WHERE order_id = 'ORD-1007'")
    count = cur.fetchone()[0]
    assert count == 1
    conn.close()

    # Second approval attempt -> Duplicate prevented
    res_dup = resolve_approval(approval_id, action="approve", db_path=temp_db)
    assert res_dup["status"] == "ERROR"
    assert "already APPROVED" in res_dup["message"]

    # Verify no duplicate refund added to ledger
    conn = get_db_connection(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM mock_refunds WHERE order_id = 'ORD-1007'")
    assert cur.fetchone()[0] == 1
    conn.close()


def test_approval_rejection_no_execution(temp_db):
    sess = create_session("sess_app_3", "Bulk export request", db_path=temp_db)
    app_item = create_approval(
        session_id=sess["id"],
        tool="export_csv",
        arguments={"table": "customers", "row_count": 500},
        decision_reasons=["Export too large"],
        db_path=temp_db
    )
    approval_id = app_item["id"]

    # Reject
    res = resolve_approval(approval_id, action="reject", db_path=temp_db)
    assert res["status"] == "SUCCESS"
    assert res["approval"]["status"] == "REJECTED"
    assert res["approval"]["execution_result"] is None

    # Cannot approve after rejection
    res_try_approve = resolve_approval(approval_id, action="approve", db_path=temp_db)
    assert res_try_approve["status"] == "ERROR"


def test_approval_blocks_if_killswitch_activated(temp_db):
    sess = create_session("sess_app_4", "Refund request", db_path=temp_db)
    app_item = create_approval(
        session_id=sess["id"],
        tool="issue_refund",
        arguments={"order_id": "ORD-1007", "amount": 15000.0},
        decision_reasons=["Large refund"],
        db_path=temp_db
    )
    approval_id = app_item["id"]

    # Activate global killswitch while pending
    set_killswitch(active=True, scope="global", reason="Emergency lockdown", db_path=temp_db)

    # Approve attempt must be blocked by killswitch
    res = resolve_approval(approval_id, action="approve", db_path=temp_db)
    assert res["status"] == "ERROR"
    assert "kill switch" in res["message"].lower()

    # Verify refund was not executed
    conn = get_db_connection(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM mock_refunds WHERE order_id = 'ORD-1007'")
    assert cur.fetchone()[0] == 0
    conn.close()
