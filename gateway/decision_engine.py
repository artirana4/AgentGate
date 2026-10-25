"""
Core Decision Engine for AgentGate.
Enforces strict decision precedence:
1. Active kill switch -> BLOCK
2. Mandatory provenance safety rules -> BLOCK
3. Policy BLOCK rules -> BLOCK
4. Sequence risk -> PAUSE or BLOCK based on thresholds
5. Policy PAUSE rules -> PAUSE
6. Otherwise -> ALLOW
"""

import os
from typing import Dict, Any, List, Optional
from gateway.models import DecisionType, ActionDecision
from gateway.approvals import is_killswitch_active
from gateway.provenance import find_provenance_match
from gateway.policy_engine import evaluate_policies, load_policy_config
from gateway.sequence_risk import calculate_sequence_risk


def evaluate_action_decision(
    session_id: str,
    tool: str,
    arguments: Dict[str, Any],
    policy_config: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
    provenance_enabled: Optional[bool] = None
) -> ActionDecision:
    """
    Evaluates an action request against AgentGate security controls.
    Returns deterministic ActionDecision adhering to precedence rules.
    """
    cfg = policy_config or load_policy_config()
    reasons: List[str] = []
    provenance_notes: List[str] = []
    
    prov_cfg = cfg.get("provenance", {})
    is_prov_enabled = provenance_enabled if provenance_enabled is not None else prov_cfg.get("enabled", True)

    # 1. Kill switch check
    ks_active, ks_reason = is_killswitch_active(session_id, db_path)
    if ks_active:
        return ActionDecision(
            decision=DecisionType.BLOCK,
            reasons=[f"Kill switch active: {ks_reason}"],
            risk_score=100,
            executed=False
        )

    # 2. Mandatory Provenance Safety Rule
    # "If an action attempts to email customer data to audit@external-example.com,
    # and the recipient appears only in an untrusted document, AgentGate must block the action"
    if is_prov_enabled and tool == "send_email":
        recipient = arguments.get("to")
        if recipient:
            prov_match = find_provenance_match(session_id, recipient, db_path=db_path)
            if prov_match["only_untrusted"]:
                sources_str = ", ".join(prov_match["sources"]) or "untrusted document"
                block_reason = (
                    f"Blocked: recipient {recipient} came only from untrusted document {sources_str}, "
                    f"not from trusted user instructions."
                )
                provenance_notes.append(f"Untrusted origin match ({prov_match['match_type']}): {sources_str}")
                return ActionDecision(
                    decision=DecisionType.BLOCK,
                    reasons=[block_reason],
                    risk_score=95,
                    provenance_notes=provenance_notes,
                    executed=False
                )
            elif prov_match["has_trusted"] and prov_match["has_untrusted"]:
                provenance_notes.append(f"Recipient {recipient} found in trusted user instruction (and also in document).")
            elif prov_match["has_trusted"]:
                provenance_notes.append(f"Recipient {recipient} verified from trusted user instruction.")
            else:
                provenance_notes.append(f"Recipient {recipient} had no prior provenance registration in session.")

    # 3. Structured Policy Rules evaluation
    policy_matches = evaluate_policies(tool, arguments, policy_config=cfg)
    policy_block_reasons = []
    policy_pause_reasons = []

    for pm in policy_matches:
        if pm["action"] == "BLOCK":
            policy_block_reasons.append(f"[Policy: {pm['rule_name']}] {pm['message']}")
        elif pm["action"] == "PAUSE":
            policy_pause_reasons.append(f"[Policy: {pm['rule_name']}] {pm['message']}")

    # 4. Sequence-aware risk scoring
    seq_result = calculate_sequence_risk(session_id, tool, arguments, policy_config=cfg, db_path=db_path)
    current_risk = seq_result["total_risk"]
    seq_decision = seq_result["elevated_decision"]
    seq_reasons = seq_result["sequence_reasons"]
    contributions = seq_result["contributions"]

    # 5. Resolve precedence: BLOCK > PAUSE > ALLOW
    if policy_block_reasons or seq_decision == "BLOCK":
        all_block_reasons = policy_block_reasons + seq_reasons
        return ActionDecision(
            decision=DecisionType.BLOCK,
            reasons=all_block_reasons if all_block_reasons else ["Blocked by security policies."],
            risk_score=current_risk,
            risk_contributions=contributions,
            provenance_notes=provenance_notes,
            executed=False
        )

    if policy_pause_reasons or seq_decision == "PAUSE":
        all_pause_reasons = policy_pause_reasons + seq_reasons
        return ActionDecision(
            decision=DecisionType.PAUSE,
            reasons=all_pause_reasons if all_pause_reasons else ["Requires explicit human approval."],
            risk_score=current_risk,
            risk_contributions=contributions,
            provenance_notes=provenance_notes,
            executed=False
        )

    # 6. Default ALLOW
    return ActionDecision(
        decision=DecisionType.ALLOW,
        reasons=["Action permitted under current safety policy."],
        risk_score=current_risk,
        risk_contributions=contributions,
        provenance_notes=provenance_notes,
        executed=False
    )
