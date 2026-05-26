"""File model for managing uploaded files."""

from sqlalchemy import Column, String, DateTime, ForeignKey, BigInteger, Integer, ARRAY
from datetime import datetime, timezone
from . import Base
import enum


def utc_now():
    """Return current UTC time with timezone info."""
    return datetime.now(timezone.utc)


class FileStatus(str, enum.Enum):
    """File upload status."""
    uploading = "uploading"
    success = "success"
    failed = "failed"


class File(Base):
    """File model representing a file uploaded to the system."""
    
    __tablename__ = "files"
    
    id = Column(String(36), primary_key=True)  # UUID
    uploader_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=True)
    message_id = Column(String(36), nullable=True)  # Associated message ID
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=True)  # Nullable until upload completes
    file_size = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=True)
    status = Column(String(20), default=FileStatus.success.value, nullable=False)  # Store as string: uploading/success/failed
    error_message = Column(String(500), nullable=True)
    # 分片上传相关字段
    total_chunks = Column(Integer, default=1, nullable=False)  # 总 chunk 数，默认1（不分片）
    received_chunks = Column(ARRAY(String), default=[], nullable=False)  # 已接收的 chunks 索引列表
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)