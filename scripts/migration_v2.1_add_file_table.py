#!/usr/bin/env python3
"""
Migration script: v2.1_add_file_table
Creates the files table for file metadata persistence.

Run with: python scripts/migration_v2.1_add_file_table.py
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from sprinkle.storage.database import get_sync_engine


def run_migration():
    engine = get_sync_engine()
    
    with engine.connect() as conn:
        print("=" * 60)
        print("Sprinkle v2.1 Migration: Creating files table")
        print("=" * 60)
        
        # --- Step 1: Create files table ---
        print("\n[1/3] Creating files table...")
        try:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS files (
                    id VARCHAR(36) PRIMARY KEY,
                    uploader_id VARCHAR(36) NOT NULL REFERENCES users(id),
                    conversation_id VARCHAR(36) REFERENCES conversations(id),
                    file_name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(500) NOT NULL,
                    file_size BIGINT NOT NULL,
                    mime_type VARCHAR(100),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            conn.commit()
            print("    ✓ files table created (or already exists)")
        except Exception as e:
            conn.rollback()
            print(f"    ✗ Error: {e}")
            raise
        
        # --- Step 2: Add conversation_members fields (nickname, left_at, is_active) ---
        print("\n[2/3] Adding nickname column to conversation_members...")
        try:
            conn.execute(text("""
                ALTER TABLE conversation_members
                ADD COLUMN IF NOT EXISTS nickname VARCHAR(100)
            """))
            conn.commit()
            print("    ✓ nickname column added (or already exists)")
        except Exception as e:
            conn.rollback()
            if "already exists" in str(e):
                print("    ✓ nickname column already exists, skipping")
            else:
                print(f"    ✗ Error: {e}")
                raise
        
        print("\n[2/3b] Adding left_at column to conversation_members...")
        try:
            conn.execute(text("""
                ALTER TABLE conversation_members
                ADD COLUMN IF NOT EXISTS left_at TIMESTAMPTZ
            """))
            conn.commit()
            print("    ✓ left_at column added (or already exists)")
        except Exception as e:
            conn.rollback()
            if "already exists" in str(e):
                print("    ✓ left_at column already exists, skipping")
            else:
                print(f"    ✗ Error: {e}")
                raise
        
        print("\n[2/3c] Adding is_active column to conversation_members...")
        try:
            conn.execute(text("""
                ALTER TABLE conversation_members
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE
            """))
            conn.commit()
            print("    ✓ is_active column added (or already exists)")
        except Exception as e:
            conn.rollback()
            if "already exists" in str(e):
                print("    ✓ is_active column already exists, skipping")
            else:
                print(f"    ✗ Error: {e}")
                raise
        
        # --- Step 3: Update conversation_members role CHECK constraint ---
        print("\n[3/3] Updating conversation_members role CHECK constraint...")
        try:
            # Drop old constraint if exists
            conn.execute(text("""
                ALTER TABLE conversation_members
                DROP CONSTRAINT IF EXISTS chk_member_role
            """))
            conn.commit()
            
            # Add new constraint
            conn.execute(text("""
                ALTER TABLE conversation_members
                ADD CONSTRAINT chk_member_role
                    CHECK (role IN ('owner', 'admin', 'member', 'agent'))
            """))
            conn.commit()
            print("    ✓ CHECK constraint updated to include agent role")
        except Exception as e:
            conn.rollback()
            print(f"    ✗ Error: {e}")
            raise
        
        print("\n" + "=" * 60)
        print("Migration completed successfully!")
        print("=" * 60)
        print("\nSummary of changes:")
        print("  - files table: created (id, uploader_id, conversation_id, file_name, file_path, file_size, mime_type, created_at)")
        print("  - conversation_members.nickname: VARCHAR(100), nullable")
        print("  - conversation_members.left_at: TIMESTAMPTZ, nullable")
        print("  - conversation_members.is_active: BOOLEAN, default TRUE, NOT NULL")
        print("  - conversation_members.role: CHECK updated to include 'agent'")
        print()


if __name__ == "__main__":
    run_migration()
