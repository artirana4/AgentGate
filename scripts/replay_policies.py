"""
AgentGate Policy Time Machine.
Re-evaluates recorded session traces against updated policy definitions
without executing mock backend actions or creating duplicate side effects.

LIMITATION:
Policy replay evaluates recorded actions only. It does not simulate how an agent
would behave differently after an earlier action was blocked.
"""

import sys
import os
import argparse
from typing import Dict, Any, List, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gateway.storage import list_sessions, get_session
from gateway.policy_engine import load_policy_config
from gateway.decision_engine import evaluate_action_decision


def replay_policy(
    policy_path: str,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Replay recorded traces from database under a new policy configuration.
    Performs NO side effects (no emails, no refunds, no CSV exports).
    """
    new_cfg = load_policy_config(policy_path)
    sessions = list_sessions(db_path=db_path)

    total_actions = 0
    newly_blocked = 0
    newly_allowed = 0
    changed_decisions: List[Dict[str, Any]] = []

    for sess_meta in sessions:
        session_id = sess_meta["id"]
        sess = get_session(session_id, db_path=db_path)
        if not sess or not sess.get("trace"):
            continue

        for step in sess["trace"]:
            total_actions += 1
            tool = step["tool"]
            args = step["arguments"]
            orig_decision = step["decision"]

            # Re-evaluate under new policy without executing
            re_eval = evaluate_action_decision(
                session_id=session_id,
                tool=tool,
                arguments=args,
                policy_config=new_cfg,
                db_path=db_path
            )
            new_decision = re_eval.decision.value

            if new_decision != orig_decision:
                if new_decision == "BLOCK" and orig_decision != "BLOCK":
                    newly_blocked += 1
                elif new_decision == "ALLOW" and orig_decision != "ALLOW":
                    newly_allowed += 1

                changed_decisions.append({
                    "session_id": session_id,
                    "step_index": step["step_index"],
                    "tool": tool,
                    "arguments": args,
                    "original_decision": orig_decision,
                    "new_decision": new_decision,
                    "original_reasons": step["reasons"],
                    "new_reasons": re_eval.reasons
                })

    return {
        "policy_file": policy_path,
        "total_sessions": len(sessions),
        "total_actions": total_actions,
        "newly_blocked": newly_blocked,
        "newly_allowed": newly_allowed,
        "changed_decisions": changed_decisions
    }


def main():
    parser = argparse.ArgumentParser(description="AgentGate Policy Time Machine Replay")
    parser.add_argument(
        "--policy",
        "-p",
        default=os.path.join(os.path.dirname(__file__), "..", "policies", "strict.yaml"),
        help="Path to candidate policy YAML to test"
    )
    args = parser.parse_args()

    policy_file = os.path.abspath(args.policy)
    print(f"\n=================================================================")
    print(f"        AgentGate Policy Time Machine (Replay Mode)              ")
    print(f"=================================================================")
    print(f"Target Policy: {policy_file}")

    result = replay_policy(policy_file)

    print(f"Sessions Analyzed:       {result['total_sessions']}")
    print(f"Actions Re-evaluated:    {result['total_actions']}")
    print(f"Newly Blocked Actions:   {result['newly_blocked']}")
    print(f"Newly Allowed Actions:   {result['newly_allowed']}")
    print(f"Total Changed Decisions: {len(result['changed_decisions'])}")
    print(f"-----------------------------------------------------------------")

    if result["changed_decisions"]:
        print("\nDecision Changes Preview:")
        for idx, change in enumerate(result["changed_decisions"][:5], 1):
            print(f"  [{idx}] Session: {change['session_id']} | Step: {change['step_index']} | Tool: {change['tool']}")
            print(f"      Original: {change['original_decision']} -> New: {change['new_decision']}")
            print(f"      New Reason: {'; '.join(change['new_reasons'])}")
        if len(result["changed_decisions"]) > 5:
            print(f"  ... and {len(result['changed_decisions']) - 5} more changes.")
    else:
        print("No decision differences detected between recorded traces and candidate policy.")

    print("\nNote: Policy replay evaluates recorded actions only. It does not simulate")
    print("how an agent would behave differently after an earlier action was blocked.\n")


if __name__ == "__main__":
    main()
