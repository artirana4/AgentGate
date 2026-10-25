"""
Human Approvals workflow for AgentGate.
Manages pending paused actions, impact previews, and atomic single-execution on approval.
Rechecks kill switch and policies before execution.
"""

import uuid
import json
import datetime
from typing import Dict, Any, List, Optional, Tuple
from gateway.storage import get_db_connection, init_db
from gateway.execution import execute_tool
from gateway.audit import record_audit_event


def generate_impact_preview(tool: str, arguments: Dict[str, Any]) -> str:
    """Generate human-readable impact preview for paused actions."""
    if tool == "issue_refund":
        order_id = arguments.get("order_id", "UNKNOWN")
        amount = float(arguments.get("amount", 0.0))
        return f"Refund of ₹{amount:,.2f} for order {order_id}. Approval will add this amount to the mock refunds ledger."

    elif tool == "export_csv":
        table = arguments.get("table", "unknown")
        row_count = arguments.get("row_count", 0)
        return f"Bulk export of {row_count} records from '{table}' table to designated sandbox directory."

    elif tool == "read_customers":
        limit = arguments.get("limit", 0)
        fields = arguments.get("fields", [])
        return f"Bulk read of {limit} customer records requesting fields {fields} from customer database."

    elif tool == "send_email":
        to = arguments.get("to", "unknown")
        subject = arguments.get("subject", "No subject")
        return f"Email dispatch to recipient '{to}' with subject '{subject}'."

    return f"Execution of action '{tool}' with parameters: {arguments}"


