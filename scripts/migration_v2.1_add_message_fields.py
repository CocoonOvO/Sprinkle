#!/usr/bin/env python3
"""
Migration script: v2.1_add_message_fields
Adds new fields to messages table and updates content_type CHECK constraint.

Run with: python scripts/migration_v2.1_add_message_fields.py
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
        print("Sprinkle v2.1 Migration: Adding message fields")
        print("=" * 60)
        
        # --- Step 1: Add message_metadata JSONB column ---
        print("\n[1/5] Adding message_metadata column (JSONB)...")
        try:
            conn.execute(text("""
                ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS message_metadata JSONB NOT NULL DEFAULT '{}'
            """))
            conn.commit()
            print("    ✓ message_metadata column added (or already exists)")
        except Exception as e:
            conn.rollback()
            # Column might already exist in some PostgreSQL versions
            if "already exists" in str(e):
                print("    ✓ message_metadata column already exists, skipping")
            else:
                print(f"    ✗ Error: {e}")
                raise
        
        # --- Step 2: Add edited_at column ---
        print("\n[2/5] Adding edited_at column (TIMESTAMPTZ)...")
        try:
            conn.execute(text("""
                ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS edited_at TIMESTAMPTZ
            """))
            conn.commit()
            print("    ✓ edited_at column added (or already exists)")
        except Exception as e:
            conn.rollback()
            if "already exists" in str(e):
                print("    ✓ edited_at column already exists, skipping")
            else:
                print(f"    ✗ Error: {e}")
                raise
        
        # --- Step 3: Add deleted_at column ---
        print("\n[3/5] Adding deleted_at column (TIMESTAMPTZ)...")
        try:
            conn.execute(text("""
                ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ
            """))
            conn.commit()
            print("    ✓ deleted_at column added (or already exists)")
        except Exception as e:
            conn.rollback()
            if "already exists" in str(e):
                print("    ✓ deleted_at column already exists, skipping")
            else:
                print(f"    ✗ Error: {e}")
                raise
        
        # --- Step 4: Add deleted_by column ---
        print("\n[4/5] Adding deleted_by column (FK -> users.id)...")
        try:
            conn.execute(text("""
                ALTER TABLE messages
                ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(36)
                    REFERENCES users(id) ON DELETE SET NULL
            """))
            conn.commit()
            print("    ✓ deleted_by column added (or already exists)")
        except Exception as e:
            conn.rollback()
            if "already exists" in str(e):
                print("    ✓ deleted_by column already exists, skipping")
            else:
                print(f"    ✗ Error: {e}")
                raise
        
        # --- Step 5: Update content_type CHECK constraint ---
        print("\n[5/5] Updating content_type CHECK constraint...")
        try:
            # Drop old constraint if exists
            conn.execute(text("""
                ALTER TABLE messages
                DROP CONSTRAINT IF EXISTS chk_content_type
            """))
            conn.commit()
            
            # Add new constraint with all content types
            conn.execute(text("""
                ALTER TABLE messages
                ADD CONSTRAINT chk_content_type
                    CHECK (content_type IN ('text', 'markdown', 'image', 'file', 'system'))
            """))
            conn.commit()
            print("    ✓ CHECK constraint updated to include image, file, system")
        except Exception as e:
            conn.rollback()
            print(f"    ✗ Error: {e}")
            raise
        
        print("\n" + "=" * 60)
        print("Migration completed successfully!")
        print("=" * 60)
        print("\nSummary of changes:")
        print("  - messages.message_metadata: JSONB, default={}, NOT NULL")
        print("  - messages.edited_at: TIMESTAMPTZ, nullable")
        print("  - messages.deleted_at: TIMESTAMPTZ, nullable")
        print("  - messages.deleted_by: VARCHAR(36), FK -> users.id, nullable")
        print("  - messages.content_type: CHECK updated to include image/file/system")
        print()


if __name__ == "__main__":
    run_migration()
