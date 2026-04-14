import os
import asyncio
from fastapi import FastAPI
from contextlib import asynccontextmanager
from bot_client import client
import main  # This will register the event handlers

# Apply uvloop for better performance on Linux/Render
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass
except Exception:
    pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("Starting Telethon client...")
    await client.start()
    me = await client.get_me()
    print(f"Logged in as {me.first_name} (@{me.username})")
    
    # Run the client until disconnected in the background
    # We don't use run_until_disconnected because we are in an async loop with FastAPI
    yield
    
    # Shutdown
    print("Disconnecting Telethon client...")
    await client.disconnect()

app = FastAPI(lifespan=lifespan)

@app.get("/")
@app.get("/health")
async def health_check():
    return {"status": "online", "bot": "TeleSiphon"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
