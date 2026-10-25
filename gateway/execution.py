"""
Central execution gateway for mock business tools.
Enforces that every mock tool is invoked through a single gateway-controlled boundary.
"""

from typing import Dict, Any, Optional
from backend_mock.tools import (
    read_customers,
    export_csv,
    send_email,
    issue_refund,
    read_document,
    ReadCustomersInput,
    ExportCsvInput,
    SendEmailInput,
    IssueRefundInput,
    ReadDocumentInput
)
from gateway.provenance import register_provenance, register_text_provenance
from backend_mock.rollback import rollback_refund


def execute_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    session_id: Optional[str] = None,
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Execute tool through single gateway boundary.
    Validates arguments, executes mock business logic, registers resulting provenance.
    """
    try:
        if tool_name == "read_customers":
            limit = arguments.get("limit", 100)
            fields = arguments.get("fields", ["id", "name", "email"])
            output = read_customers(limit=limit, fields=fields, db_path=db_path)
            return {"status": "SUCCESS", "tool": tool_name, "output": output}

        elif tool_name == "export_csv":
            table = arguments.get("table", "customers")
            row_count = arguments.get("row_count", 100)
            output = export_csv(table=table, row_count=row_count, db_path=db_path)
            if session_id and output.get("filename"):
                register_provenance(
                    session_id=session_id,
                    source_id="export_csv",
                    source_type="system",
                    trust_level="system",
                    extracted_value=output["filename"],
                    db_path=db_path
                )
            return {"status": "SUCCESS", "tool": tool_name, "output": output}

        elif tool_name == "send_email":
            to = arguments.get("to")
            subject = arguments.get("subject", "Notification")
            body = arguments.get("body", "")
            attachment = arguments.get("attachment")
            output = send_email(to=to, subject=subject, body=body, attachment=attachment, db_path=db_path)
            return {"status": "SUCCESS", "tool": tool_name, "output": output}

        elif tool_name == "issue_refund":
            order_id = arguments.get("order_id")
            amount = float(arguments.get("amount", 0.0))
            output = issue_refund(order_id=order_id, amount=amount, db_path=db_path)
            return {"status": "SUCCESS", "tool": tool_name, "output": output}

        elif tool_name == "rollback_refund":
            ref_id = arguments.get("refund_id")
            reason = arguments.get("reason", "Compensating reversal")
            output = rollback_refund(refund_id=ref_id, reason=reason, session_id=session_id, db_path=db_path)
            return {"status": "SUCCESS", "tool": tool_name, "output": output}

        elif tool_name == "read_document":
            doc_id = arguments.get("doc_id")
            output = read_document(doc_id=doc_id)
            if session_id:
                # Untrusted document provenance registration
                register_text_provenance(
                    session_id=session_id,
                    source_id=doc_id,
                    source_type="document",
                    trust_level="untrusted",
                    text=output["content"],
                    db_path=db_path
                )
            return {"status": "SUCCESS", "tool": tool_name, "output": output}

        else:
            return {
                "status": "ERROR",
                "tool": tool_name,
                "error": f"Unknown tool: '{tool_name}'. Allowed: read_customers, export_csv, send_email, issue_refund, read_document"
            }

    except Exception as e:
        return {
            "status": "ERROR",
            "tool": tool_name,
            "error": str(e)
        }
