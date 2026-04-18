#!/usr/bin/env python3
"""
Migration script: v2.1_fix_conversation_members_pk
Changes conversation_members primary key from (id) to (conversation_id, user_id).

Run with: python scripts/migration_v2.1_fix_conversation_members_pk.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from sprinkle.storage.database import get_sync_engine


def run_migration():
    engine = get_sync_engine()
    
    with engine.connect() as conn:
        print("=" * 60)
        print("Sprinkle v2.1 Migration: Fixing conversation_members PK")
        print("=" * 60)
        
        # Check if there are any data
        result = conn.execute(text("SELECT COUNT(*) FROM conversation_members"))
        count = result.scalar()
        print(f"\nCurrent row count: {count}")
        
        if count > 0:
            print("\n⚠️  Warning: Table has data! Aborting to prevent data loss.")
            print("Please manually migrate your data if needed.")
            return
        
        # Step 1: Drop existing primary key
        print("\n[1/5] Dropping existing primary key on 'id'...")
        try:
            conn.execute(text("""
                ALTER TABLE conversation_members
                DROP CONSTRAINT IF EXISTS conversation_members_pkey
            """))
            conn.commit()
            print("    ✓ Old primary key dropped")
        except Exception as e:
            conn.rollback()
            print(f"    ✗ Error: {e}")
            raise
        
        # Step 2: Drop the 'id' column
        print("\n[2/5] Dropping 'id' column...")
        try:
            conn.execute(text("""
                ALTER TABLE conversation_members
                DROP COLUMN IF EXISTS id
            """))
            conn.commit()
            print("    ✓ 'id' column dropped")
        except Exception as e:
            conn.rollback()
            print(f"    ✗ Error: {e}")
            raise
        
        # Step 3: Add composite primary key
        print("\n[3/5] Adding composite primary key (conversation_id, user_id)...")
        try:
            conn.execute(text("""
                ALTER TABLE conversation_members
                ADD CONSTRAINT conversation_members_pkey
                PRIMARY KEY (conversation_id, user_id)
            """))
            conn.commit()
            print("    ✓ Composite primary key added")
        except Exception as e:
            conn.rollback()
            print(f"    ✗ Error: {e}")
            raise
        
        # Step 4: Ensure required columns are NOT NULL
        print("\n[4/5] Setting NOT NULL constraints...")
        try:
            conn.execute(text("""
                ALTER TABLE conversation_members
                ALTER COLUMN conversation_id SET NOT NULL,
                ALTER COLUMN user_id SET NOT NULL
            """))
            conn.commit()
            print("    ✓ NOT NULL constraints set")
        except Exception as e:
            conn.rollback()
            # Some might already be set, ignore
            print(f"    ⚠️  Warning: {e}")
        
        # Step 5: Verify
        print("\n[5/5] Verifying schema...")
        result = conn.execute(text("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'conversation_members'
            ORDER BY ordinal_position
        """))
        columns = result.fetchall()
        print("    Columns:")
        for col in columns:
            nullable = "NULL" if col[2] == 'YES' else "NOT NULL"
            print(f"      - {col[0]}: {col[1]} ({nullable})")
        
        print("\n" + "=" * 60)
        print("Migration completed successfully!")
        print("=" * 60)


if __name__ == "__main__":
    run_migration()
