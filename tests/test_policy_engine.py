"""
Unit tests for Structured Policy Engine.
Verifies all supported operators, rejection of malformed definitions,
type mismatches, missing fields, and default security rules.
"""

import pytest
from gateway.policy_engine import (
    evaluate_condition,
    evaluate_policies,
    validate_rule_structure,
    load_policy_config,
    PolicyValidationError
)


def test_operators():
    # gt & lt
    assert evaluate_condition({"field": "amount", "op": "gt", "value": 1000}, {"amount": 1001}) is True
    assert evaluate_condition({"field": "amount", "op": "gt", "value": 1000}, {"amount": 1000}) is False
    assert evaluate_condition({"field": "amount", "op": "lt", "value": 500}, {"amount": 499}) is True
    assert evaluate_condition({"field": "amount", "op": "lt", "value": 500}, {"amount": 500}) is False

    # eq
    assert evaluate_condition({"field": "status", "op": "eq", "value": "COMPLETED"}, {"status": "COMPLETED"}) is True
    assert evaluate_condition({"field": "status", "op": "eq", "value": "COMPLETED"}, {"status": "PENDING"}) is False

    # in & not_in
    assert evaluate_condition({"field": "role", "op": "in", "value": ["admin", "superadmin"]}, {"role": "admin"}) is True
    assert evaluate_condition({"field": "role", "op": "in", "value": ["admin", "superadmin"]}, {"role": "guest"}) is False
    assert evaluate_condition({"field": "role", "op": "not_in", "value": ["guest", "banned"]}, {"role": "employee"}) is True
    assert evaluate_condition({"field": "role", "op": "not_in", "value": ["guest", "banned"]}, {"role": "guest"}) is False

    # ends_with
    assert evaluate_condition({"field": "email", "op": "ends_with", "value": "@example-corp.internal"}, {"email": "test@example-corp.internal"}) is True
    assert evaluate_condition({"field": "email", "op": "ends_with", "value": "@example-corp.internal"}, {"email": "test@external.com"}) is False


def test_missing_fields_and_type_mismatches():
    # Field missing in arguments safely evaluates to False
    assert evaluate_condition({"field": "missing_key", "op": "gt", "value": 10}, {"amount": 50}) is False

    # Type mismatch on numeric op safely evaluates to False
    assert evaluate_condition({"field": "amount", "op": "gt", "value": 100}, {"amount": "invalid_number"}) is False


def test_malformed_rule_validation():
    # Unknown operator
    with pytest.raises(PolicyValidationError, match="unknown operator"):
        validate_rule_structure({
            "name": "bad_op",
            "tool": "send_email",
            "conditions": [{"field": "x", "op": "regex_eval", "value": ".*"}],
            "action": "BLOCK",
            "message": "bad"
        }, index=0)

    # Missing required field
    with pytest.raises(PolicyValidationError, match="missing required fields"):
        validate_rule_structure({
            "name": "incomplete_rule",
            "tool": "send_email"
        }, index=0)

    # Invalid action
    with pytest.raises(PolicyValidationError, match="invalid action"):
        validate_rule_structure({
            "name": "bad_action",
            "tool": "send_email",
            "conditions": [{"field": "x", "op": "eq", "value": 1}],
            "action": "PERMIT_ALL",
            "message": "bad"
        }, index=0)


def test_default_policies_evaluation():
    config = load_policy_config()

    # 1. Refund > 10,000 -> PAUSE
    matches = evaluate_policies("issue_refund", {"order_id": "ORD-1007", "amount": 15000.0}, policy_config=config)
    assert any(m["action"] == "PAUSE" for m in matches)

    # Refund <= 10,000 -> not paused by this rule
    matches_small = evaluate_policies("issue_refund", {"order_id": "ORD-1001", "amount": 500.0}, policy_config=config)
    assert len(matches_small) == 0

    # 2. External email recipient -> BLOCK
    matches_ext_email = evaluate_policies(
        "send_email",
        {"to": "audit@external-example.com", "subject": "Test", "body": "Data"},
        policy_config=config
    )
    assert any(m["action"] == "BLOCK" for m in matches_ext_email)

    # Internal email recipient -> Allowed by policy
    matches_int_email = evaluate_policies(
        "send_email",
        {"to": "alice@example-corp.internal", "subject": "Test", "body": "Data"},
        policy_config=config
    )
    assert len(matches_int_email) == 0

    # 3. CSV export > 100 rows -> PAUSE
    matches_bulk_export = evaluate_policies("export_csv", {"table": "customers", "row_count": 200}, policy_config=config)
    assert any(m["action"] == "PAUSE" for m in matches_bulk_export)

    # 4. Customer read > 100 records -> PAUSE
    matches_bulk_read = evaluate_policies("read_customers", {"limit": 150}, policy_config=config)
    assert any(m["action"] == "PAUSE" for m in matches_bulk_read)
