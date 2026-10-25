"""
Optional LLM Agent for AgentGate.
Provides an autonomous tool-calling loop using external LLMs (e.g. OpenAI or Gemini).
DISABLED BY DEFAULT. Requires explicit provider configuration and API key.
All tool calls are strictly intercepted and routed through AgentGate before execution.
"""

import os
import sys
import json
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from gateway.storage import create_session
from gateway.provenance import register_text_provenance
from gateway.decision_engine import evaluate_action_decision
from gateway.execution import execute_tool
from gateway.approvals import create_approval
from gateway.models import DecisionType


class LLMAgent:
    """
    LLM-powered agent with safety interception.
    Requires environment variables: AGENTGATE_LLM_PROVIDER and AGENTGATE_LLM_API_KEY.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.provider = os.environ.get("AGENTGATE_LLM_PROVIDER")
        self.api_key = os.environ.get("AGENTGATE_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self.db_path = db_path
        self.session_id: Optional[str] = None

    @property
    def is_enabled(self) -> bool:
        return bool(self.provider and self.api_key)

    def run_step(self, user_instruction: str, planned_tool: str, planned_args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run a tool call through AgentGate safety gateway.
        If disabled, refuses execution safely.
        """
        if not self.is_enabled:
            return {
                "status": "DISABLED",
                "message": "LLM agent is disabled by default. Configure AGENTGATE_LLM_PROVIDER and AGENTGATE_LLM_API_KEY to enable.",
                "executed": False
            }

        if not self.session_id:
            self.session_id = f"llm_sess_{os.urandom(4).hex()}"
            create_session(self.session_id, user_instruction, db_path=self.db_path)
            register_text_provenance(
                session_id=self.session_id,
                source_id="user_instruction",
                source_type="user_instruction",
                trust_level="user",
                text=user_instruction,
                db_path=self.db_path
            )

        # Gateway intercept
        dec = evaluate_action_decision(
            session_id=self.session_id,
            tool=planned_tool,
            arguments=planned_args,
            db_path=self.db_path
        )

        if dec.decision == DecisionType.ALLOW:
            result = execute_tool(planned_tool, planned_args, session_id=self.session_id, db_path=self.db_path)
            return {"status": "SUCCESS", "decision": "ALLOW", "result": result, "executed": True}
        elif dec.decision == DecisionType.PAUSE:
            app_item = create_approval(self.session_id, planned_tool, planned_args, dec.reasons, db_path=self.db_path)
            return {"status": "PAUSED", "decision": "PAUSE", "approval_id": app_item["id"], "reasons": dec.reasons, "executed": False}
        else:
            return {"status": "BLOCKED", "decision": "BLOCK", "reasons": dec.reasons, "executed": False}
