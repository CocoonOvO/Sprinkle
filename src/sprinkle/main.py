"""FastAPI application entry point."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI

from sprinkle.config import settings
from sprinkle.api import api_router, websocket_router


def setup_logging() -> None:
    """Configure logging with rotating file handler."""
    # Get log config
    log_level = getattr(logging, settings.logging.level.upper(), logging.INFO)
    log_file = settings.logging.file
    max_bytes = settings.logging.max_bytes
    backup_count = settings.logging.backup_count
    
    # Create logs directory if not exists
    log_path = Path(log_file)
    if log_path.parent != log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create rotating file handler
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    
    # Set format
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(formatter)
    
    # Add to root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.addHandler(file_handler)
    
    # Also output to stderr for uvicorn
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(log_level)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)
    
    root_logger.info(f"Logging configured: level={settings.logging.level}, file={log_file}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup: initialize WebSocket handler
    from sprinkle.api.websocket import get_ws_handler
    handler = get_ws_handler()
    await handler.start()
    
    yield
    
    # Shutdown: stop WebSocket handler
    await handler.stop()


# Setup logging on module load
setup_logging()

app = FastAPI(
    title=settings.app.name,
    debug=settings.app.debug,
    lifespan=lifespan,
)

# Include API router
app.include_router(api_router)

# Include WebSocket router (WebSocket endpoints need direct app mounting)
app.include_router(websocket_router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"message": f"Welcome to {settings.app.name}", "status": "ok"}


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "sprinkle.main:app",
        host=settings.app.host,
        port=settings.app.port,
        reload=settings.app.debug,
    )
