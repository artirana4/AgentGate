"""
Deterministic Scripted Agent for AgentGate.
Passes all actions through AgentGate before execution.
Respects ALLOW, PAUSE, and BLOCK decisions without ever bypassing the gateway boundary.
"""

import sys
import os
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gateway.storage import create_session, get_session
from gateway.provenance import register_text_provenance
from gateway.decision_engine import evaluate_action_decision
from gateway.execution import execute_tool
from gateway.approvals import create_approval, resolve_approval
from gateway.models import DecisionType


class ScriptedAgent:
    """
    Simulated autonomous business agent following scripted plans.
    Guaranteed to route all actions strictly through the AgentGate decision boundary.
    """

    def __init__(self, agent_name: str = "ScriptedAgent-1", db_path: Optional[str] = None):
        self.agent_name = agent_name
        self.db_path = db_path
        self.session_id: Optional[str] = None

    def start_session(self, user_instruction: str) -> str:
        """Initialize session with explicit user instruction."""
        session_id = f"agent_sess_{os.urandom(4).hex()}"
        create_session(session_id=session_id, user_instruction=user_instruction, db_path=self.db_path)
        register_text_provenance(
            session_id=session_id,
            source_id="user_instruction",
            source_type="user_instruction",
            trust_level="user",
            text=user_instruction,
            db_path=self.db_path
        )
        self.session_id = session_id
        return session_id

    def execute_plan(self, actions: List[Dict[str, Any]], auto_approve: bool = False) -> List[Dict[str, Any]]:
        """
        Execute an ordered series of planned actions through AgentGate.
        Halts immediately on BLOCK or PAUSE (unless auto_approve is simulated).
        """
        if not self.session_id:
            raise RuntimeError("Agent must start a session before executing actions.")

        results = []
        for idx, act in enumerate(actions, 1):
            tool = act["tool"]
            args = act["arguments"]

            # 1. Gate evaluation
            decision_res = evaluate_action_decision(
                session_id=self.session_id,
                tool=tool,
                arguments=args,
                db_path=self.db_path
            )

            step_summary = {
                "step": idx,
                "tool": tool,
                "arguments": args,
                "decision": decision_res.decision.value,
                "reasons": decision_res.reasons,
                "risk_score": decision_res.risk_score,
                "output": None
            }

            if decision_res.decision == DecisionType.ALLOW:
                # Gateway executes tool
                exec_res = execute_tool(
                    tool_name=tool,
                    arguments=args,
                    session_id=self.session_id,
                    db_path=self.db_path
                )
                step_summary["output"] = exec_res

            elif decision_res.decision == DecisionType.PAUSE:
                app_item = create_approval(
                    session_id=self.session_id,
                    tool=tool,
                    arguments=args,
                    decision_reasons=decision_res.reasons,
                    db_path=self.db_path
                )
                step_summary["pending_approval_id"] = app_item["id"]
                step_summary["impact_preview"] = app_item["impact_preview"]

                if auto_approve:
                    # Simulated human approval
                    resolved = resolve_approval(app_item["id"], action="approve", db_path=self.db_path)
                    step_summary["approval_status"] = "APPROVED"
                    step_summary["output"] = resolved.get("approval", {}).get("execution_result")
                else:
                    results.append(step_summary)
                    break  # Paused: awaiting human approval

            elif decision_res.decision == DecisionType.BLOCK:
                results.append(step_summary)
                break  # Blocked: agent must abort

            results.append(step_summary)

        return results
