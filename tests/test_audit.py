"""
Unit tests for Tamper-Evident Audit Logging.
Verifies canonical SHA-256 hash chaining, integrity verification,
and detection of deliberate SQLite record tampering.
"""

from gateway.audit import (
    record_audit_event,
    verify_audit_chain,
    get_audit_events
)
from gateway.storage import get_db_connection


def test_audit_hash_chain_validity(temp_db):
    # Log multiple sequence events
    record_audit_event("SESSION_CREATED", session_id="s1", action="start", db_path=temp_db)
    record_audit_event("ACTION_EXECUTED", session_id="s1", action="read_customers", arguments={"limit": 10}, decision="ALLOW", db_path=temp_db)
    record_audit_event("ACTION_BLOCKED", session_id="s1", action="send_email", arguments={"to": "bad@external.com"}, decision="BLOCK", db_path=temp_db)

    events = get_audit_events(limit=10, db_path=temp_db)
    assert len(events) == 3

    # Verification must pass
    res = verify_audit_chain(db_path=temp_db)
    assert res["verified"] is True
    assert res["total_events"] == 3
    assert res["first_invalid_index"] is None


def test_audit_detects_deliberate_tampering(temp_db):
    record_audit_event("EVT_1", session_id="s_tamper", action="step1", decision="ALLOW", db_path=temp_db)
    e2 = record_audit_event("EVT_2", session_id="s_tamper", action="step2", decision="BLOCK", reason="Initial", db_path=temp_db)
    record_audit_event("EVT_3", session_id="s_tamper", action="step3", decision="ALLOW", db_path=temp_db)

    # Verify initially valid
    assert verify_audit_chain(db_path=temp_db)["verified"] is True

    # Deliberately modify record 2 directly in SQLite (simulating rogue DBA / attacker)
    conn = get_db_connection(temp_db)
    with conn:
        conn.execute("UPDATE audit_log SET decision = 'ALLOW' WHERE event_id = ?", (e2["event_id"],))
    conn.close()

    # Chain verification must catch the tampering
    res = verify_audit_chain(db_path=temp_db)
    assert res["verified"] is False
    assert res["first_invalid_index"] == 1
    assert res["first_invalid_event_id"] == e2["event_id"]
    assert "Hash mismatch" in res["details"]
