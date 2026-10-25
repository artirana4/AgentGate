"""
Persistent SQLite storage for AgentGate.
Provides isolated transactions, session management, trace logging, approvals, and kill switch state.
"""

import os
import sqlite3
import json
import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path

DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agentgate.db"))


def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    """Initialize all AgentGate tables."""
    conn = get_db_connection(db_path)
    with conn:
        cursor = conn.cursor()
        
        # Sessions
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_instruction TEXT NOT NULL,
                created_at TEXT NOT NULL,
                risk_score INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ACTIVE',
                kill_switch_active INTEGER DEFAULT 0
            )
        """)
        
        # Session traces
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS session_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                decision TEXT NOT NULL,
                reasons TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                risk_contributions TEXT NOT NULL,
                provenance_notes TEXT NOT NULL,
                execution_result TEXT,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        
        # Provenance registry
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS provenance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_type TEXT NOT NULL,
                trust_level TEXT NOT NULL,
                extracted_value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        
        # Approvals
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                tool TEXT NOT NULL,
                arguments TEXT NOT NULL,
                impact_preview TEXT NOT NULL,
                status TEXT NOT NULL,
                decision_reasons TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                execution_result TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        
        # Kill switch (global key is 'GLOBAL', session keys are 'SESSION:{id}')
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch (
                switch_key TEXT PRIMARY KEY,
                active INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                reason TEXT
            )
        """)
        
        # Initialize global switch to inactive if absent
        cursor.execute("SELECT switch_key FROM kill_switch WHERE switch_key = 'GLOBAL'")
        if not cursor.fetchone():
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO kill_switch (switch_key, active, updated_at, reason) VALUES ('GLOBAL', 0, ?, 'Initial state')",
                (now_iso,)
            )
        
        # Audit Log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                event_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                session_id TEXT,
                action TEXT,
                arguments TEXT,
                decision TEXT,
                reason TEXT,
                previous_hash TEXT NOT NULL,
                current_hash TEXT NOT NULL
            )
        """)
    conn.close()


# --- Sessions Operations ---

def create_session(session_id: str, user_instruction: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    init_db(db_path)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, user_instruction, created_at, risk_score, status, kill_switch_active) VALUES (?, ?, ?, 0, 'ACTIVE', 0)",
            (session_id, user_instruction, now_iso)
        )
    conn.close()
    return {
        "id": session_id,
        "user_instruction": user_instruction,
        "created_at": now_iso,
        "risk_score": 0,
        "status": "ACTIVE",
        "kill_switch_active": False,
        "trace": []
    }


def get_session(session_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        if not row:
            return None
        
        # Load trace
        cur.execute("SELECT * FROM session_traces WHERE session_id = ? ORDER BY step_index ASC", (session_id,))
        trace_rows = cur.fetchall()
        trace = []
        for tr in trace_rows:
            trace.append({
                "step_index": tr["step_index"],
                "tool": tr["tool"],
                "arguments": json.loads(tr["arguments"]),
                "decision": tr["decision"],
                "reasons": json.loads(tr["reasons"]),
                "risk_score": tr["risk_score"],
                "risk_contributions": json.loads(tr["risk_contributions"]),
                "provenance_notes": json.loads(tr["provenance_notes"]),
                "execution_result": json.loads(tr["execution_result"]) if tr["execution_result"] else None,
                "timestamp": tr["timestamp"]
            })
        
        return {
            "id": row["id"],
            "user_instruction": row["user_instruction"],
            "created_at": row["created_at"],
            "risk_score": row["risk_score"],
            "status": row["status"],
            "kill_switch_active": bool(row["kill_switch_active"]),
            "trace": trace
        }
    finally:
        conn.close()


def list_sessions(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM sessions ORDER BY created_at DESC")
        rows = cur.fetchall()
        return [
            {
                "id": r["id"],
                "user_instruction": r["user_instruction"],
                "created_at": r["created_at"],
                "risk_score": r["risk_score"],
                "status": r["status"],
                "kill_switch_active": bool(r["kill_switch_active"])
            }
            for r in rows
        ]
    finally:
        conn.close()


def update_session_risk(session_id: str, new_risk: int, db_path: Optional[str] = None) -> None:
    conn = get_db_connection(db_path)
    with conn:
        conn.execute("UPDATE sessions SET risk_score = ? WHERE id = ?", (new_risk, session_id))
    conn.close()


def add_session_trace(
    session_id: str,
    tool: str,
    arguments: Dict[str, Any],
    decision: str,
    reasons: List[str],
    risk_score: int,
    risk_contributions: List[Dict[str, Any]],
    provenance_notes: List[str],
    execution_result: Optional[Dict[str, Any]],
    db_path: Optional[str] = None
) -> int:
    init_db(db_path)
    conn = get_db_connection(db_path)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM session_traces WHERE session_id = ?", (session_id,))
        step_index = cur.fetchone()[0] + 1
        cur.execute("""
            INSERT INTO session_traces (
                session_id, step_index, tool, arguments, decision, reasons, risk_score, 
                risk_contributions, provenance_notes, execution_result, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            session_id,
            step_index,
            tool,
            json.dumps(arguments, sort_keys=True),
            decision,
            json.dumps(reasons),
            risk_score,
            json.dumps(risk_contributions),
            json.dumps(provenance_notes),
            json.dumps(execution_result) if execution_result is not None else None,
            now_iso
        ))
    conn.close()
    return step_index
