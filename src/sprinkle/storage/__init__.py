"""Storage module - database and file storage."""

__version__ = "0.1.0"

from sprinkle.storage.database import (
    Base,
    get_async_engine,
    get_async_session,
    get_sync_engine,
    init_db,
    close_db,
)
from sprinkle.storage.layered import (
    LayeredStorageService,
    StorageMigrationTask,
    MigrationResult,
    MessageRecord,
    ConversationRecord,
    MemberRecord,
    create_layered_storage,
)

__all__ = [
    # Database
    "Base",
    "get_async_engine",
    "get_async_session",
    "get_sync_engine",
    "init_db",
    "close_db",
    # Layered Storage (Phase 6)
    "LayeredStorageService",
    "StorageMigrationTask",
    "MigrationResult",
    "MessageRecord",
    "ConversationRecord",
    "MemberRecord",
    "create_layered_storage",
]