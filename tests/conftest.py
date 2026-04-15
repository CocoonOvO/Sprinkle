"""Pytest configuration and fixtures for Sprinkle tests."""

from __future__ import annotations

import os
import pytest


# ============================================================================
# Test Environment Setup
# ============================================================================

def pytest_configure(config):
    """Set test environment variables before any tests or modules are loaded."""
    os.environ["SPRINKLE_TEST"] = "1"


def pytest_unconfigure(config):
    """Clean up environment after all tests complete."""
    os.environ.pop("SPRINKLE_TEST", None)


# ============================================================================
# Engine Cleanup Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def dispose_engines():
    """Dispose SQLAlchemy engines after each test.
    
    With SPRINKLE_TEST=1 and NullPool, the async engine doesn't pool connections,
    so event loop issues are avoided. We still dispose the sync engine.
    """
    yield
    
    # Dispose sync engine
    try:
        from sprinkle.storage.database import get_sync_engine
        sync_engine = get_sync_engine()
        sync_engine.dispose()
    except Exception:
        pass
