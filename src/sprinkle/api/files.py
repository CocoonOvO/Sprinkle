"""File API endpoints - database-backed."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sprinkle.kernel.auth import UserCredentials
from sprinkle.api.dependencies import get_current_user, get_db_session
from sprinkle.models.file import File as FileModel, FileStatus
from sprinkle.models.message import Message

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
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AsyncUploadInitRequest(BaseModel):
    """Request to initialize async file upload."""
    file_name: str
    file_size: int
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None
    total_chunks: int = 1  # 默认1，兼容不分片场景


class AsyncUploadInitResponse(BaseModel):
    """Response after initializing async upload."""
    file_id: str
    status: str
    upload_url: str

class AsyncUploadContentRequest(BaseModel):
    """Request to upload file content for async upload."""
    pass  # Content sent as multipart form



class ChunkUploadResponse(BaseModel):
    """Response after uploading a chunk."""
    file_id: str
    chunk_index: int
    total_chunks: int
    received_chunks: list[int]
    progress_percent: float
    status: str  # continuing / completed


# ============================================================================
# Constants
# ============================================================================

STORAGE_DIR = Path("/home/cream/.openclaw/scone/Sprinkle/data/files")
STORAGE_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Helper Functions
# ============================================================================

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


async def get_file_or_404(db: AsyncSession, file_id: str) -> FileModel:
    """Get file by ID from database or raise 404."""
    result = await db.execute(
        select(FileModel).where(FileModel.id == file_id)
    )
    file_record = result.scalar_one_or_none()
    if file_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        )
    return file_record


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
    db: AsyncSession = Depends(get_db_session),
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
    
    # Create file record in database
    file_record = FileModel(
        id=file_id,
        uploader_id=current_user.user_id,
        file_name=file.filename or stored_filename,
        file_path=str(file_path),
        file_size=file_size,
        mime_type=mime_type,
        conversation_id=conversation_id,
    )
    db.add(file_record)
    await db.commit()
    await db.refresh(file_record)
    
    return FileResponse(
        id=file_record.id,
        file_name=file_record.file_name,
        file_size=file_record.file_size,
        mime_type=file_record.mime_type,
        conversation_id=file_record.conversation_id,
        uploader_id=file_record.uploader_id,
        status=FileStatus.success.value,
        created_at=file_record.created_at,
    )


@router.post(
    "/upload-async",
    response_model=AsyncUploadInitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Initialize async file upload",
)
async def init_async_upload(
    request: AsyncUploadInitRequest,
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> AsyncUploadInitResponse:
    """Initialize an async file upload.
    
    This creates a file record with status='uploading' and returns the file_id.
    Use POST /files/{file_id}/upload-content to upload the actual file content.
    
    - **file_name**: Name of the file
    - **file_size**: Size of the file in bytes
    - **conversation_id**: Optional conversation ID
    - **message_id**: Optional message ID to associate with
    """
    file_id = str(uuid4())
    
    # Create file record with uploading status
    file_record = FileModel(
        id=file_id,
        uploader_id=current_user.user_id,
        file_name=request.file_name,
        file_path="",  # Empty until upload completes
        file_size=request.file_size,
        mime_type=guess_mime_type(request.file_name),
        conversation_id=request.conversation_id,
        message_id=request.message_id,
        status=FileStatus.uploading,
        total_chunks=request.total_chunks,
        received_chunks=[],
    )
    db.add(file_record)
    
    # If message_id provided, update message metadata with file_id
    if request.message_id:
        from sqlalchemy import select
        result = await db.execute(
            select(Message).where(Message.id == request.message_id)
        )
        message = result.scalar_one_or_none()
        if message:
            import json
            metadata = message.message_metadata or {}
            file_ids = metadata.get("file_ids", [])
            file_ids.append(file_id)
            metadata["file_ids"] = file_ids
            message.message_metadata = metadata
    
    await db.commit()
    
    return AsyncUploadInitResponse(
        file_id=file_id,
        status=FileStatus.uploading.value,
        upload_url=f"/api/v1/files/{file_id}/upload-content",
    )


@router.post(
    "/{file_id}/upload-content",
    status_code=status.HTTP_200_OK,
    summary="Upload file content for async upload",
)
async def upload_async_content(
    file_id: str,
    file: UploadFile = File(...),
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Upload the actual file content for an async upload.
    
    - **file_id**: File ID from init_async_upload response
    - **file**: File content (multipart/form-data)
    """
    # Get file record
    file_record = await get_file_or_404(db, file_id)
    
    # Check ownership
    if file_record.uploader_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only upload your own files",
        )
    
    # Check status
    if file_record.status != FileStatus.uploading:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File is not in uploading status (current: {file_record.status})",
        )
    
    # Read and save file content
    content = await file.read()
    file_ext = Path(file_record.file_name).suffix or ""
    stored_filename = f"{file_id}{file_ext}"
    file_path = STORAGE_DIR / stored_filename
    
    with open(file_path, "wb") as f:
        f.write(content)
    
    # Update file record
    file_record.file_path = str(file_path)
    file_record.status = FileStatus.success
    file_record.mime_type = guess_mime_type(file_record.file_name)
    
    await db.commit()
    await db.refresh(file_record)
    
    return FileResponse(
        id=file_record.id,
        file_name=file_record.file_name,
        file_size=file_record.file_size,
        mime_type=file_record.mime_type,
        conversation_id=file_record.conversation_id,
        uploader_id=file_record.uploader_id,
        status=file_record.status.value,
        created_at=file_record.created_at,
    )


