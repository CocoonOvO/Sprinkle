"""File model for managing uploaded files."""

from sqlalchemy import Column, String, DateTime, ForeignKey, BigInteger
from datetime import datetime
from . import Base


class File(Base):
    """File model representing a file uploaded to the system."""
    
    __tablename__ = "files"
    
    id = Column(String(36), primary_key=True)  # UUID
    uploader_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    mime_type = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
