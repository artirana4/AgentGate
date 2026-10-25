"""
Unit tests for Global and Session Kill Switch controls.
Verifies execution blocking, per-session scoping, deactivation restoration,
and audit record capture.
"""

from gateway.storage import create_session
from gateway.approvals import set_killswitch, is_killswitch_active
from gateway.decision_engine import evaluate_action_decision
from gateway.audit import get_audit_events


def test_global_killswitch_blocks_all_actions(temp_db):
    sess = create_session("sess_ks_global", "Benign lookup", db_path=temp_db)

    # Initial state is inactive
    active, _ = is_killswitch_active(sess["id"], db_path=temp_db)
    assert not active

    # Activate global killswitch
    set_killswitch(active=True, scope="global", reason="Breach detected", db_path=temp_db)

    active, reason = is_killswitch_active(sess["id"], db_path=temp_db)
    assert active
    assert "Breach detected" in reason

    # Any candidate action should be BLOCKed
    dec = evaluate_action_decision(
        session_id=sess["id"],
        tool="read_customers",
        arguments={"limit": 10},
        db_path=temp_db
    )
    assert dec.decision.value == "BLOCK"
    assert "kill switch" in dec.reasons[0].lower()

    # Deactivate restores normal flow
    set_killswitch(active=False, scope="global", reason="Issue resolved", db_path=temp_db)
    active, _ = is_killswitch_active(sess["id"], db_path=temp_db)
    assert not active

    dec_resumed = evaluate_action_decision(
        session_id=sess["id"],
        tool="read_customers",
        arguments={"limit": 10},
        db_path=temp_db
    )
    assert dec_resumed.decision.value == "ALLOW"


def test_session_scoped_killswitch(temp_db):
    sess_a = create_session("sess_a", "User A", db_path=temp_db)
    sess_b = create_session("sess_b", "User B", db_path=temp_db)

    # Activate killswitch for session A only
    set_killswitch(active=True, scope="session", session_id=sess_a["id"], reason="Compromised token", db_path=temp_db)

    active_a, _ = is_killswitch_active(sess_a["id"], db_path=temp_db)
    assert active_a

    active_b, _ = is_killswitch_active(sess_b["id"], db_path=temp_db)
    assert not active_b

    # Session A blocked
    dec_a = evaluate_action_decision(sess_a["id"], "read_customers", {"limit": 5}, db_path=temp_db)
    assert dec_a.decision.value == "BLOCK"

    # Session B allowed
    dec_b = evaluate_action_decision(sess_b["id"], "read_customers", {"limit": 5}, db_path=temp_db)
    assert dec_b.decision.value == "ALLOW"


def test_killswitch_audit_logged(temp_db):
    set_killswitch(active=True, scope="global", reason="Security drill", db_path=temp_db)
    events = get_audit_events(limit=10, db_path=temp_db)
    assert any(e["event_type"] == "KILL_SWITCH_ACTIVATED" for e in events)
