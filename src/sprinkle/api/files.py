"""File API endpoints."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user

router = APIRouter()


# ============================================================================
# Request/Response Models
# ============================================================================

class FileResponse(BaseModel):
    """File response schema."""
    id: str
    file_name: str
    file_size: int
    mime_type: str
    conversation_id: Optional[str] = None
    uploader_id: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ============================================================================
# In-Memory File Store
# ============================================================================

class FileStore:
    """File data store."""
    def __init__(
        self,
        id: str,
        uploader_id: str,
        file_name: str,
        file_path: str,
        file_size: int,
        mime_type: str,
        conversation_id: Optional[str] = None,
        created_at: datetime = None,
        deleted_at: Optional[datetime] = None,
    ):
        self.id = id
        self.uploader_id = uploader_id
        self.file_name = file_name
        self.file_path = file_path
        self.file_size = file_size
        self.mime_type = mime_type
        self.conversation_id = conversation_id
        self.created_at = created_at or datetime.now(timezone.utc)
        self.deleted_at = deleted_at


# Store
_files: Dict[str, FileStore] = {}

# File storage directory
STORAGE_DIR = Path("./data/files")
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


def get_file_store() -> Dict[str, FileStore]:
    """Get files store."""
    return _files


def clear_file_store() -> None:
    """Clear all file data (for testing)."""
    _files.clear()


# ============================================================================
# Helper Functions
# ============================================================================

def get_file_or_404(file_id: str) -> FileStore:
    """Get file by ID or raise 404."""
    if file_id not in _files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    file = _files[file_id]
    if file.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return file


def guess_mime_type(file_name: str) -> str:
    """Guess MIME type from file name."""
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    
    mime_types = {
        "txt": "text/plain",
        "html": "text/html",
        "css": "text/css",
        "js": "application/javascript",
        "json": "application/json",
        "xml": "application/xml",
        "pdf": "application/pdf",
        "zip": "application/zip",
        "tar": "application/x-tar",
        "gz": "application/gzip",
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "bmp": "image/bmp",
        "webp": "image/webp",
        "svg": "image/svg+xml",
        "ico": "image/x-icon",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "mp4": "video/mp4",
        "webm": "video/webm",
        "avi": "video/x-msvideo",
    }
    
    return mime_types.get(ext, "application/octet-stream")


# ============================================================================
# API Endpoints
# ============================================================================

@router.post(
    "/upload",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload file",
)
async def upload_file(
    file: UploadFile = File(...),
    conversation_id: Optional[str] = None,
    current_user: UserCredentials = Depends(get_current_user),
) -> FileResponse:
    """Upload a file.
    
    - **file**: File to upload (multipart/form-data)
    - **conversation_id**: Optional conversation ID to associate with
    """
    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Generate file ID and path
    file_id = str(uuid4())
    file_ext = Path(file.filename).suffix if file.filename else ""
    stored_filename = f"{file_id}{file_ext}"
    file_path = STORAGE_DIR / stored_filename
    
    # Write file to disk
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Guess MIME type
    mime_type = guess_mime_type(file.filename or stored_filename)
    
    # Create file record
    now = datetime.now(timezone.utc)
    file_record = FileStore(
        id=file_id,
        uploader_id=current_user.user_id,
        file_name=file.filename or stored_filename,
        file_path=str(file_path),
        file_size=file_size,
        mime_type=mime_type,
        conversation_id=conversation_id,
        created_at=now,
    )
    _files[file_id] = file_record
    
    return FileResponse(
        id=file_record.id,
        file_name=file_record.file_name,
        file_size=file_record.file_size,
        mime_type=file_record.mime_type,
        conversation_id=file_record.conversation_id,
        uploader_id=file_record.uploader_id,
        created_at=file_record.created_at,
    )


@router.get(
    "/{file_id}",
    summary="Download file",
)
async def download_file(
    file_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> StreamingResponse:
    """Download a file.
    
    - **file_id**: File UUID
    """
    # Get file record
    file_record = get_file_or_404(file_id)
    
    # Check if file exists on disk
    file_path = Path(file_record.file_path)
    if not file_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found on server",
        )
    
    # Read file content
    with open(file_path, "rb") as f:
        content = f.read()
    
    # Return as streaming response
    return StreamingResponse(
        iter([content]),
        media_type=file_record.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{file_record.file_name}"',
            "Content-Length": str(file_record.file_size),
        },
    )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete file",
)
async def delete_file(
    file_id: str,
    current_user: UserCredentials = Depends(get_current_user),
) -> None:
    """Delete a file (soft delete).
    
    Only the uploader can delete a file.
    
    - **file_id**: File UUID
    """
    # Get file record
    file_record = get_file_or_404(file_id)
    
    # Check if user is the uploader
    if file_record.uploader_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own files",
        )
    
    # Soft delete
    file_record.deleted_at = datetime.now(timezone.utc)
    
    # Optionally delete physical file (async in real implementation)
    # For now, we keep the file on disk