# ============================================================================
# Chunk Upload Helpers
# ============================================================================

async def merge_chunks(db: AsyncSession, file_record) -> None:
    """合并所有 chunks 到完整文件"""
    import json
    file_id = file_record.id
    file_ext = Path(file_record.file_name).suffix or ""
    chunks_dir = STORAGE_DIR / file_id
    final_path = STORAGE_DIR / f"{file_id}{file_ext}"
    
    # 按顺序合并所有 chunks
    received = sorted([int(x) for x in file_record.received_chunks])
    with open(final_path, "wb") as outf:
        for idx in received:
            chunk_path = chunks_dir / f"chunk_{idx}"
            if chunk_path.exists():
                with open(chunk_path, "rb") as inf:
                    outf.write(inf.read())
    
    # 清理 chunks 目录
    import shutil
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir)
    
    # 更新文件记录
    file_record.file_path = str(final_path)
    file_record.status = FileStatus.success
    
    # 推送完成事件
    from sprinkle.api.websocket import ConnectionManager
    if file_record.conversation_id:
        await ConnectionManager.broadcast_to_conversation(
            file_record.conversation_id,
            [],
            {
                "event": "file.upload.completed",
                "data": {
                    "file_id": file_id,
                    "message_id": file_record.message_id,
                    "file_name": file_record.file_name,
                    "file_size": file_record.file_size,
                    "mime_type": file_record.mime_type,
                }
            }
        )


async def push_chunk_progress(db: AsyncSession, file_record, chunk_index: int) -> None:
    """推送 chunk 上传进度到 WebSocket"""
    from sprinkle.api.websocket import ConnectionManager
    if file_record.conversation_id:
        received = sorted([int(x) for x in file_record.received_chunks])
        progress = len(received) / file_record.total_chunks * 100
        await ConnectionManager.broadcast_to_conversation(
            file_record.conversation_id,
            [],
            {
                "event": "file.upload.progress",
                "data": {
                    "file_id": file_record.id,
                    "message_id": file_record.message_id,
                    "chunk_index": chunk_index,
                    "total_chunks": file_record.total_chunks,
                    "received_chunks": received,
                    "progress_percent": round(progress, 1),
                    "status": "continuing" if len(received) < file_record.total_chunks else "completed",
                }
            }
        )


# ============================================================================
# Chunk Upload Endpoint
# ============================================================================

