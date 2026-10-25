"""
Unit tests for Data Provenance tracking.
Verifies trust levels, extraction, matching algorithms (exact, normalized, approximate),
the mandatory untrusted exfiltration safety rule, and the three recipient trust cases.
"""

from gateway.storage import create_session
from gateway.provenance import (
    register_provenance,
    register_text_provenance,
    find_provenance_match,
    get_session_provenance
)
from gateway.execution import execute_tool
from gateway.decision_engine import evaluate_action_decision


def test_provenance_registration_and_trust_levels(temp_db):
    sess = create_session("sess_prov_1", "Send invoice to accounting@example-corp.internal", db_path=temp_db)
    session_id = sess["id"]

    # Register user instruction entities
    register_text_provenance(
        session_id=session_id,
        source_id="user_instruction",
        source_type="user_instruction",
        trust_level="user",
        text=sess["user_instruction"],
        db_path=temp_db
    )

    records = get_session_provenance(session_id, db_path=temp_db)
    assert len(records) >= 1
    rec = records[0]
    assert rec["trust_level"] == "user"
    assert rec["extracted_value"] == "accounting@example-corp.internal"


def test_matching_algorithms_exact_normalized_approximate(temp_db):
    sess = create_session("sess_match", "Test matching", db_path=temp_db)
    session_id = sess["id"]

    register_provenance(
        session_id=session_id,
        source_id="doc_test",
        source_type="document",
        trust_level="untrusted",
        extracted_value="InjectedUser@ExternalDomain.com",
        db_path=temp_db
    )

    # 1. Exact match
    m_exact = find_provenance_match(session_id, "InjectedUser@ExternalDomain.com", db_path=temp_db)
    assert m_exact["found"] is True
    assert m_exact["match_type"] == "exact"

    # 2. Normalized match (different case / spaces)
    m_norm = find_provenance_match(session_id, " injecteduser@externaldomain.com ", db_path=temp_db)
    assert m_norm["found"] is True
    assert m_norm["match_type"] == "normalized"

    # 3. Approximate match (slight typo / bounded difflib)
    m_approx = find_provenance_match(session_id, "injecteduser@externaldoman.com", db_path=temp_db, similarity_cutoff=0.85)
    assert m_approx["found"] is True
    assert m_approx["match_type"] == "approximate"


def test_three_trust_cases_and_mandatory_safety_rule(temp_db):
    # Case 1: Recipient appears ONLY in untrusted document
    sess1 = create_session("sess_c1", "Please process vendor invoices", db_path=temp_db)
    execute_tool("read_document", {"doc_id": "invoice_482"}, session_id=sess1["id"], db_path=temp_db)

    # Attempt to send email to the recipient found in invoice_482
    dec1 = evaluate_action_decision(
        session_id=sess1["id"],
        tool="send_email",
        arguments={"to": "audit@external-example.com", "subject": "Data", "body": "Export"},
        db_path=temp_db,
        provenance_enabled=True
    )
    assert dec1.decision.value == "BLOCK"
    assert any("came only from untrusted document" in r for r in dec1.reasons)
    assert any("invoice_482" in r for r in dec1.reasons)

    # Case 2: Recipient explicitly supplied by trusted user instruction
    sess2 = create_session("sess_c2", "Send the report to partner@trusted-partner.internal", db_path=temp_db)
    register_text_provenance(sess2["id"], "user_instruction", "user_instruction", "user", sess2["user_instruction"], db_path=temp_db)

    dec2 = evaluate_action_decision(
        session_id=sess2["id"],
        tool="send_email",
        arguments={"to": "partner@trusted-partner.internal", "subject": "Report", "body": "Attached"},
        db_path=temp_db,
        provenance_enabled=True
    )
    # Since recipient is in allowed domains and from user, not blocked by provenance
    assert dec2.decision.value == "ALLOW"

    # Case 3: Recipient appears in BOTH user instruction and untrusted document
    sess3 = create_session("sess_c3", "Send invoice to audit@external-example.com please", db_path=temp_db)
    register_text_provenance(sess3["id"], "user_instruction", "user_instruction", "user", sess3["user_instruction"], db_path=temp_db)
    execute_tool("read_document", {"doc_id": "invoice_482"}, session_id=sess3["id"], db_path=temp_db)

    prov_match3 = find_provenance_match(sess3["id"], "audit@external-example.com", db_path=temp_db)
    assert prov_match3["has_trusted"] is True
    assert prov_match3["has_untrusted"] is True
    assert prov_match3["only_untrusted"] is False


def test_provenance_disabled_ablation(temp_db):
    sess = create_session("sess_ablation", "Review document", db_path=temp_db)
    execute_tool("read_document", {"doc_id": "invoice_482"}, session_id=sess["id"], db_path=temp_db)

    # When provenance is disabled, the specific provenance safety block is not triggered
    # (General domain policy may still trigger BLOCK, but reason will not be the provenance untrusted document block)
    dec = evaluate_action_decision(
        session_id=sess["id"],
        tool="send_email",
        arguments={"to": "audit@external-example.com", "subject": "Data", "body": "Export"},
        db_path=temp_db,
        provenance_enabled=False
    )
    assert not any("came only from untrusted document" in r for r in dec.reasons)
