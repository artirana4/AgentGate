"""
Mock business tools for AgentGate.
All tools operate on synthetic data and local SQLite databases.
No external network calls, payment systems, or real email systems are contacted.
"""

import os
import sqlite3
import csv
import uuid
import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, field_validator

from backend_mock.seed import generate_customers, generate_orders, DOCUMENTS

DEFAULT_SANDBOX_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "sandbox"))
DEFAULT_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "agentgate.db"))

ALLOWED_CUSTOMER_FIELDS = {"id", "name", "email", "phone", "city"}
ALLOWED_TABLES = {"customers", "orders"}


# --- Pydantic Input Schemas ---

class ReadCustomersInput(BaseModel):
    limit: int = Field(..., ge=1, le=1000, description="Maximum number of customer records to return")
    fields: List[str] = Field(default_factory=lambda: ["id", "name", "email"], description="Fields to return")

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("At least one field must be requested")
        for f in v:
            if f not in ALLOWED_CUSTOMER_FIELDS:
                raise ValueError(f"Disallowed field: {f}. Allowed: {sorted(list(ALLOWED_CUSTOMER_FIELDS))}")
        return v


class ExportCsvInput(BaseModel):
    table: str = Field(..., description="Table name to export (e.g. customers, orders)")
    row_count: int = Field(..., ge=1, le=10000, description="Number of rows to export")

    @field_validator("table")
    @classmethod
    def validate_table(cls, v: str) -> str:
        if v not in ALLOWED_TABLES:
            raise ValueError(f"Disallowed table: {v}. Approved tables: {sorted(list(ALLOWED_TABLES))}")
        return v


class SendEmailInput(BaseModel):
    to: str = Field(..., min_length=3, description="Recipient email address")
    subject: str = Field(..., min_length=1, description="Email subject line")
    body: str = Field(..., description="Email body content")
    attachment: Optional[str] = Field(None, description="Optional path to attachment inside sandbox")


class IssueRefundInput(BaseModel):
    order_id: str = Field(..., min_length=1, description="Order identifier, e.g. ORD-1007")
    amount: float = Field(..., gt=0.0, description="Refund amount in currency units")


class ReadDocumentInput(BaseModel):
    doc_id: str = Field(..., min_length=1, description="Document identifier, e.g. invoice_482")


# --- Database Initialization & Helper ---

def get_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    target_path = db_path or DEFAULT_DB_PATH
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_mock_db(db_path: Optional[str] = None) -> None:
    """Initialize mock database with customers, orders, documents, outbox, and refunds ledger."""
    conn = get_db_connection(db_path)
    with conn:
        cursor = conn.cursor()
        
        # Customers table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mock_customers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                phone TEXT NOT NULL,
                city TEXT NOT NULL
            )
        """)
        
        # Orders table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mock_orders (
                id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL
            )
        """)
        
        # Email outbox table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mock_outbox (
                id TEXT PRIMARY KEY,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                attachment TEXT,
                created_at TEXT NOT NULL
            )
        """)
        
        # Refunds ledger table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS mock_refunds (
                refund_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (order_id) REFERENCES mock_orders (id)
            )
        """)
        
        # Check if customers need seeding
        cursor.execute("SELECT COUNT(*) FROM mock_customers")
        if cursor.fetchone()[0] == 0:
            customers = generate_customers(200)
            cursor.executemany(
                "INSERT INTO mock_customers (id, name, email, phone, city) VALUES (:id, :name, :email, :phone, :city)",
                customers
            )
        
        # Check if orders need seeding
        cursor.execute("SELECT COUNT(*) FROM mock_orders")
        if cursor.fetchone()[0] == 0:
            orders = generate_orders(50)
            cursor.executemany(
                "INSERT INTO mock_orders (id, customer_id, amount, status) VALUES (:id, :customer_id, :amount, :status)",
                orders
            )
    conn.close()


# --- Tool Implementations ---

