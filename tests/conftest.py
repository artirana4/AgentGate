"""
Pytest configuration and fixtures for AgentGate tests.
Provides isolated temporary SQLite databases for each test run.
"""

import os
import tempfile
import pytest
from backend_mock.tools import init_mock_db
from gateway.storage import init_db


@pytest.fixture
def temp_db(tmp_path):
    """Provides an isolated database path in a temporary directory."""
    db_file = str(tmp_path / "test_agentgate.db")
    init_mock_db(db_file)
    init_db(db_file)
    return db_file


@pytest.fixture
def temp_sandbox(tmp_path):
    """Provides an isolated sandbox directory."""
    sb = str(tmp_path / "sandbox")
    os.makedirs(sb, exist_ok=True)
    return sb
