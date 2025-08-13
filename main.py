# main.py
import logging
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, status

import config
from background_service import ScrapingService

# --- Service Setup ---
app_settings = config.Settings()
scraping_service = ScrapingService(app_settings)
LOCK_FILE = "/tmp/scraper_leader.lock"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's lifecycle with a leader election system
    to ensure only one worker runs the background scraping service.
    """
    worker_pid = os.getpid()
    is_leader = False

    # --- Leader Election using a file lock ---
    # This is an atomic operation: it will only succeed if the file does not exist.
    try:
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(lock_fd, str(worker_pid).encode())
        os.close(lock_fd)
        is_leader = True
        logging.warning(f"WORKER {worker_pid}: Acquired lock. Promoting to LEADER.")
    except FileExistsError:
        logging.warning(f"WORKER {worker_pid}: Lock file exists. Running as FOLLOWER.")
        is_leader = False

    # --- Start/Stop logic for LEADER only ---
    if is_leader:
        logging.info("LEADER: Starting background service...")
        await scraping_service.start()
    else:
        logging.info("FOLLOWER: Skipping background service startup.")

    yield  # Application runs here

    if is_leader:
        logging.info("LEADER: Shutting down background service...")
        await scraping_service.stop()
        try:
            os.remove(LOCK_FILE)
            logging.info(f"LEADER {worker_pid}: Lock file removed successfully.")
        except OSError as e:
            logging.error(f"LEADER {worker_pid}: Failed to remove lock file: {e}")
    else:
        logging.info(f"FOLLOWER {worker_pid}: Shutting down normally.")


app = FastAPI(title="Live Tennis Score API", lifespan=lifespan)


@app.get("/all_live_itf_data", status_code=status.HTTP_200_OK)
async def get_all_live_itf_data():
    """Returns all live match data from the service's in-memory cache."""
    cache = scraping_service.live_data_cache
    last_updated = cache["last_updated"]

    if not last_updated or not cache["data"]:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cache is currently empty. The service may be initializing. Please try again in a moment."
        )

    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()

    return {
        "cache_last_updated_utc": last_updated.isoformat(),
        "cache_age_seconds": round(age_seconds),
        "match_count": len(cache["data"]),
        "matches": list(cache["data"].values())
    }


@app.get("/match/{match_id}", status_code=status.HTTP_200_OK)
async def get_match_data(match_id: str):
    """Returns data for a specific match from the service's cache."""
    match_data = scraping_service.live_data_cache["data"].get(match_id)

    if not match_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Data for match ID '{match_id}' not found in the live cache."
        )

    return match_data


@app.get("/investigate/{match_id}", status_code=status.HTTP_200_OK)
async def investigate_match(match_id: str):
    """
    A temporary debugging endpoint to find new data sources for a given match.
    """
    # This endpoint will only work if called on the leader process, which is fine for debugging.
    if not scraping_service.main_scraper:
        raise HTTPException(status_code=503, detail="Scraping service not active on this worker (it's a follower).")

    logging.info(f"Received investigation request for match ID: {match_id}")
    urls = await asyncio.get_event_loop().run_in_executor(None, lambda: scraping_service.main_scraper.investigate_data_sources(match_id))

    return {
        "message": "Investigation complete. Check logs for captured URLs.",
        "match_id": match_id,
        "urls_found": len(urls)
    }