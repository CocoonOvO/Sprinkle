"""Migration: Add avatar_id to users table."""

import sys
import asyncio
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sqlalchemy import text
from sprinkle.storage.database import get_async_engine


async def upgrade():
    """Add avatar_id column to users table."""
    engine = get_async_engine()
    async with engine.begin() as conn:
        # Check if column already exists
        result = await conn.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'users' AND column_name = 'avatar_id'
        """))
        if result.fetchone():
            print("✓ avatar_id column already exists, skipping")
            return
        
        # Add avatar_id column
        await conn.execute(text("""
            ALTER TABLE users 
            ADD COLUMN avatar_id VARCHAR(36) 
            REFERENCES files(id) 
            ON DELETE SET NULL
        """))
        print("✓ Added avatar_id column to users table")


async def downgrade():
    """Remove avatar_id column from users table."""
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE users DROP COLUMN avatar_id"))
        print("✓ Removed avatar_id column from users table")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "downgrade":
        asyncio.run(downgrade())
    else:
        asyncio.run(upgrade())