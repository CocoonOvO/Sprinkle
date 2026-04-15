"""User model for Sprinkle."""

from sqlalchemy import Column, String, DateTime, Text, Boolean, Enum as SQLEnum
from datetime import datetime
from . import Base
import enum


class UserType(str, enum.Enum):
    human = "human"
    agent = "agent"


class User(Base):
    __tablename__ = "users"
    
    id = Column(String(36), primary_key=True)  # UUID
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False, default="")
    display_name = Column(String(100), nullable=False)
    user_type = Column(SQLEnum(UserType), default=UserType.human, nullable=False)
    extra_data = Column(Text, default="{}", nullable=False)  # JSON stored as text
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
