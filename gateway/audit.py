"""
Tamper-evident audit logging for AgentGate.
Uses canonical SHA-256 hash chaining to detect any modifications to the audit log.
Note: A local audit hash chain is tamper-evident, not fully tamper-proof
if an attacker has direct database access and recalculates all subsequent hashes.
"""

import hashlib
import json
import uuid
import datetime
from typing import Dict, Any, List, Optional, Tuple
from gateway.storage import get_db_connection, init_db, DEFAULT_DB_PATH

GENESIS_HASH = "0" * 64


def canonical_hash(
    event_id: str,
    timestamp: str,
    event_type: str,
    session_id: Optional[str],
    action: Optional[str],
    arguments: Optional[Dict[str, Any]],
    decision: Optional[str],
    reason: Optional[str],
    previous_hash: str
) -> str:
    """Compute deterministic SHA-256 hash of canonical representation."""
    canonical_payload = {
        "event_id": event_id,
        "timestamp": timestamp,
        "event_type": event_type,
        "session_id": session_id or "",
        "action": action or "",
        "arguments": json.dumps(arguments or {}, sort_keys=True),
        "decision": decision or "",
        "reason": reason or "",
        "previous_hash": previous_hash
    }
    canonical_str = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


def record_audit_event(
    event_type: str,
    session_id: Optional[str] = None,
    action: Optional[str] = None,
    arguments: Optional[Dict[str, Any]] = None,
    decision: Optional[str] = None,
    reason: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Append a new tamper-evident audit event to SQLite log."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        # Find latest event's current_hash to use as previous_hash
        cur.execute("SELECT current_hash FROM audit_log ORDER BY rowid DESC LIMIT 1")
        last_row = cur.fetchone()
        previous_hash = last_row["current_hash"] if last_row else GENESIS_HASH
        
        event_id = f"EVT-{uuid.uuid4().hex[:12].upper()}"
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        curr_hash = canonical_hash(
            event_id=event_id,
            timestamp=timestamp,
            event_type=event_type,
            session_id=session_id,
            action=action,
            arguments=arguments,
            decision=decision,
            reason=reason,
            previous_hash=previous_hash
        )
        
        with conn:
            cur.execute("""
                INSERT INTO audit_log (
                    event_id, timestamp, event_type, session_id, action, arguments,
                    decision, reason, previous_hash, current_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id,
                timestamp,
                event_type,
                session_id,
                action,
                json.dumps(arguments, sort_keys=True) if arguments else None,
                decision,
                reason,
                previous_hash,
                curr_hash
            ))
            
        return {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": event_type,
            "session_id": session_id,
            "action": action,
            "arguments": arguments,
            "decision": decision,
            "reason": reason,
            "previous_hash": previous_hash,
            "current_hash": curr_hash
        }
    finally:
        conn.close()


def get_audit_events(limit: int = 100, session_id: Optional[str] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve audit events, optionally filtered by session."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        if session_id:
            cur.execute("SELECT * FROM audit_log WHERE session_id = ? ORDER BY rowid DESC LIMIT ?", (session_id, limit))
        else:
            cur.execute("SELECT * FROM audit_log ORDER BY rowid DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        events = []
        for r in rows:
            events.append({
                "event_id": r["event_id"],
                "timestamp": r["timestamp"],
                "event_type": r["event_type"],
                "session_id": r["session_id"],
                "action": r["action"],
                "arguments": json.loads(r["arguments"]) if r["arguments"] else None,
                "decision": r["decision"],
                "reason": r["reason"],
                "previous_hash": r["previous_hash"],
                "current_hash": r["current_hash"]
            })
        return events
    finally:
        conn.close()


def verify_audit_chain(db_path: Optional[str] = None) -> Dict[str, Any]:
    """Verify integrity of the audit log hash chain."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM audit_log ORDER BY rowid ASC")
        rows = cur.fetchall()
        
        if not rows:
            return {
                "verified": True,
                "total_events": 0,
                "first_invalid_index": None,
                "first_invalid_event_id": None,
                "details": "Audit chain is empty. Clean state."
            }
        
        expected_prev = GENESIS_HASH
        for idx, row in enumerate(rows):
            # Check previous hash link
            if row["previous_hash"] != expected_prev:
                return {
                    "verified": False,
                    "total_events": len(rows),
                    "first_invalid_index": idx,
                    "first_invalid_event_id": row["event_id"],
                    "details": f"Broken link at index {idx}: expected prev_hash {expected_prev}, got {row['previous_hash']}"
                }
            
            # Check current hash integrity
            args_dict = json.loads(row["arguments"]) if row["arguments"] else {}
            recomputed = canonical_hash(
                event_id=row["event_id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                session_id=row["session_id"],
                action=row["action"],
                arguments=args_dict,
                decision=row["decision"],
                reason=row["reason"],
                previous_hash=row["previous_hash"]
            )
            
            if row["current_hash"] != recomputed:
                return {
                    "verified": False,
                    "total_events": len(rows),
                    "first_invalid_index": idx,
                    "first_invalid_event_id": row["event_id"],
                    "details": f"Hash mismatch at index {idx} ({row['event_id']}): stored {row['current_hash']} != computed {recomputed}"
                }
            
            expected_prev = row["current_hash"]
            
        return {
            "verified": True,
            "total_events": len(rows),
            "first_invalid_index": None,
            "first_invalid_event_id": None,
            "details": f"All {len(rows)} events in hash chain verified successfully."
        }
    finally:
        conn.close()
