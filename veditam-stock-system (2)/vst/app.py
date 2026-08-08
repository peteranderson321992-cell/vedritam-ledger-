# app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import os

from api import router
import database
import modules
import procurement
import messaging
import security as security_mod
from config import APP_NAME, VERSION, ALLOWED_STATIC_FILES, BASE_DIR, DATA_DIR

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting Vedritam School Stock Ledger Management System...")
    database.init_db()
    modules.init_stores()
    procurement.init_stores()
    messaging.init_stores()
    security_mod.init_stores()
    yield


app = FastAPI(title=APP_NAME, version=VERSION, lifespan=lifespan)

# Enable CORS for frontend interoperability
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register the API router.
app.include_router(router)

# --- Static frontend serving ---
# StaticFiles(directory=".") is not used: it would expose the CSV data files
# for direct download. Each frontend asset is served through an explicit route.

NO_CACHE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@app.get("/")
def serve_root():
    """Serves the login gateway by default."""
    return FileResponse(os.path.join(BASE_DIR, "index.html"), headers=NO_CACHE)

@app.get("/favicon.ico")
def serve_favicon():
    """Serves the logo as the browser tab icon (avoids 404 noise)."""
    return FileResponse(os.path.join(BASE_DIR, "logo.png"), headers=NO_CACHE)

@app.get("/{filename}")
def serve_static(filename: str):
    """
    Strictly serves only predefined safe frontend files.
    Rejects any request for .csv, .py, or unknown files.
    """
    if filename in ALLOWED_STATIC_FILES:
        filepath = os.path.join(BASE_DIR, filename)
        if not os.path.exists(filepath):
            # Data files (e.g. catalog.csv) live inside the data folder.
            filepath = os.path.join(DATA_DIR, filename)
        if os.path.exists(filepath):
            # Frontend files are never cached so updates always load fresh.
            return FileResponse(filepath, headers=NO_CACHE)
    
    # Fallback/Security rejection
    raise HTTPException(status_code=404, detail="File not found or access denied.")

# --- Entry Point ---
if __name__ == "__main__":
    import uvicorn
    # Run the server on port 8000
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