@router.post(
    "/{file_id}/chunks/{chunk_index}",
    response_model=ChunkUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Upload a single chunk",
)
async def upload_chunk(
    file_id: str,
    chunk_index: int,
    chunk: UploadFile = File(...),
    current_user: UserCredentials = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> ChunkUploadResponse:
    """Upload a single chunk of a file.
    
    - **file_id**: File ID from init_async_upload response
    - **chunk_index**: 0-based chunk index
    - **chunk**: Chunk content as multipart/form-data
    """
    # Get file record
    file_record = await get_file_or_404(db, file_id)
    
    # Check ownership
    if file_record.uploader_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only upload your own files",
        )
    
    # Check status
    if file_record.status != FileStatus.uploading:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File is not in uploading status (current: {file_record.status})",
        )
    
    # Check chunk index validity
    if chunk_index < 0 or chunk_index >= file_record.total_chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid chunk_index {chunk_index}, expected 0-{file_record.total_chunks - 1}",
        )
    
    # Check if chunk already received
    if str(chunk_index) in file_record.received_chunks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Chunk {chunk_index} already uploaded",
        )
    
    # Create chunks directory and save chunk
    chunks_dir = STORAGE_DIR / file_id
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunk_path = chunks_dir / f"chunk_{chunk_index}"
    
    content = await chunk.read()
    with open(chunk_path, "wb") as f:
        f.write(content)
    
    # Update received_chunks
    file_record.received_chunks = file_record.received_chunks + [str(chunk_index)]
    
    # Push progress via WebSocket
    await push_chunk_progress(db, file_record, chunk_index)
    
    # Check if all chunks received, then merge
    received = sorted([int(x) for x in file_record.received_chunks])
    if len(received) == file_record.total_chunks:
        await merge_chunks(db, file_record)
    
    await db.commit()
    await db.refresh(file_record)
    
    return ChunkUploadResponse(
        file_id=file_id,
        chunk_index=chunk_index,
        total_chunks=file_record.total_chunks,
        received_chunks=received,
        progress_percent=round(len(received) / file_record.total_chunks * 100, 1),
        status="completed" if len(received) == file_record.total_chunks else "continuing",
    )


@router.get(
    "/{file_id}/info",
    response_model=FileResponse,
    summary="Get file metadata",
)
async def get_file_info(
    file_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Get file metadata without downloading.
    
    - **file_id**: File UUID
    """
    file_record = await get_file_or_404(db, file_id)
    
    return FileResponse(
        id=file_record.id,
        file_name=file_record.file_name,
        file_size=file_record.file_size,
        mime_type=file_record.mime_type,
        conversation_id=file_record.conversation_id,
        uploader_id=file_record.uploader_id,
        status=file_record.status.value if hasattr(file_record.status, 'value') else file_record.status,
        created_at=file_record.created_at,
    )


@router.get(
    "/{file_id}",
    summary="Download file",
)
async def download_file(
    file_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Download a file.
    
    - **file_id**: File UUID
    """
    # Get file record from database
    file_record = await get_file_or_404(db, file_id)
    
    # Check if file exists on disk
    file_path = Path(file_record.file_path)
    if not file_path.is_absolute():
        # Handle relative paths - strip data/files/ prefix if present
        relative_part = file_path
        if relative_part.parts and relative_part.parts[0] in ("data", "data/files"):
            relative_part = Path(*relative_part.parts[2:] if relative_part.parts[0] == "data" and len(relative_part.parts) > 1 and relative_part.parts[1] == "files" else relative_part.parts[1:])
        file_path = STORAGE_DIR / relative_part
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
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a file (soft delete).
    
    Only the uploader can delete a file.
    
    - **file_id**: File UUID
    """
    # Get file record from database
    file_record = await get_file_or_404(db, file_id)
    
    # Check if user is the uploader
    if file_record.uploader_id != current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own files",
        )
    
    # Physical file deletion
    file_path = Path(file_record.file_path)
    if file_path.exists():
        file_path.unlink()
    
    # Delete database record
    await db.delete(file_record)
    await db.commit()


# ============================================================================
# Stub In-Memory Store (kept for backward compatibility with tests only)
# ============================================================================
# The API no longer uses these - all data goes through the database.
# Tests may still write to these stubs but the API will not read from them.

class FileStore:
    """File data store (stub for test compatibility)."""
    def __init__(
        self,
        id: str,
        file_name: str,
        file_size: int,
        mime_type: str,
        uploader_id: str,
        conversation_id: Optional[str] = None,
        file_path: Optional[str] = None,
        created_at: datetime = None,
    ):
        self.id = id
        self.file_name = file_name
        self.file_size = file_size
        self.mime_type = mime_type
        self.uploader_id = uploader_id
        self.conversation_id = conversation_id
        self.file_path = file_path
        self.created_at = created_at or datetime.now(timezone.utc)


# Stub stores (not used by API anymore, but tests may write to them)
_files: Dict[str, FileStore] = {}


def get_file_store() -> Dict[str, FileStore]:
    """Get files store (stub - not used by API, for test compatibility)."""
    return _files


def clear_file_store() -> None:
    """Clear all file records (for testing).

    Clears database tables.
    """
    from sprinkle.storage.database import SessionLocal
    db = SessionLocal()
    try:
        from sprinkle.models.file import File as FileModel
        db.query(FileModel).delete()
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
