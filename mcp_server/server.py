"""
Model Context Protocol (MCP) Server for AgentGate.
Exposes business tools via MCP while strictly enforcing that every tool call
passes through AgentGate's central execution boundary.
"""

import sys
import os
from typing import Dict, Any, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from mcp.server.mcpserver import MCPServer
from gateway.execution import execute_tool

mcp = MCPServer("AgentGate-Gateway-MCP")


@mcp.tool()
def read_customers(limit: int = 10, fields: Optional[List[str]] = None) -> Dict[str, Any]:
    """Read customer records through AgentGate boundary."""
    return execute_tool("read_customers", {"limit": limit, "fields": fields or ["id", "name", "email"]})


@mcp.tool()
def export_csv(table: str = "customers", row_count: int = 50) -> Dict[str, Any]:
    """Export table records to sandbox CSV through AgentGate boundary."""
    return execute_tool("export_csv", {"table": table, "row_count": row_count})


@mcp.tool()
def send_email(to: str, subject: str, body: str, attachment: Optional[str] = None) -> Dict[str, Any]:
    """Queue email in mock outbox through AgentGate boundary."""
    return execute_tool("send_email", {"to": to, "subject": subject, "body": body, "attachment": attachment})


@mcp.tool()
def issue_refund(order_id: str, amount: float) -> Dict[str, Any]:
    """Issue order refund through AgentGate boundary."""
    return execute_tool("issue_refund", {"order_id": order_id, "amount": amount})


@mcp.tool()
def read_document(doc_id: str) -> Dict[str, Any]:
    """Read internal document through AgentGate boundary."""
    return execute_tool("read_document", {"doc_id": doc_id})


if __name__ == "__main__":
    mcp.run()
