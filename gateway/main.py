"""
FastAPI application for AgentGate Security Gateway.
Serves API endpoints for session management, tool execution interception,
approvals workflow, kill switch control, and tamper-evident audit logs.
"""

import os
import uuid
from contextlib import asynccontextmanager
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from gateway.models import (
    SessionCreate,
    SessionResponse,
    ActionRequest,
    ActionDecision,
    DecisionType,
    ApprovalItem,
    KillSwitchRequest,
    AuditVerifyResponse
)
from gateway.storage import (
    init_db,
    create_session,
    get_session,
    list_sessions,
    update_session_risk,
    add_session_trace
)
from gateway.audit import record_audit_event, get_audit_events, verify_audit_chain
from gateway.provenance import register_text_provenance
from gateway.decision_engine import evaluate_action_decision
from gateway.execution import execute_tool
from gateway.approvals import (
    create_approval,
    list_approvals,
    get_approval,
    resolve_approval,
    is_killswitch_active,
    set_killswitch
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="AgentGate Security Gateway",
    description="Interception and Control Gateway between AI Agents and Business Systems",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["System"])
def health_check():
    return {"status": "HEALTHY", "gateway": "AgentGate", "version": "1.0.0"}


@app.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, tags=["Sessions"])
def api_create_session(payload: SessionCreate):
    session_id = payload.session_id or f"sess_{uuid.uuid4().hex[:10]}"
    sess = create_session(session_id=session_id, user_instruction=payload.user_instruction)
    
    # Register explicit user instruction as trusted user provenance
    register_text_provenance(
        session_id=session_id,
        source_id="user_instruction",
        source_type="user_instruction",
        trust_level="user",
        text=payload.user_instruction
    )
    
    record_audit_event(
        event_type="SESSION_CREATED",
        session_id=session_id,
        action="create_session",
        arguments={"user_instruction": payload.user_instruction},
        decision="ALLOW",
        reason="Session created with explicit trusted user instruction."
    )
    
    return sess


@app.get("/sessions", response_model=List[Dict[str, Any]], tags=["Sessions"])
def api_list_sessions():
    return list_sessions()


@app.get("/sessions/{session_id}", response_model=SessionResponse, tags=["Sessions"])
def api_get_session(session_id: str):
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return sess


@app.post("/execute", response_model=ActionDecision, tags=["Gateway"])
def api_execute(payload: ActionRequest):
    sess = get_session(payload.session_id)
    if not sess:
        raise HTTPException(status_code=404, detail=f"Session '{payload.session_id}' not found. Please create session first.")

    decision_res = evaluate_action_decision(
        session_id=payload.session_id,
        tool=payload.tool,
        arguments=payload.arguments
    )

    execution_result = None
    pending_app_id = None
    executed = False

    if decision_res.decision == DecisionType.ALLOW:
        execution_result = execute_tool(
            tool_name=payload.tool,
            arguments=payload.arguments,
            session_id=payload.session_id
        )
        executed = True
        record_audit_event(
            event_type="ACTION_EXECUTED",
            session_id=payload.session_id,
            action=payload.tool,
            arguments=payload.arguments,
            decision="ALLOW",
            reason="; ".join(decision_res.reasons)
        )

    elif decision_res.decision == DecisionType.PAUSE:
        app_item = create_approval(
            session_id=payload.session_id,
            tool=payload.tool,
            arguments=payload.arguments,
            decision_reasons=decision_res.reasons
        )
        pending_app_id = app_item["id"]

    elif decision_res.decision == DecisionType.BLOCK:
        record_audit_event(
            event_type="ACTION_BLOCKED",
            session_id=payload.session_id,
            action=payload.tool,
            arguments=payload.arguments,
            decision="BLOCK",
            reason="; ".join(decision_res.reasons)
        )

    # Persist trace
    add_session_trace(
        session_id=payload.session_id,
        tool=payload.tool,
        arguments=payload.arguments,
        decision=decision_res.decision.value,
        reasons=decision_res.reasons,
        risk_score=decision_res.risk_score,
        risk_contributions=decision_res.risk_contributions,
        provenance_notes=decision_res.provenance_notes,
        execution_result=execution_result
    )

    update_session_risk(payload.session_id, decision_res.risk_score)

    return ActionDecision(
        decision=decision_res.decision,
        reasons=decision_res.reasons,
        risk_score=decision_res.risk_score,
        risk_contributions=decision_res.risk_contributions,
        provenance_notes=decision_res.provenance_notes,
        pending_approval_id=pending_app_id,
        execution_result=execution_result,
        executed=executed
    )


@app.get("/events", tags=["Audit"])
def api_get_events(limit: int = Query(default=100, ge=1, le=1000), session_id: Optional[str] = None):
    return get_audit_events(limit=limit, session_id=session_id)


@app.get("/audit/verify", response_model=AuditVerifyResponse, tags=["Audit"])
def api_verify_audit():
    result = verify_audit_chain()
    return AuditVerifyResponse(**result)


@app.get("/approvals", tags=["Approvals"])
def api_list_approvals(status: Optional[str] = None, session_id: Optional[str] = None):
    return list_approvals(status=status, session_id=session_id)


@app.post("/approvals/{approval_id}/approve", tags=["Approvals"])
def api_approve(approval_id: str):
    try:
        res = resolve_approval(approval_id, action="approve")
        if res.get("status") == "ERROR":
            raise HTTPException(status_code=400, detail=res.get("message"))
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/approvals/{approval_id}/reject", tags=["Approvals"])
def api_reject(approval_id: str):
    try:
        res = resolve_approval(approval_id, action="reject")
        if res.get("status") == "ERROR":
            raise HTTPException(status_code=400, detail=res.get("message"))
        return res
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/killswitch", tags=["Control"])
def api_set_killswitch(payload: KillSwitchRequest):
    return set_killswitch(
        active=payload.active,
        scope=payload.scope,
        session_id=payload.session_id,
        reason=payload.reason or "Operator request via API"
    )


@app.get("/killswitch", tags=["Control"])
def api_get_killswitch(session_id: Optional[str] = None):
    active, reason = is_killswitch_active(session_id=session_id)
    return {
        "active": active,
        "reason": reason or "Normal operation (inactive)",
        "scope": "session" if session_id else "global",
        "session_id": session_id
    }
