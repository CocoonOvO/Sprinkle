"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI

from sprinkle.config import settings
from sprinkle.api import api_router

app = FastAPI(
    title=settings.app.name,
    debug=settings.app.debug,
)

# Include API router
app.include_router(api_router)


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
