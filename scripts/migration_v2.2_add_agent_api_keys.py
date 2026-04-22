#!/usr/bin/env python3
"""
Migration v2.2: Add agent_api_keys table for persistent authentication.

This migration adds support for API Key authentication for agents,
enabling long-lived WebSocket connections without token expiration.

Run: python scripts/migration_v2.2_add_agent_api_keys.py
"""

import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from sqlalchemy import text
from sprinkle.storage.database import get_sync_engine


def get_engine():
    """Get sync engine."""
    return get_sync_engine()


def upgrade():
    """Create agent_api_keys table."""
    engine = get_engine()
    with engine.connect() as conn:
        # Create agent_api_keys table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS agent_api_keys (
                id VARCHAR(36) PRIMARY KEY,
                user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name VARCHAR(100) NOT NULL,
                secret_hash VARCHAR(255) NOT NULL,
                description VARCHAR(255),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_used_at TIMESTAMP,
                last_used_ip VARCHAR(45),
                is_active BOOLEAN NOT NULL DEFAULT TRUE
            )
        """))

        # Create index for faster lookups
        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_agent_api_keys_user_id
            ON agent_api_keys(user_id)
        """))

        conn.execute(text("""
            CREATE INDEX IF NOT EXISTS ix_agent_api_keys_is_active
            ON agent_api_keys(is_active)
        """))

        conn.commit()
        print("✓ Created agent_api_keys table")
        print("✓ Created indexes")


def downgrade():
    """Drop agent_api_keys table."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS agent_api_keys CASCADE"))
        conn.commit()
        print("✓ Dropped agent_api_keys table")


def check_table_exists() -> bool:
    """Check if agent_api_keys table exists."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'agent_api_keys'
            )
        """))
        return result.scalar()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migration v2.2: Add agent_api_keys table")
    parser.add_argument("--downgrade", action="store_true", help="Run downgrade instead of upgrade")
    args = parser.parse_args()

    print("=" * 60)
    print("Migration v2.2: Add agent_api_keys table")
    print("=" * 60)

    if args.downgrade:
        print("\nRunning DOWNGRADE...")
        downgrade()
        print("\n✓ Migration complete")
    else:
        if check_table_exists():
            print("\n⚠ agent_api_keys table already exists, skipping...")
        else:
            print("\nRunning UPGRADE...")
            upgrade()
            print("\n✓ Migration complete")
