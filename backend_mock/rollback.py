"""
Compensating rollback extension for refunds ledger.
Implements accounting reversal without mutating or deleting historical records.
"""

import uuid
import datetime
from typing import Dict, Any, Optional
from backend_mock.tools import get_db_connection, init_mock_db
from gateway.audit import record_audit_event


def rollback_refund(
    refund_id: str,
    reason: str = "Operator manual rollback",
    session_id: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Issues a compensating reversal entry in mock_refunds.
    Does NOT mutate, delete, or rewrite historical ledger records.
    """
    init_mock_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM mock_refunds WHERE refund_id = ?", (refund_id,))
        orig = cur.fetchone()
        if not orig:
            raise ValueError(f"Historical refund '{refund_id}' not found.")

        if orig["status"] == "REVERSED":
            raise ValueError(f"Refund '{refund_id}' has already been reversed.")

        rev_id = f"REV-{uuid.uuid4().hex[:8].upper()}"
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        reversed_amount = -abs(float(orig["amount"]))

        with conn:
            conn.execute("""
                INSERT INTO mock_refunds (refund_id, order_id, amount, status, created_at)
                VALUES (?, ?, ?, 'COMPENSATING_REVERSAL', ?)
            """, (rev_id, orig["order_id"], reversed_amount, now_str))

        record_audit_event(
            event_type="REFUND_ROLLBACK_COMPENSATED",
            session_id=session_id,
            action="rollback_refund",
            arguments={"original_refund_id": refund_id, "compensating_id": rev_id, "amount": reversed_amount, "reason": reason},
            decision="ALLOW",
            reason=f"Compensating reversal issued for {refund_id}. Historical ledger preserved.",
            db_path=db_path
        )

        return {
            "status": "REFUND_REVERSED",
            "compensating_refund_id": rev_id,
            "original_refund_id": refund_id,
            "order_id": orig["order_id"],
            "reversal_amount": reversed_amount,
            "created_at": now_str,
            "notes": "Historical ledger entry preserved; compensating reversal recorded."
        }
    finally:
        conn.close()
