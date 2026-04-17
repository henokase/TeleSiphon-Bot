"""
TeleSiphon Web & Lifecycle Entry Point
--------------------------------------
This module serves as the main execution entry point. it initializes the 
FastAPI web server for health monitoring (Render/Uptime) and manages the 
asynchronous lifecycle of the Telethon UserBot client.
"""

import os
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from bot_client import client
import main  # Ensures event handlers are registered on import

# --- Performance Optimization ---
# Use uvloop for high-performance event loop management on Unix-based systems.
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except (ImportError, Exception):
    # Fallback to default asyncio loop if uvloop is unavailable or incompatible.
    pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the startup and shutdown sequence of the application services.
    """
    # Startup Sequence
    print("INFO: Initializing Telethon persistent session...")
    await client.start()
    
    bot_info = await client.get_me()
    print(f"INFO: Authentication successful. Active as {bot_info.first_name} (@{bot_info.username})")
    
    yield
    
    # Shutdown Sequence
    print("INFO: Gracefully disconnecting Telethon session...")
    await client.disconnect()
    print("INFO: Shutdown complete.")

app = FastAPI(
    title="TeleSiphon API",
    description="Uptime monitoring service for TeleSiphon UserBot",
    lifespan=lifespan
)

@app.get("/")
@app.get("/health")
async def health_check():
    """Endpoint for external uptime monitoring and health validation."""
    return {
        "status": "online", 
        "service": "TeleSiphon",
        "version": "1.2.0"
    }

if __name__ == "__main__":
    import uvicorn
    # PORT is typically assigned dynamically by cloud providers like Render.
    bind_port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=bind_port)
