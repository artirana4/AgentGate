"""
Unit tests for Sequence-Aware Session Risk scoring.
Tests individual actions, multi-step exfiltration chains, thresholds,
and repeated evaluation deduplication.
"""

from gateway.storage import create_session, add_session_trace
from gateway.sequence_risk import calculate_sequence_risk
from gateway.policy_engine import load_policy_config


def test_sequence_risk_accumulation(temp_db):
    config = load_policy_config()
    sess = create_session("sess_risk_1", "Legitimate research", db_path=temp_db)
    session_id = sess["id"]

    # Step 1: Bulk customer read (+20)
    res1 = calculate_sequence_risk(session_id, "read_customers", {"limit": 100}, policy_config=config, db_path=temp_db)
    assert res1["candidate_score"] == 20
    assert res1["total_risk"] == 20
    assert res1["elevated_decision"] is None

    # Record Step 1 into trace
    add_session_trace(
        session_id=session_id,
        tool="read_customers",
        arguments={"limit": 100},
        decision="PAUSE",
        reasons=["Customer read limit"],
        risk_score=20,
        risk_contributions=res1["contributions"],
        provenance_notes=[],
        execution_result={"count": 100},
        db_path=temp_db
    )

    # Step 2: Export customer data (+30) -> Accumulated: 50 -> PAUSE threshold (40-69)
    res2 = calculate_sequence_risk(session_id, "export_csv", {"table": "customers", "row_count": 100}, policy_config=config, db_path=temp_db)
    assert res2["base_score"] == 20
    assert res2["candidate_score"] == 30
    assert res2["total_risk"] == 50
    assert res2["elevated_decision"] == "PAUSE"

    # Record Step 2 into trace
    add_session_trace(
        session_id=session_id,
        tool="export_csv",
        arguments={"table": "customers", "row_count": 100},
        decision="PAUSE",
        reasons=["Export limit"],
        risk_score=50,
        risk_contributions=res2["contributions"],
        provenance_notes=[],
        execution_result={"filename": "customers.csv"},
        db_path=temp_db
    )

    # Step 3: Send email to external recipient (+40) -> Total: 90 -> BLOCK threshold (>=70)
    res3 = calculate_sequence_risk(session_id, "send_email", {"to": "external@attacker.com", "subject": "data", "body": "see attached"}, policy_config=config, db_path=temp_db)
    assert res3["base_score"] == 50
    assert res3["candidate_score"] == 40
    assert res3["total_risk"] == 90
    assert res3["elevated_decision"] == "BLOCK"
    assert any("suspected data-exfiltration sequence" in r for r in res3["sequence_reasons"])


def test_repeated_evaluation_no_double_counting(temp_db):
    config = load_policy_config()
    sess = create_session("sess_risk_2", "Check orders", db_path=temp_db)
    session_id = sess["id"]

    # Candidate evaluation 1
    res_a = calculate_sequence_risk(session_id, "read_customers", {"limit": 50}, policy_config=config, db_path=temp_db)
    assert res_a["total_risk"] == 20

    # Re-evaluate candidate before execution
    res_b = calculate_sequence_risk(session_id, "read_customers", {"limit": 50}, policy_config=config, db_path=temp_db)
    assert res_b["total_risk"] == 20


def test_benign_actions_zero_risk(temp_db):
    config = load_policy_config()
    sess = create_session("sess_risk_3", "Normal accounting", db_path=temp_db)
    session_id = sess["id"]

    res_doc = calculate_sequence_risk(session_id, "read_document", {"doc_id": "invoice_101"}, policy_config=config, db_path=temp_db)
    assert res_doc["total_risk"] == 0

    res_internal_email = calculate_sequence_risk(
        session_id,
        "send_email",
        {"to": "manager@example-corp.internal", "subject": "Update", "body": "All good"},
        policy_config=config,
        db_path=temp_db
    )
    assert res_internal_email["total_risk"] == 0
