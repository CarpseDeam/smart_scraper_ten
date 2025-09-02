# main.py
import logging
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, status

import config
from background_service import ScrapingService

# --- Service Setup ---
app_settings = config.Settings()
scraping_service = ScrapingService(app_settings)

# --- Redis Client for Leader Election ---
redis_client = redis.from_url(app_settings.REDIS_URL, encoding="utf-8", decode_responses=True)


async def refresh_lock(lock_key: str, worker_id: str, ttl: int):
    """Periodically refresh the TTL of the leader lock."""
    while True:
        await asyncio.sleep(ttl / 2)  # Refresh halfway through the TTL
        try:
            # Check if we are still the leader before refreshing
            if await redis_client.get(lock_key) == worker_id:
                await redis_client.expire(lock_key, ttl)
                logging.info(f"WORKER {worker_id}: Refreshed leader lock.")
            else:
                logging.warning(f"WORKER {worker_id}: Lost leader lock. Stopping refresh task.")
                break
        except Exception as e:
            logging.error(f"WORKER {worker_id}: Failed to refresh leader lock: {e}")
            break


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages the application's lifecycle with a Redis-based leader election system
    to ensure only one worker runs the background scraping service.
    """
    worker_id = str(uuid.uuid4())
    is_leader = False
    lock_refresh_task = None

    logging.info(f"WORKER {worker_id}: Starting up and participating in leader election.")

    # --- Leader Election using Redis SETNX ---
    is_leader = await redis_client.set(
        app_settings.LEADER_LOCK_KEY,
        worker_id,
        nx=True,
        ex=app_settings.LEADER_LOCK_TTL_SECONDS
    )

    if is_leader:
        logging.warning(f"WORKER {worker_id}: Acquired lock. Promoting to LEADER.")
        # Start a background task to keep the lock alive
        lock_refresh_task = asyncio.create_task(
            refresh_lock(app_settings.LEADER_LOCK_KEY, worker_id, app_settings.LEADER_LOCK_TTL_SECONDS)
        )
    else:
        leader_id = await redis_client.get(app_settings.LEADER_LOCK_KEY)
        logging.warning(f"WORKER {worker_id}: Lock already held by {leader_id}. Running as FOLLOWER.")

    # --- Start/Stop logic for LEADER only ---
    if is_leader:
        logging.info("LEADER: Starting background service...")
        await scraping_service.start()
    else:
        logging.info("FOLLOWER: Skipping background service startup.")

    yield  # Application runs here

    if is_leader:
        logging.info("LEADER: Shutting down background service...")
        if lock_refresh_task:
            lock_refresh_task.cancel()
        await scraping_service.stop()
        try:
            # Safely release the lock if we are still the holder
            if await redis_client.get(app_settings.LEADER_LOCK_KEY) == worker_id:
                await redis_client.delete(app_settings.LEADER_LOCK_KEY)
                logging.info(f"LEADER {worker_id}: Lock released successfully.")
        except Exception as e:
            logging.error(f"LEADER {worker_id}: Failed to release lock: {e}")
    else:
        logging.info(f"FOLLOWER {worker_id}: Shutting down normally.")


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
