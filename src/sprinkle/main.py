"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from sprinkle.config import settings
from sprinkle.api import api_router, websocket_router


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
