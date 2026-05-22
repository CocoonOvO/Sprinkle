#!/usr/bin/env python3
"""
Migration v2.3: Add avatar_url column to users table.

This migration adds the avatar_url field to support user avatars.

Run: python scripts/migration_v2.3_add_avatar_url.py
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
    """Add avatar_url column to users table."""
    engine = get_engine()
    with engine.connect() as conn:
        # Add avatar_url column (default empty string, allowing NULL)
        conn.execute(text("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(500) DEFAULT '' NOT NULL
        """))
        conn.commit()
        print("+ Added avatar_url column to users table")


def downgrade():
    """Remove avatar_url column from users table."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE users DROP COLUMN IF EXISTS avatar_url
        """))
        conn.commit()
        print("+ Dropped avatar_url column from users table")


def check_column_exists() -> bool:
    """Check if avatar_url column exists."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.columns
                WHERE table_name = 'users'
                AND column_name = 'avatar_url'
            )
        """))
        return result.scalar()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migration v2.3: Add avatar_url column")
    parser.add_argument("--downgrade", action="store_true", help="Run downgrade instead of upgrade")
    args = parser.parse_args()

    print("=" * 60)
    print("Migration v2.3: Add avatar_url column to users")
    print("=" * 60)

    if args.downgrade:
        print("\nRunning DOWNGRADE...")
        downgrade()
        print("\n+ Migration complete")
    else:
        if check_column_exists():
            print("\n+ avatar_url column already exists, skipping...")
        else:
            print("\nRunning UPGRADE...")
            upgrade()
            print("\n+ Migration complete")