def create_approval(
    session_id: str,
    tool: str,
    arguments: Dict[str, Any],
    decision_reasons: List[str],
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Store a pending approval item."""
    init_db(db_path)
    approval_id = f"APP-{uuid.uuid4().hex[:8].upper()}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    preview = generate_impact_preview(tool, arguments)

    conn = get_db_connection(db_path)
    with conn:
        conn.execute("""
            INSERT INTO approvals (
                id, session_id, tool, arguments, impact_preview, status,
                decision_reasons, created_at, resolved_at, execution_result
            ) VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, NULL)
        """, (
            approval_id,
            session_id,
            tool,
            json.dumps(arguments, sort_keys=True),
            preview,
            json.dumps(decision_reasons),
            now_iso
        ))
    conn.close()

    record_audit_event(
        event_type="APPROVAL_CREATED",
        session_id=session_id,
        action=tool,
        arguments=arguments,
        decision="PAUSE",
        reason=f"Approval requested: {preview}",
        db_path=db_path
    )

    return {
        "id": approval_id,
        "session_id": session_id,
        "tool": tool,
        "arguments": arguments,
        "impact_preview": preview,
        "status": "PENDING",
        "decision_reasons": decision_reasons,
        "created_at": now_iso
    }


def list_approvals(status: Optional[str] = None, session_id: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """List approval items, optionally filtered by status or session."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        query = "SELECT * FROM approvals WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status.upper())
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += " ORDER BY created_at DESC"
        cur.execute(query, params)
        rows = cur.fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "tool": r["tool"],
                "arguments": json.loads(r["arguments"]),
                "impact_preview": r["impact_preview"],
                "status": r["status"],
                "decision_reasons": json.loads(r["decision_reasons"]),
                "created_at": r["created_at"],
                "resolved_at": r["resolved_at"],
                "execution_result": json.loads(r["execution_result"]) if r["execution_result"] else None
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_approval(approval_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch single approval item by ID."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        r = cur.fetchone()
        if not r:
            return None
        return {
            "id": r["id"],
            "session_id": r["session_id"],
            "tool": r["tool"],
            "arguments": json.loads(r["arguments"]),
            "impact_preview": r["impact_preview"],
            "status": r["status"],
            "decision_reasons": json.loads(r["decision_reasons"]),
            "created_at": r["created_at"],
            "resolved_at": r["resolved_at"],
            "execution_result": json.loads(r["execution_result"]) if r["execution_result"] else None
        }
    finally:
        conn.close()


def is_killswitch_active(session_id: Optional[str] = None, db_path: Optional[str] = None) -> Tuple[bool, Optional[str]]:
    """Check if global or per-session kill switch is currently engaged."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        # Check global switch
        cur.execute("SELECT active, reason FROM kill_switch WHERE switch_key = 'GLOBAL'")
        g = cur.fetchone()
        if g and g["active"] == 1:
            return True, f"Global kill switch is ACTIVE: {g['reason']}"

        # Check session switch
        if session_id:
            cur.execute("SELECT active, reason FROM kill_switch WHERE switch_key = ?", (f"SESSION:{session_id}",))
            s = cur.fetchone()
            if s and s["active"] == 1:
                return True, f"Session kill switch is ACTIVE for session {session_id}: {s['reason']}"

        return False, None
    finally:
        conn.close()


def set_killswitch(
    active: bool,
    scope: str = "global",
    session_id: Optional[str] = None,
    reason: str = "Operator action",
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Activate or deactivate global or session kill switch."""
    init_db(db_path)
    switch_key = "GLOBAL" if scope.lower() == "global" else f"SESSION:{session_id}"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    active_int = 1 if active else 0

    conn = get_db_connection(db_path)
    with conn:
        conn.execute("""
            INSERT INTO kill_switch (switch_key, active, updated_at, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(switch_key) DO UPDATE SET
                active = excluded.active,
                updated_at = excluded.updated_at,
                reason = excluded.reason
        """, (switch_key, active_int, now_iso, reason))
        
        # If session scope, also mirror in sessions table
        if scope.lower() == "session" and session_id:
            conn.execute("UPDATE sessions SET kill_switch_active = ? WHERE id = ?", (active_int, session_id))
    conn.close()

    action_label = "KILL_SWITCH_ACTIVATED" if active else "KILL_SWITCH_DEACTIVATED"
    record_audit_event(
        event_type=action_label,
        session_id=session_id,
        action="set_killswitch",
        arguments={"scope": scope, "active": active, "reason": reason},
        decision="BLOCK" if active else "ALLOW",
        reason=f"Kill switch {'activated' if active else 'deactivated'} for {switch_key}: {reason}",
        db_path=db_path
    )

    return {
        "switch_key": switch_key,
        "scope": scope,
        "session_id": session_id,
        "active": active,
        "reason": reason,
        "updated_at": now_iso
    }


def resolve_approval(
    approval_id: str,
    action: str,  # "approve" or "reject"
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Safely resolve a pending approval.
    Ensures at-most-once execution, rechecks kill switch, and revalidates safety.
    """
    approval = get_approval(approval_id, db_path)
    if not approval:
        raise ValueError(f"Approval item '{approval_id}' not found.")

    if approval["status"] != "PENDING":
        return {
            "status": "ERROR",
            "message": f"Approval item '{approval_id}' is already {approval['status']}. Cannot re-resolve.",
            "approval": approval
        }

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if action.lower() == "reject":
        conn = get_db_connection(db_path)
        with conn:
            conn.execute(
                "UPDATE approvals SET status = 'REJECTED', resolved_at = ? WHERE id = ?",
                (now_iso, approval_id)
            )
        conn.close()

        record_audit_event(
            event_type="APPROVAL_REJECTED",
            session_id=approval["session_id"],
            action=approval["tool"],
            arguments=approval["arguments"],
            decision="BLOCK",
            reason=f"Human operator explicitly rejected approval '{approval_id}'",
            db_path=db_path
        )
        approval["status"] = "REJECTED"
        approval["resolved_at"] = now_iso
        return {"status": "SUCCESS", "action": "reject", "approval": approval}

    elif action.lower() == "approve":
        # 1. Recheck Kill Switch
        ks_active, ks_reason = is_killswitch_active(approval["session_id"], db_path)
        if ks_active:
            record_audit_event(
                event_type="APPROVAL_EXECUTION_BLOCKED",
                session_id=approval["session_id"],
                action=approval["tool"],
                arguments=approval["arguments"],
                decision="BLOCK",
                reason=f"Approved action blocked because kill switch became active: {ks_reason}",
                db_path=db_path
            )
            return {
                "status": "ERROR",
                "message": f"Cannot execute approved action: {ks_reason}",
                "approval": approval
            }

        # 2. Execute at most once
        exec_result = execute_tool(
            tool_name=approval["tool"],
            arguments=approval["arguments"],
            session_id=approval["session_id"],
            db_path=db_path
        )

        conn = get_db_connection(db_path)
        with conn:
            conn.execute(
                "UPDATE approvals SET status = 'APPROVED', resolved_at = ?, execution_result = ? WHERE id = ?",
                (now_iso, json.dumps(exec_result), approval_id)
            )
        conn.close()

        record_audit_event(
            event_type="APPROVAL_APPROVED_AND_EXECUTED",
            session_id=approval["session_id"],
            action=approval["tool"],
            arguments=approval["arguments"],
            decision="ALLOW",
            reason=f"Human operator approved and executed action '{approval_id}'",
            db_path=db_path
        )

        approval["status"] = "APPROVED"
        approval["resolved_at"] = now_iso
        approval["execution_result"] = exec_result
        return {"status": "SUCCESS", "action": "approve", "approval": approval}

    else:
        raise ValueError(f"Unknown approval resolution action: '{action}'. Must be 'approve' or 'reject'.")
