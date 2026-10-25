"""
Sequence-Aware Session Risk scoring for AgentGate.
Maintains and accumulates risk scores per session across sequential actions.
Detects multi-step exfiltration chains (e.g., read -> export -> external email).
"""

from typing import Dict, Any, List, Optional, Tuple
from gateway.storage import get_session, update_session_risk

DEFAULT_WEIGHTS = {
    "bulk_customer_read": 20,
    "customer_data_export": 30,
    "external_recipient_email": 40
}

DEFAULT_THRESHOLDS = {
    "pause_min": 40,
    "block_min": 70
}


def classify_action_risk_contribution(
    tool: str,
    arguments: Dict[str, Any],
    allowed_domains: Optional[List[str]] = None
) -> Tuple[int, Optional[str], Optional[str]]:
    """
    Classify if an individual action contributes to sequence risk.
    Returns: (points, event_category, reason)
    """
    allowed_doms = allowed_domains or ["example-corp.internal", "internal.local", "trusted-partner.internal"]

    if tool == "read_customers":
        limit = arguments.get("limit", 0)
        # Even reading > 10 records contributes to bulk accumulation
        if limit >= 10:
            return 20, "bulk_customer_read", f"Bulk customer read (limit={limit}) (+20 risk)"

    elif tool == "export_csv":
        table = arguments.get("table", "")
        if table == "customers":
            return 30, "customer_data_export", f"Customer database export to CSV (+30 risk)"

    elif tool == "send_email":
        recipient = str(arguments.get("to", "")).lower()
        is_internal = any(recipient.endswith(d.lower()) for d in allowed_doms)
        if not is_internal:
            return 40, "external_recipient_email", f"Email send to external recipient '{recipient}' (+40 risk)"

    return 0, None, None


def calculate_sequence_risk(
    session_id: str,
    current_tool: str,
    current_arguments: Dict[str, Any],
    policy_config: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Calculates current session risk including historical steps and candidate action.
    Avoids double-counting on repeated evaluations by checking existing trace categories.
    """
    cfg = policy_config or {}
    risk_cfg = cfg.get("risk_scoring", {})
    weights = risk_cfg.get("weights", DEFAULT_WEIGHTS)
    thresholds = risk_cfg.get("thresholds", DEFAULT_THRESHOLDS)
    allowed_domains = cfg.get("allowed_domains", ["example-corp.internal", "internal.local", "trusted-partner.internal"])

    session = get_session(session_id, db_path) if session_id else None
    
    # Analyze prior recorded actions in this session
    prior_categories = set()
    prior_contributions = []
    base_score = 0

    if session and "trace" in session:
        for step in session["trace"]:
            # Only consider successfully executed or paused actions
            t = step.get("tool")
            args = step.get("arguments", {})
            pts, cat, rsn = classify_action_risk_contribution(t, args, allowed_domains)
            if cat and cat not in prior_categories:
                prior_categories.add(cat)
                pts = weights.get(cat, pts)
                base_score += pts
                prior_contributions.append({
                    "category": cat,
                    "points": pts,
                    "reason": rsn,
                    "step_index": step.get("step_index")
                })

    # Now evaluate current candidate action
    cand_pts, cand_cat, cand_rsn = classify_action_risk_contribution(current_tool, current_arguments, allowed_domains)
    cand_score = 0
    all_contributions = list(prior_contributions)

    if cand_cat and cand_cat not in prior_categories:
        cand_score = weights.get(cand_cat, cand_pts)
        all_contributions.append({
            "category": cand_cat,
            "points": cand_score,
            "reason": cand_rsn,
            "step_index": "current"
        })

    total_risk = base_score + cand_score
    block_threshold = thresholds.get("block_min", 70)
    pause_threshold = thresholds.get("pause_min", 40)

    elevated_decision = None
    sequence_reasons = []

    # Sequence pattern explanation
    has_read = "bulk_customer_read" in prior_categories or cand_cat == "bulk_customer_read"
    has_export = "customer_data_export" in prior_categories or cand_cat == "customer_data_export"
    has_external_email = "external_recipient_email" in prior_categories or cand_cat == "external_recipient_email"

    if has_read and has_export and has_external_email:
        sequence_reasons.append(
            "Blocked because this action follows a bulk customer read and export in the same session, creating a suspected data-exfiltration sequence."
        )
    elif total_risk >= block_threshold:
        sequence_reasons.append(
            f"Blocked by sequence risk score: {total_risk} >= threshold {block_threshold}."
        )
    elif total_risk >= pause_threshold:
        sequence_reasons.append(
            f"Paused by sequence risk score: {total_risk} >= threshold {pause_threshold} (requires human approval)."
        )

    if total_risk >= block_threshold:
        elevated_decision = "BLOCK"
    elif total_risk >= pause_threshold:
        elevated_decision = "PAUSE"

    return {
        "base_score": base_score,
        "candidate_score": cand_score,
        "total_risk": total_risk,
        "contributions": all_contributions,
        "elevated_decision": elevated_decision,
        "sequence_reasons": sequence_reasons
    }
