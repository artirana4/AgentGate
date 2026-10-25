"""
Data models and schemas for AgentGate.
"""

from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class DecisionType(str, Enum):
    ALLOW = "ALLOW"
    PAUSE = "PAUSE"
    BLOCK = "BLOCK"


class TrustLevel(str, Enum):
    USER = "user"
    SYSTEM = "system"
    UNTRUSTED = "untrusted"


class ActionRequest(BaseModel):
    session_id: str = Field(..., description="Target session ID")
    tool: str = Field(..., description="Tool name to execute, e.g. read_customers, send_email")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool")
    scenario_id: Optional[str] = Field(None, description="Optional benchmark scenario ID")


class ActionDecision(BaseModel):
    decision: DecisionType
    reasons: List[str] = Field(default_factory=list)
    risk_score: int = 0
    risk_contributions: List[Dict[str, Any]] = Field(default_factory=list)
    provenance_notes: List[str] = Field(default_factory=list)
    pending_approval_id: Optional[str] = None
    execution_result: Optional[Dict[str, Any]] = None
    executed: bool = False


class SessionCreate(BaseModel):
    user_instruction: str = Field(..., min_length=1, description="Explicit user instruction (trusted)")
    session_id: Optional[str] = Field(None, description="Optional custom session ID")


class SessionResponse(BaseModel):
    id: str
    user_instruction: str
    created_at: str
    risk_score: int = 0
    status: str = "ACTIVE"
    kill_switch_active: bool = False
    trace: List[Dict[str, Any]] = Field(default_factory=list)


class ApprovalItem(BaseModel):
    id: str
    session_id: str
    tool: str
    arguments: Dict[str, Any]
    impact_preview: str
    status: str  # PENDING, APPROVED, REJECTED
    decision_reasons: List[str]
    created_at: str
    resolved_at: Optional[str] = None
    execution_result: Optional[Dict[str, Any]] = None


class KillSwitchRequest(BaseModel):
    scope: str = Field(default="global", description="'global' or 'session'")
    session_id: Optional[str] = Field(None, description="Session ID if scope is 'session'")
    active: bool = Field(..., description="True to activate kill switch, False to deactivate")
    reason: Optional[str] = Field("Operator action", description="Reason for status change")


class AuditVerifyResponse(BaseModel):
    verified: bool
    total_events: int
    first_invalid_index: Optional[int] = None
    first_invalid_event_id: Optional[str] = None
    details: str
