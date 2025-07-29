import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, status

import config
from smart_scraper import TenipoScraper
from data_mapper import transform_match_data_to_client_format

# --- Global State Management ---
app_container = {}
app_settings = config.Settings()
live_data_cache = {"data": {}, "last_updated": None}
CACHE_REFRESH_INTERVAL_SECONDS = 30


# --- Core Application Logic ---

async def poll_for_live_data():
    """
    The main engine of our application. It runs in the background for the
    entire lifespan of the app, continuously updating our data cache.
    """
    # Initialize our custom HTTP client for Tenipo.
    client = TenipoScraper(app_settings)
    app_container["client_instance"] = client

    # This loop runs forever until the application is shut down.
    while True:
        logging.info("BACKGROUND_POLL: Starting new polling cycle...")
        try:
            # 1. Await the async scraper method directly.
            all_matches_summary = await client.get_live_matches_summary()

            # 2. Filter for ITF matches.
            itf_matches_summary = [
                m for m in all_matches_summary
                if m and "ITF" in m.get("tournament_name", "")
            ]
            logging.info(f"Found {len(itf_matches_summary)} live ITF matches to process.")

            # 3. Build a new, fresh cache.
            new_cache_data = {}
            for match_summary in itf_matches_summary:
                match_id = match_summary.get("id")
                if not match_id: continue

                # Await the async scraper method directly.
                raw_data = await client.fetch_match_data(match_id)
                if raw_data:
                    formatted_data = transform_match_data_to_client_format(raw_data)
                    new_cache_data[match_id] = formatted_data

            # 4. Atomically replace the old cache with the new one.
            live_data_cache["data"] = new_cache_data
            live_data_cache["last_updated"] = datetime.now(timezone.utc)
            logging.info(f"BACKGROUND_POLL: Cache updated successfully with {len(new_cache_data)} matches.")

        except Exception as e:
            logging.error(f"BACKGROUND_POLL: An unexpected error occurred during polling cycle: {e}", exc_info=True)

        logging.info(f"BACKGROUND_POLL: Polling cycle complete. Waiting for {CACHE_REFRESH_INTERVAL_SECONDS} seconds.")
        await asyncio.sleep(CACHE_REFRESH_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles application startup and shutdown events."""
    logging.info("Application startup: Initializing resources...")
    polling_task = asyncio.create_task(poll_for_live_data())
    app_container["polling_task"] = polling_task
    yield
    logging.info("Application shutdown: Cleaning up resources...")
    if polling_task:
        polling_task.cancel()
    if client := app_container.get("client_instance"):
        await client.close()
    logging.info("Shutdown cleanup complete.")


# Initialize the FastAPI application.
app = FastAPI(title="Live Tennis Score API", lifespan=lifespan)


# --- API Endpoints ---

@app.get("/all_live_itf_data", status_code=status.HTTP_200_OK)
async def get_all_live_itf_data():
    last_updated = live_data_cache["last_updated"]
    if not last_updated or not live_data_cache["data"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cache is empty. Please try again.")
    age_seconds = (datetime.now(timezone.utc) - last_updated).total_seconds()
    return {
        "cache_last_updated_utc": last_updated.isoformat(),
        "cache_age_seconds": round(age_seconds),
        "match_count": len(live_data_cache["data"]),
        "matches": list(live_data_cache["data"].values())
    }


@app.get("/match/{match_id}", status_code=status.HTTP_200_OK)
async def get_match_data(match_id: str):
    match_data = live_data_cache["data"].get(match_id)
    if not match_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Match ID '{match_id}' not found.")
    return match_data