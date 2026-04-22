#!/usr/bin/env python3
"""Initialize Sprinkle database schema."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sprinkle.storage.database import get_sync_engine

def init_db():
    engine = get_sync_engine()
    conn = engine.connect()
    
    print("Creating Sprinkle tables...")
    
    # Users table
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR(36) PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            password_hash VARCHAR(255),
            display_name VARCHAR(100),
            user_type VARCHAR(20) NOT NULL DEFAULT 'human',
            extra_data JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    print("  ✓ users table")
    
    # Conversations table
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS conversations (
            id VARCHAR(36) PRIMARY KEY,
            type VARCHAR(20) NOT NULL DEFAULT 'group',
            name VARCHAR(100),
            owner_id VARCHAR(36) NOT NULL REFERENCES users(id),
            extra_data JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """))
    print("  ✓ conversations table")
    
    # Conversation members table (composite PK)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS conversation_members (
            conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id),
            user_id VARCHAR(36) NOT NULL REFERENCES users(id),
            role VARCHAR(20) NOT NULL DEFAULT 'member',
            nickname VARCHAR(100),
            invited_by VARCHAR(36) REFERENCES users(id),
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            left_at TIMESTAMPTZ,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            PRIMARY KEY (conversation_id, user_id)
        )
    """))
    print("  ✓ conversation_members table")
    
    # Messages table
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS messages (
            id VARCHAR(36) PRIMARY KEY,
            conversation_id VARCHAR(36) NOT NULL REFERENCES conversations(id),
            sender_id VARCHAR(36) NOT NULL REFERENCES users(id),
            content TEXT NOT NULL,
            content_type VARCHAR(20) NOT NULL DEFAULT 'text',
            reply_to_id VARCHAR(36) REFERENCES messages(id),
            message_metadata JSONB DEFAULT '{}',
            is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ,
            edited_at TIMESTAMPTZ,
            deleted_at TIMESTAMPTZ,
            deleted_by VARCHAR(36) REFERENCES users(id)
        )
    """))
    print("  ✓ messages table")
    
    # Files table
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
    print("  ✓ files table")
    
    conn.commit()
    conn.close()
    print("\n✅ Database initialized successfully!")

if __name__ == "__main__":
    init_db()