def read_customers(limit: int, fields: Optional[List[str]] = None, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read customers with requested field filtering. Never exposes unrequested fields."""
    validated = ReadCustomersInput(limit=limit, fields=fields or ["id", "name", "email"])
    init_mock_db(db_path)
    conn = get_db_connection(db_path)
    try:
        col_list = ", ".join(validated.fields)
        cursor = conn.cursor()
        cursor.execute(f"SELECT {col_list} FROM mock_customers ORDER BY id ASC LIMIT ?", (validated.limit,))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            record = {}
            for f in validated.fields:
                record[f] = row[f]
            result.append(record)
        return result
    finally:
        conn.close()


def export_csv(table: str, row_count: int, sandbox_dir: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Export table data to a CSV file inside the sandbox directory. Prevents path traversal."""
    validated = ExportCsvInput(table=table, row_count=row_count)
    target_sandbox = os.path.abspath(sandbox_dir or DEFAULT_SANDBOX_DIR)
    os.makedirs(target_sandbox, exist_ok=True)
    
    init_mock_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        if validated.table == "customers":
            cursor.execute("SELECT id, name, email, phone, city FROM mock_customers ORDER BY id ASC LIMIT ?", (validated.row_count,))
            fieldnames = ["id", "name", "email", "phone", "city"]
        else:
            cursor.execute("SELECT id, customer_id, amount, status FROM mock_orders ORDER BY id ASC LIMIT ?", (validated.row_count,))
            fieldnames = ["id", "customer_id", "amount", "status"]
        
        rows = cursor.fetchall()
        actual_count = len(rows)
        
        # Generate safe filename and ensure it stays inside sandbox
        safe_filename = f"{validated.table}_export_{uuid.uuid4().hex[:8]}.csv"
        file_path = os.path.join(target_sandbox, safe_filename)
        
        # Verify no path traversal
        resolved_path = Path(file_path).resolve()
        if not str(resolved_path).startswith(str(Path(target_sandbox).resolve())):
            raise PermissionError("Path traversal detected! Exports must remain in sandbox.")
        
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows:
                writer.writerow(dict(r))
        
        return {
            "status": "SUCCESS",
            "table": validated.table,
            "filename": safe_filename,
            "filepath": str(resolved_path),
            "row_count": actual_count,
        }
    finally:
        conn.close()


def send_email(to: str, subject: str, body: str, attachment: Optional[str] = None, db_path: Optional[str] = None, sandbox_dir: Optional[str] = None) -> Dict[str, Any]:
    """Store email in SQLite mock outbox. Verifies attachments remain in sandbox."""
    validated = SendEmailInput(to=to, subject=subject, body=body, attachment=attachment)
    target_sandbox = os.path.abspath(sandbox_dir or DEFAULT_SANDBOX_DIR)
    
    if validated.attachment:
        # Check attachment path traversal
        att_path = os.path.abspath(os.path.join(target_sandbox, os.path.basename(validated.attachment)))
        resolved_att = Path(att_path).resolve()
        if not str(resolved_att).startswith(str(Path(target_sandbox).resolve())):
            raise PermissionError("Attachment path traversal detected!")
    
    init_mock_db(db_path)
    conn = get_db_connection(db_path)
    try:
        msg_id = f"MSG-{uuid.uuid4().hex[:8].upper()}"
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with conn:
            conn.execute(
                "INSERT INTO mock_outbox (id, recipient, subject, body, attachment, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (msg_id, validated.to, validated.subject, validated.body, validated.attachment, now_str)
            )
        return {
            "status": "QUEUED_IN_OUTBOX",
            "message_id": msg_id,
            "to": validated.to,
            "subject": validated.subject,
            "attachment": validated.attachment,
            "created_at": now_str,
        }
    finally:
        conn.close()


def issue_refund(order_id: str, amount: float, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Issue a refund for a valid order and store in refunds ledger."""
    validated = IssueRefundInput(order_id=order_id, amount=amount)
    init_mock_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, amount, status FROM mock_orders WHERE id = ?", (validated.order_id,))
        order = cursor.fetchone()
        if not order:
            raise ValueError(f"Order not found: {validated.order_id}")
        
        refund_id = f"REF-{uuid.uuid4().hex[:8].upper()}"
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        with conn:
            conn.execute(
                "INSERT INTO mock_refunds (refund_id, order_id, amount, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (refund_id, validated.order_id, validated.amount, "COMPLETED", now_str)
            )
        
        return {
            "status": "REFUND_ISSUED",
            "refund_id": refund_id,
            "order_id": validated.order_id,
            "amount": validated.amount,
            "created_at": now_str,
        }
    finally:
        conn.close()


def read_document(doc_id: str) -> Dict[str, Any]:
    """Read a synthetic document from mock knowledge base."""
    validated = ReadDocumentInput(doc_id=doc_id)
    if validated.doc_id not in DOCUMENTS:
        raise ValueError(f"Document not found: {validated.doc_id}. Available: {list(DOCUMENTS.keys())}")
    
    doc = DOCUMENTS[validated.doc_id]
    return {
        "doc_id": validated.doc_id,
        "title": doc["title"],
        "content": doc["content"],
    }
